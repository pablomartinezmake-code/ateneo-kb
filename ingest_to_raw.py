"""
Hybrid file → Markdown ingest for Ateneo-KB.

Default behaviour:
- PDF -> pdf_pages: keeps the page boundary as an explicit `<!-- pdf_page: NNN -->`
  anchor. This is the default because a flat conversion loses the page silently,
  and the printed page can only be recovered later by applying a per-document
  offset -- which needs an anchor to be applied to.
- Markdown / text -> plain passthrough
- Other supported office / web formats -> MarkItDown (if installed)

Examples:
  python ingest_to_raw.py path/to/file.pdf --out 01_raw/papers
  python ingest_to_raw.py path/to/file.docx
  python ingest_to_raw.py notes.txt --copy-original
  python ingest_to_raw.py book.pdf --out 01_raw/books --converter markitdown
  python ingest_to_raw.py slides.pptx --out 01_raw/imported --write-meta
"""

from __future__ import annotations

import argparse
import inspect
import json
import posixpath
import re
import shutil
import sys
import subprocess
import os
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, UTC
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

# Intérprete con pymupdf4llm, markitdown y pymupdf. Si no es el que corre el guion, dilo en ATENEO_PYTHON_PDF.
INTERPRETE_CON_LECTURA_PDF = os.environ.get(
    "ATENEO_PYTHON_PDF",
    "un intérprete con pymupdf4llm, markitdown y pymupdf (pip install -r requirements.txt)",
)


# Corte del maquetador: guion blando entre dos letras, pegado a la segunda o con un SALTO de línea en medio.
# Soft hyphens followed by horizontal space alone do not establish a word break.
_GUION_BLANDO_PARTE = re.compile(r"(?<=[^\W\d_])\xad(?:[ \t]*\r?\n[ \t]*)?(?=[^\W\d_])")

CONSEJO_GUION_BLANDO = "Guion blando: buscar con buscar_tolerante.py."
CONSEJO_LIGADURAS = "Ligaduras tipográficas: buscar con buscar_tolerante.py."
CONSEJO_NUL = "NUL: rg salta este fichero al recorrer carpetas; usar rg -a."


def medir_invisibles(texto: str) -> dict[str, int]:
    """Cuenta en el markdown el guion blando, el NUL y las ligaduras U+FB00–U+FB06.

    `guion_blando_parte_palabra` son los U+00AD que cortan una palabra: entre dos letras (`[^\\W\\d_]`), pegados a la
    segunda o con un salto de línea en medio. Delante de una palabra, detrás de un guion normal o seguidos de espacio
    sin salto, no parten palabra.
    """
    return {
        "guion_blando": texto.count("\xad"),
        "guion_blando_parte_palabra": len(_GUION_BLANDO_PARTE.findall(texto)),
        "nul": texto.count("\x00"),
        "ligaduras": sum(1 for caracter in texto if "\ufb00" <= caracter <= "\ufb06"),
    }


def linea_invisibles(medida: dict[str, int], ruta: str | None = None) -> str:
    """Línea `Invisibles:`. Con todo a cero dice «ninguno»; si no, las cuatro cifras y el consejo que sirve para
    cada carácter presente (Sol, S04: a las ligaduras se les recomendaba una herramienta que no las pliega)."""
    hay = any(
        medida[clave]
        for clave in ("guion_blando", "guion_blando_parte_palabra", "nul", "ligaduras")
    )
    if not hay:
        cuerpo = "ninguno"
    else:
        consejos = [c for clave, c in (("guion_blando_parte_palabra", CONSEJO_GUION_BLANDO),
                                        ("ligaduras", CONSEJO_LIGADURAS), ("nul", CONSEJO_NUL))
                    if medida[clave]]
        cuerpo = (
            f"guion_blando={medida['guion_blando']}, "
            f"guion_blando_parte_palabra={medida['guion_blando_parte_palabra']}, "
            f"nul={medida['nul']}, "
            f"ligaduras={medida['ligaduras']}."
            + "".join(" " + c for c in consejos)
        )
    if ruta:
        return f"Invisibles: {ruta} -- {cuerpo}"
    return f"Invisibles: {cuerpo}"


def anotar_lectura_capa(lectura: str, medida: dict[str, int]) -> str:
    """Si la lectura acaba en «limpio» y hay invisibles, remite a esa línea.

    El número y la palabra del veredicto no cambian: solo se añade la remisión.
    """
    hay = medida["guion_blando"] or medida["nul"] or medida["ligaduras"]
    if hay and lectura.endswith("limpio"):
        return f"{lectura} -- ver Invisibles:"
    return lectura


def aviso_modulo_ausente(nombre: str) -> None:
    print(
        f"Falta el módulo {nombre}. "
        f"Este intérprete es {sys.executable}. "
        f"Hace falta {INTERPRETE_CON_LECTURA_PDF}.",
        file=sys.stderr,
    )
    raise SystemExit(1)


try:
    from pdf2md import (
        convert_pdf_to_markdown,
        resolve_output_dir,
        resolve_repo_path,
        slugify,
        write_markdown,
    )
except ImportError as exc:
    aviso_modulo_ausente(getattr(exc, "name", None) or "pymupdf4llm")
from fidelidad_capa import banda as fidelity_band, medir_markdown as measure_text_layer

REPO_ROOT = Path(__file__).resolve().parent
RAW_ROOT = REPO_ROOT / "01_raw"
DEFAULT_MD_OUT = "01_raw/web"
EXPLICIT_OUT_EXTENSIONS = {".pdf", ".epub"}
TEXT_PASSTHROUGH_EXTENSIONS = {".md", ".txt"}
EPUB_HTML_EXTENSIONS = (".xhtml", ".html", ".htm")
EPUB_PAGE_TOKEN = "ATENEOEPUBPAGEBREAK"
INVISIBLE_TEXT_CHARS = dict.fromkeys(map(ord, "\u00ad\u200b\ufeff"), None)
MIN_PDF_TEXT_CHARACTERS_PER_PAGE = 200


def assess_pdf_text_layer(markdown: str, pdf_pages: int) -> dict:
    """Descuenta líneas presentes en mayoría estricta SOLO para decidir OCR.

    Los marcadores no son texto del libro. Se cuenta cada línea una vez por
    página, incluyendo las páginas vacías en el denominador. No se altera el
    Markdown ni el page_model: una cabecera descontada puede ser texto legítimo.
    Una sola página no permite establecer repetición entre páginas.
    El mínimo se aplica a la media por página, también contando las vacías:
    los folios de un libro largo no deben bastar para aceptar su capa.
    """
    parts = re.split(r"(?m)^<!-- pdf_page: \d+ -->\s*$", markdown)
    pages = parts[1:] if len(parts) > 1 else [markdown]
    lines_by_page = [
        [normalized for line in page.splitlines()
         if (normalized := " ".join(line.split()))]
        for page in pages
    ]
    page_count = max(pdf_pages, len(pages))
    min_pages = max(2, page_count // 2 + 1)
    counts = Counter(line for lines in lines_by_page for line in set(lines))
    repeated = {line for line, count in counts.items() if count >= min_pages}
    all_lines = [line for lines in lines_by_page for line in lines]
    remaining = [line for line in all_lines if line not in repeated]
    characters = len("\n".join(remaining))
    return {
        "scope": "OCR decision only; Markdown unchanged",
        "pdf_pages": page_count,
        "repeated_line_min_pages": min_pages,
        "minimum_characters_per_page": MIN_PDF_TEXT_CHARACTERS_PER_PAGE,
        "characters_before": len("\n".join(all_lines)),
        "characters_after": characters,
        "characters_per_page_after": characters / page_count,
        "repeated_lines": [
            {"line": line, "pages": counts[line]} for line in sorted(repeated)
        ],
        "decision": (
            "ocr" if characters < MIN_PDF_TEXT_CHARACTERS_PER_PAGE * page_count
            else "capa"
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingesta híbrida a Markdown para Ateneo-KB."
    )
    parser.add_argument("input", nargs="?", help="Ruta al archivo de entrada.")
    parser.add_argument(
        "--solo-invisibles",
        nargs="+",
        metavar="MD",
        help=(
            "Mide uno o varios Markdown ya escritos e imprime la línea Invisibles:, "
            "sin reingerir ni escribir el sidecar."
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "Carpeta de salida para el Markdown derivado. Obligatoria para PDF o EPUB. "
            f"Para otros formatos, por defecto: {DEFAULT_MD_OUT}."
        ),
    )
    parser.add_argument(
        "--converter",
        choices=["auto", "pdf_pages", "pdf2md", "markitdown", "epub_clean", "rapidocr"],
        default="auto",
        help="Conversor a usar. 'auto' enruta por extensión.",
    )
    parser.add_argument(
        "--hojas-desfase", type=int, nargs="+", metavar="N",
        help="Hojas físicas del PDF que se contrastan por OCR (por defecto, cinco de muestra).",
    )
    parser.add_argument(
        "--ocr-lang",
        default="spa",
        help="Idioma Tesseract para el fallback OCR automático (default: spa).",
    )
    parser.add_argument(
        "--stem",
        help="Nombre base del archivo de salida (sin extensión).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Sobrescribe el .md derivado si ya existe.",
    )
    parser.add_argument(
        "--copy-original",
        action="store_true",
        help=(
            "Compatibilidad hacia atrás. La preservación del original ahora es el "
            "comportamiento por defecto."
        ),
    )
    parser.add_argument(
        "--skip-original-copy",
        action="store_true",
        help=(
            "No copia el original a la vault. Úsalo solo si el archivo ya vive en "
            "01_raw o si quieres forzar una ingesta derivada sin original."
        ),
    )
    parser.add_argument(
        "--original-out",
        help="Carpeta de destino para el original copiado. Si se omite, se infiere por extensión.",
    )
    parser.add_argument(
        "--write-meta",
        action="store_true",
        help="Escribe un sidecar .meta.json con detalles de la conversión.",
    )
    return parser


def infer_converter(input_path: Path, explicit: str) -> str:
    if explicit != "auto":
        return explicit
    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        return "pdf_pages"
    if suffix == ".epub":
        return "epub_clean"
    if suffix in TEXT_PASSTHROUGH_EXTENSIONS:
        return "copy"
    return "markitdown"


def infer_original_out(input_path: Path) -> str:
    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        return "01_raw/pdf"
    if suffix in {".html", ".htm", ".md", ".txt"}:
        return "01_raw/web"
    if suffix == ".epub":
        return "01_raw/papers"
    return "01_raw/imported"


def ensure_input_exists(input_path: Path) -> None:
    if not input_path.exists() or not input_path.is_file():
        raise FileNotFoundError(f"No existe el archivo de entrada: {input_path}")


def is_in_raw(input_path: Path) -> bool:
    try:
        input_path.resolve().relative_to(RAW_ROOT.resolve())
    except ValueError:
        return False
    return True


def convert_with_pdf_pages(input_path: Path, textos: list[str] | None = None) -> tuple[str, dict]:
    """
    PDF -> Markdown keeping the page boundary as an explicit anchor.

    Emits one `<!-- pdf_page: NNN -->` marker per page that carries a text
    layer. The marker is the PDF page, NOT the printed page: double-page
    scans hold two printed pages behind one marker, and the offset to the
    printed page can change between sections of the same document. This
    converter does not try to derive the printed page; it keeps the anchor
    the offset can later be applied to. A flat conversion leaves nothing.

    Pages without a text layer are skipped, but numbering stays absolute so
    a skipped page never shifts the anchors after it. They are reported in
    the returned page_model so the meta sidecar records the holes.
    If supplied, textos collects one unstripped layer per PDF page.
    """
    try:
        import pymupdf
    except ImportError as exc:
        aviso_modulo_ausente(getattr(exc, "name", None) or "pymupdf")

    from folio_candidato import fila_de_pagina

    flags = getattr(pymupdf, "TEXTFLAGS_TEXT", None)
    compartir = flags is not None and flags == getattr(pymupdf, "TEXTFLAGS_WORDS", None)
    filas = []
    chunks: list[str] = []
    without_text: list[int] = []
    with pymupdf.open(input_path) as doc:
        page_count = doc.page_count
        for number, page in enumerate(doc, start=1):
            if compartir:
                textpage = page.get_textpage(flags=flags)
                bruto = page.get_text(textpage=textpage)
                filas.append(fila_de_pagina(page, number, textpage))
                del textpage
            else:
                bruto = page.get_text()
            if textos is not None:
                textos.append(bruto)
            text = bruto.strip()
            if not text:
                without_text.append(number)
                continue
            chunks.append(f"<!-- pdf_page: {number:03d} -->")
            chunks.append(text)

    page_model = {
        "kind": "pdf_page_markers",
        "marker": "<!-- pdf_page: NNN -->",
        "numbering": "absolute PDF page; skipped pages do not shift later anchors",
        "pdf_pages": page_count,
        "pages_with_text": page_count - len(without_text),
        "pages_without_text_layer": without_text,
        "printed_page": (
            "not derived -- per-document offset lives in the pagination audit"
        ),
        "folio_candidato": folio_candidates(input_path, filas if compartir else None),
    }
    return "\n".join(chunks), page_model


def folio_candidates(input_path: Path, filas=None) -> dict:
    """El folio impreso que cada página del PDF trae en su pie o cabecera, de ESA página.

    Va en la ficha y no en la marca del Markdown: cinco lectores esperan
    `<!-- pdf_page: NNN -->` exacto, y una línea aparte se colaría como texto del
    libro. En el Markdown, el folio al pie queda justo antes de la marca de la
    página SIGUIENTE y parece suyo:
    esto dice de qué página es. Sigue siendo candidato: se lee en la imagen.
    """
    from folio_candidato import candidatos, leer

    res = candidatos(leer(str(input_path)) if filas is None else filas)
    return {
        "aviso": "candidato de la capa de texto, no lectura: el folio se lee en la imagen",
        "capa": res["capa"],
        "lado": res["lado"],
        "doble_pagina": res["doble_pagina"],
        "paginas": {
            str(p["pagina_pdf"]): {
                "folio": p["folio_candidato"],
                "origen": p["origen"],
                **({"vistos": p["vistos"]} if "vistos" in p else {}),
                **({"salto": p["salto"]} if "salto" in p else {}),
            }
            for p in res["paginas"]
            if p["folio_candidato"] is not None or "salto" in p or "vistos" in p
        },
        "sin_folio": [
            p["pagina_pdf"] for p in res["paginas"] if p["folio_candidato"] is None
        ],
    }


def hojas_de_la_muestra(total: int) -> list[int]:
    elegidas: list[int] = []
    for fraccion in (0.15, 0.35, 0.55, 0.75, 0.95):
        pagina = max(1, min(total, round(fraccion * total)))
        if pagina not in elegidas:
            elegidas.append(pagina)
    return elegidas


def renglon_de_muestra(page, texto=None, repeated=()) -> str:
    if texto is None:
        texto = page.get_text()
    if not texto.strip():
        return "(sin capa de texto)"
    has_letters = False
    for linea in texto.splitlines():
        if any(caracter.isalpha() for caracter in linea if not caracter.isspace()):
            has_letters = True
            if " ".join(linea.split()) not in repeated:
                return linea.strip()[:100]
    return "(solo líneas repetidas)" if has_letters else "(sin letras)"


def leer_muestra(path, textos=None, repeated=None) -> tuple[list[dict], int]:
    import pymupdf

    if textos is None:
        with pymupdf.open(path) as doc:
            total = doc.page_count
            sample = {n: doc[n - 1].get_text() for n in hojas_de_la_muestra(total)} if total else {}
    else:
        total = len(textos)
        sample = {n: textos[n - 1] for n in hojas_de_la_muestra(total)} if total else {}
    ignored = set()
    if repeated is not None:
        # A running header can alternate and never reach a whole-book majority.
        counts = Counter(line for page in (textos if textos is not None else sample.values())
                         for line in {" ".join(s.split()) for s in page.split("\n", 4)[:4]})
        ignored = repeated | {line for line, count in counts.items() if count > 1}
    return [{"pagina_pdf": n, "renglon": renglon_de_muestra(None, page, ignored)}
            for n, page in sample.items()], total


def muestra_de_hojas(path, textos=None) -> list[dict]:
    muestra, _total = leer_muestra(path, textos)
    return muestra


def muestra_ocr(markdown: str, total: int) -> list[dict]:
    """Read the sample from the runner's absolute PDF anchors, including blanks."""
    parts = re.split(r"(?m)^<!-- pdf_page: (\d+) -->\r?$", markdown)
    numbers = [int(number) for number in parts[1::2]]
    if numbers != list(range(1, total + 1)):
        raise ValueError("Las anclas OCR no coinciden con las hojas físicas del PDF.")
    selected = set(hojas_de_la_muestra(total))
    return [
        {"pagina_pdf": n, "renglon": renglon_de_muestra(None, page) if page.strip() else "(sin texto OCR)"}
        for n, page in zip(numbers, parts[2::2]) if n in selected
    ]


def convert_with_markitdown(input_path: Path) -> str:
    try:
        from markitdown import MarkItDown
    except ImportError as exc:
        aviso_modulo_ausente(getattr(exc, "name", None) or "markitdown")

    init_params = inspect.signature(MarkItDown.__init__).parameters
    if "enable_plugins" in init_params:
        md = MarkItDown(enable_plugins=False)
    else:
        md = MarkItDown()
    result = md.convert(str(input_path))
    text = getattr(result, "text_content", None)
    if not text:
        raise RuntimeError(f"MarkItDown no devolvió texto para: {input_path}")
    return text


def convert_with_epub_clean(input_path: Path) -> str:
    """
    Custom clean extraction for EPUB files.

    Reads the EPUB spine instead of sorting files alphabetically and converts
    each HTML/XHTML body to structured Markdown. This keeps headings,
    paragraphs, lists, and notes searchable without flattening a whole chapter
    into a single line.
    """
    with zipfile.ZipFile(input_path, "r") as z:
        full_text = []
        for member in epub_spine_members(z):
            content = read_epub_member(z, member)
            markdown = html_fragment_to_markdown(content)
            if markdown:
                full_text.append(markdown)

        return "\n\n".join(full_text)


def epub_spine_members(z: zipfile.ZipFile) -> list[str]:
    """
    Return HTML-like EPUB members in reading order.

    EPUB reading order lives in the package document's spine. If the package
    metadata is malformed, fall back to sorted HTML members so old behavior
    still works.
    """
    names = set(z.namelist())
    try:
        container = ElementTree.fromstring(z.read("META-INF/container.xml"))
        rootfile = next(
            element
            for element in container.iter()
            if strip_xml_namespace(element.tag) == "rootfile"
            and element.attrib.get("full-path")
        )
        opf_path = rootfile.attrib["full-path"]
        opf = ElementTree.fromstring(z.read(opf_path))
    except Exception:
        return sorted_html_members(names)

    base_dir = posixpath.dirname(opf_path)
    manifest: dict[str, str] = {}
    for item in opf.iter():
        if strip_xml_namespace(item.tag) != "item":
            continue
        item_id = item.attrib.get("id")
        href = item.attrib.get("href")
        if not item_id or not href:
            continue
        member = normalize_epub_member(base_dir, href)
        media_type = item.attrib.get("media-type", "").lower()
        if member.lower().endswith(EPUB_HTML_EXTENSIONS) or "html" in media_type:
            manifest[item_id] = member

    members: list[str] = []
    seen: set[str] = set()
    for itemref in opf.iter():
        if strip_xml_namespace(itemref.tag) != "itemref":
            continue
        member = manifest.get(itemref.attrib.get("idref", ""))
        if member and member in names and member not in seen:
            members.append(member)
            seen.add(member)

    return members or sorted_html_members(names)


def sorted_html_members(names: set[str]) -> list[str]:
    return sorted(name for name in names if name.lower().endswith(EPUB_HTML_EXTENSIONS))


def normalize_epub_member(base_dir: str, href: str) -> str:
    href = unquote(href.split("#", 1)[0])
    joined = posixpath.normpath(posixpath.join(base_dir, href))
    return joined.lstrip("./")


def strip_xml_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_epub_member(z: zipfile.ZipFile, member: str) -> str:
    raw = z.read(member)
    encoding_match = re.search(
        br"<\?xml[^>]*encoding=[\"']([^\"']+)[\"']",
        raw[:200],
        flags=re.IGNORECASE,
    )
    encoding = encoding_match.group(1).decode("ascii", errors="ignore") if encoding_match else "utf-8"
    return raw.decode(encoding or "utf-8", errors="replace")


def html_fragment_to_markdown(content: str) -> str:
    try:
        from bs4 import BeautifulSoup, Comment
        from markdownify import markdownify
    except ImportError as exc:
        raise RuntimeError(
            "El conversor EPUB estructurado requiere beautifulsoup4 y markdownify."
        ) from exc

    soup = BeautifulSoup(content, "html.parser")
    root = soup.body or soup

    for element in root.find_all(["script", "style", "svg"]):
        element.decompose()
    for comment in root.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    # La página impresa que el editor marcó en el EPUB es lo único que lo hace
    # citable; markdownify la tiraba sin avisar (Stiegler, 2026-09-03: 963
    # marcas, cero en el markdown). Se cambia por un testigo sin < > ni _ para
    # que no lo borre ni lo escape la conversión, y luego se vuelve ancla.
    for element in root.find_all(attrs={"epub:type": "pagebreak"}) + root.find_all(
        attrs={"role": "doc-pagebreak"}
    ):
        if element.parent is None:
            continue
        label = (
            element.get("title")
            or element.get("aria-label")
            or re.sub(r"^(?:page|p)[-_]?", "", element.get("id", ""), flags=re.I)
        )
        label = re.sub(r"[^0-9A-Za-z]", "", label)
        if label:
            element.replace_with(f" {EPUB_PAGE_TOKEN}{label}{EPUB_PAGE_TOKEN} ")
        else:
            element.decompose()

    markdown = markdownify(
        str(root),
        heading_style="ATX",
        bullets="-",
        strip=["img"],
    )
    markdown = re.sub(
        rf"\s*{EPUB_PAGE_TOKEN}([0-9A-Za-z]+){EPUB_PAGE_TOKEN}\s*",
        r"\n\n<!-- page: \1 -->\n\n",
        markdown,
    )
    return normalize_markdown(markdown)


def normalize_markdown(markdown: str) -> str:
    markdown = markdown.translate(INVISIBLE_TEXT_CHARS)
    markdown = markdown.replace("\xa0", " ")
    lines = []
    for line in markdown.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        lines.append(line)
    markdown = "\n".join(lines)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip()


def convert_with_copy(input_path: Path) -> str:
    return input_path.read_text(encoding="utf-8")


def convert_with_rapidocr(input_path: Path) -> str:
    """
    Lazy import of OCR module to avoid heavy dependencies unless used.
    """
    try:
        from rapidocr_pdf import convert_pdf_to_markdown_ocr
    except ImportError:
        raise RuntimeError(
            "Módulo rapidocr_pdf no encontrado. Asegúrate de que rapidocr_pdf.py esté en el path."
        )
    return convert_pdf_to_markdown_ocr(str(input_path))


def convert_with_tesseract(input_path: Path, language: str) -> str:
    """Run the existing page-aware Tesseract converter in a disposable folder."""
    runner = REPO_ROOT / "04_ops" / "scripts" / "ocr_tesseract.py"
    with tempfile.TemporaryDirectory(prefix="ateneo-tesseract-") as tmp:
        output = Path(tmp) / "ocr.md"
        subprocess.run(
            [sys.executable, str(runner), str(input_path), str(output), "--lang", language],
            check=True,
        )
        return output.read_text(encoding="utf-8")


def ruta_declarada(ruta: Path) -> str:
    """Ruta para el sidecar: relativa si el fichero está dentro del repositorio.

    Una ruta absoluta lleva el nombre de usuario de quien ingirió; al compartir la
    biblioteca, viajaría con cada sidecar. Fuera del repo no hay otra forma de
    decir de dónde salió el fichero, y se guarda entera.
    """
    try:
        return ruta.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(ruta.resolve())


def write_meta(
    meta_path: Path,
    *,
    source_path: Path,
    original_copy: Path | None,
    original_preserved: bool,
    preservation_mode: str,
    converter: str,
    page_model: dict | None = None,
    text_layer_fidelity: dict | None = None,
    text_layer_sufficiency: dict | None = None,
    pdf_origin: dict | None = None,
    capa_heredada: dict | None = None,
    muestra_hojas: list | None = None,
    ocr_fallback: dict | None = None,
    invisibles: dict | None = None,
    capa_desfasada: dict | None = None,
) -> None:
    payload = {
        "source_path": ruta_declarada(source_path),
        "original_copy": ruta_declarada(original_copy) if original_copy else None,
        "original_preserved": original_preserved,
        "preservation_mode": preservation_mode,
        "converter": converter,
        "converted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "input_extension": source_path.suffix.lower(),
    }
    if page_model is not None:
        payload["page_model"] = page_model
    # Se declara SIEMPRE que se haya medido, tambien cuando sale limpio: un
    # campo ausente significa «nadie lo ha mirado», que es el estado que esta
    # medida vino a terminar.
    if text_layer_fidelity is not None:
        payload["text_layer_fidelity"] = text_layer_fidelity
    if invisibles is not None:
        payload["invisibles"] = invisibles
    if text_layer_sufficiency is not None:
        payload["text_layer_sufficiency"] = text_layer_sufficiency
    if pdf_origin is not None:
        payload["pdf_origin"] = pdf_origin
    if capa_heredada is not None:
        # La evidencia por hoja se reproduce con capa_heredada.py <pdf>;
        # la ficha conserva el veredicto, los agregados y los fallos, sin ese volumen.
        payload["capa_heredada"] = {k: v for k, v in capa_heredada.items() if k != "hojas"}
    if capa_desfasada is not None:
        payload["capa_desfasada"] = capa_desfasada
    if muestra_hojas is not None:
        payload["muestra_hojas"] = muestra_hojas
    if ocr_fallback is not None:
        payload["ocr_fallback"] = ocr_fallback
    meta_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def maybe_copy_original(
    *,
    input_path: Path,
    skip_original_copy: bool,
    original_out: str | None,
    overwrite: bool,
) -> tuple[Path | None, str]:
    if is_in_raw(input_path):
        return input_path.resolve(), "already_in_vault"

    if skip_original_copy:
        return None, "skipped"

    target_dir = resolve_output_dir(original_out or infer_original_out(input_path))
    target_path = Path(target_dir) / input_path.name
    if target_path.exists() and not overwrite:
        raise FileExistsError(
            f"El original ya existe en la vault: {target_path}. Usa --overwrite para reemplazarlo."
        )
    shutil.copy2(input_path, target_path)
    return target_path, "copied"


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.solo_invisibles:
        for ruta in args.solo_invisibles:
            texto = Path(ruta).read_text(encoding="utf-8")
            print(linea_invisibles(medir_invisibles(texto), ruta))
        return
    if not args.input:
        parser.error("falta el archivo de entrada")
    input_path = Path(resolve_repo_path(args.input))
    if args.out is None:
        candidates = input_path.rglob("*") if input_path.is_dir() else (input_path,)
        if any(
            path.suffix.lower() in EXPLICIT_OUT_EXTENSIONS and path.is_file()
            for path in candidates
        ):
            parser.error(
                "Para PDF o EPUB, especifica --out. "
                "Por ejemplo: --out 01_raw/books o --out 01_raw/papers."
            )
    ensure_input_exists(input_path)

    converter = infer_converter(input_path, args.converter)
    out_dir = Path(resolve_output_dir(DEFAULT_MD_OUT if args.out is None else args.out))
    stem = args.stem or slugify(input_path.name)
    out_path = out_dir / f"{stem}.md"

    if out_path.exists() and not args.overwrite:
        raise FileExistsError(
            f"El Markdown derivado ya existe: {out_path}. Usa --overwrite para reemplazarlo."
        )

    page_model: dict | None = None

    textos = None
    if converter == "pdf_pages":
        textos = []
        markdown, page_model = convert_with_pdf_pages(input_path, textos)
    elif converter == "pdf2md":
        markdown = convert_pdf_to_markdown(str(input_path))
    elif converter == "markitdown":
        markdown = convert_with_markitdown(input_path)
    elif converter == "epub_clean":
        markdown = convert_with_epub_clean(input_path)
        printed = re.findall(r"(?m)^<!-- page: ([0-9A-Za-z]+) -->$", markdown)
        if printed:
            page_model = {
                "anchor": "<!-- page: NNN -->",
                "anchor_is": (
                    "printed page as the publisher placed it in the EPUB "
                    "(pagebreak marks); not checked against the printed book"
                ),
                "printed_page_anchors": len(printed),
                "first": printed[0],
                "last": printed[-1],
            }
        print(f"Folios:    {len(printed)} anclas de página impresa del EPUB")
    elif converter == "rapidocr":
        markdown = convert_with_rapidocr(input_path)
        if markdown.startswith("Error: Dependencias de OCR"):
            aviso_modulo_ausente("rapidocr_onnxruntime")
    else:
        markdown = convert_with_copy(input_path)

    muestra = None
    total_muestra = 0
    error_muestra = None

    text_layer_sufficiency = None
    if args.converter == "auto" and input_path.suffix.lower() == ".pdf":
        text_layer_sufficiency = assess_pdf_text_layer(
            markdown, page_model["pdf_pages"] if page_model else 1,
        )

    ocr_fallback = None
    # Automatic fallback discounts repeated lines and page markers first.
    if (
        args.converter == "auto"
        and input_path.suffix.lower() == ".pdf"
        and text_layer_sufficiency["decision"] == "ocr"
        and converter != "rapidocr"
    ):
        print(
            "Warning: El PDF parece ser una imagen o está vacío. Reintentando con Tesseract...",
            file=sys.stderr,
        )
        try:
            markdown = convert_with_tesseract(input_path, args.ocr_lang)
            total_muestra = page_model["pdf_pages"]
            muestra = muestra_ocr(markdown, total_muestra)
            converter = "tesseract (fallback)"
            ocr_fallback = {
                "engine": "tesseract",
                "language": args.ocr_lang,
                "trigger": "insufficient_pdf_text_layer",
                "status": "completed",
            }
            # The OCR pass replaces the whole markdown, anchors included, so the
            # page_model from pdf_pages no longer describes this file.
            page_model = None
        except Exception as e:
            raise SystemExit(
                f"Error en fallback Tesseract ({args.ocr_lang}): {e}. "
                "No se ha escrito el Markdown ni el sidecar; revisa Tesseract y el idioma solicitado."
            ) from e

    if input_path.suffix.lower() == ".pdf" and ocr_fallback is None:
        repeated = ({row["line"] for row in text_layer_sufficiency["repeated_lines"]}
                    if text_layer_sufficiency else None)
        try:
            muestra, total_muestra = leer_muestra(input_path, textos, repeated)
        except ImportError as exc:
            aviso_modulo_ausente(getattr(exc, "name", None) or "pymupdf")
        except Exception as exc:
            error_muestra = exc

    write_markdown(str(out_path), markdown)

    # Se mide el markdown que se acaba de escribir, no el PDF: el markdown es el
    # texto que la KB usa de verdad -- nadie busca dentro del PDF. Medir el
    # original cuando lo que se lee es el derivado daria un numero correcto
    # sobre el objeto equivocado.
    fidelity = measure_text_layer(markdown)
    invisibles = medir_invisibles(markdown)
    from erratas_palabra import lectura_integrada

    # An ebook converter may repaginate independently of the printed edition.
    pdf_origin = None
    if input_path.suffix.lower() == ".pdf":
        from productor_pdf import evaluar as evaluar_productor
        try:
            pdf_origin = evaluar_productor(input_path)
        except Exception as exc:
            # Metadatos ilegibles no paran el alta, pero quedan dichos en la ficha.
            pdf_origin = {"error": f"metadatos ilegibles: {exc}"}

    capa_heredada = None
    if input_path.suffix.lower() == ".pdf":
        try:
            from capa_heredada import evaluar, aviso_segundo_lector
            capa_heredada = evaluar(input_path, textos)
            aviso = aviso_segundo_lector(capa_heredada, input_path, out_path)
            if aviso:
                print(aviso)
        except Exception as exc:
            capa_heredada = {"error": f"detector de capa heredada: {exc}"}
            print(f"Capa heredada: no se pudo evaluar ({exc})")

    capa_desfasada = None
    if input_path.suffix.lower() == ".pdf":
        try:
            from capa_desfasada import evaluar as evaluar_desfase, aviso as aviso_desfase
            hojas = args.hojas_desfase
            if hojas is None:
                candidates = [row["pagina_pdf"] for row in capa_heredada.get("hojas", [])
                              if row["caracteres"] >= 40]
                hojas = ([candidates[i - 1] for i in hojas_de_la_muestra(len(candidates))]
                         if candidates else [row["pagina_pdf"] for row in muestra or []])
            lengua = capa_heredada.get("lengua") or args.ocr_lang
            capa_desfasada = evaluar_desfase(input_path, hojas, lengua, textos)
            print(aviso_desfase(capa_desfasada))
        except Exception as exc:
            capa_desfasada = {"error": str(exc)}
            print(f"Alineación de la capa: no se pudo evaluar ({exc})")

    copied_original, preservation_mode = maybe_copy_original(
        input_path=input_path,
        skip_original_copy=args.skip_original_copy,
        original_out=args.original_out,
        overwrite=args.overwrite,
    )

    if preservation_mode == "skipped":
        print(
            "Warning: original no preservado en 01_raw. Esta ingesta pierde trazabilidad "
            "editorial salvo que el usuario lo haya pedido explícitamente.",
            file=sys.stderr,
        )

    if args.write_meta:
        write_meta(
            out_path.with_suffix(".meta.json"),
            source_path=input_path,
            original_copy=copied_original,
            original_preserved=preservation_mode != "skipped",
            preservation_mode=preservation_mode,
            converter=converter,
            page_model=page_model,
            text_layer_fidelity=fidelity,
            text_layer_sufficiency=text_layer_sufficiency,
            pdf_origin=pdf_origin,
            capa_heredada=capa_heredada,
            capa_desfasada=capa_desfasada,
            muestra_hojas=muestra,
            ocr_fallback=ocr_fallback,
            invisibles=invisibles,
        )

    print(f"Conversor: {converter}")
    print(f"Markdown:  {out_path}")
    # Se imprime SIEMPRE, con --write-meta o sin el. El punto de esta medida es
    # que nadie descubra un testigo sucio semanas despues, citando de el.
    print(
        f"Capa:      {fidelity['per_thousand_words']}/mil palabras "
        f"({fidelity['units_affected']}/{fidelity['units_total']} unidades, "
        f"ratio {fidelity['distinct_mark_ratio']}) -- "
        + anotar_lectura_capa(lectura_integrada(fidelity_band(
            fidelity["per_thousand_words"],
            fidelity["distinct_mark_ratio"],
            fidelity["words"],
            fidelity.get("diacritics_per_thousand"),
            fidelity.get("diacritics_language"),
        ), fidelity['wordlike_errors']), invisibles)
    )
    print(linea_invisibles(invisibles))
    if input_path.suffix.lower() == ".pdf":
        if error_muestra is not None:
            print(f"Muestra:   no se pudo leer ({error_muestra})")
        elif muestra is not None:
            print(
                "Muestra:   leer estas cinco antes de dar el alta "
                "(¿es el libro que dice ser?)"
            )
            for item in muestra:
                print(
                    f"  hoja {item['pagina_pdf']:3d}/{total_muestra}: {item['renglon']}"
                )
    from fidelidad_capa import aviso_controles
    control_warning = aviso_controles(fidelity['control_characters'])
    if control_warning:
        print(control_warning)
    if page_model and "folio_candidato" in page_model:
        folios = page_model["folio_candidato"]
        if folios["capa"] != "ok":
            print("Folio:     sin capa de texto -- leer cada folio en la imagen")
        else:
            saltos = sum(1 for p in folios["paginas"].values() if "salto" in p)
            print(
                f"Folio:     {folios['lado']}"
                + (", doble página" if folios["doble_pagina"] else "")
                + f" -- {len(folios['paginas'])} candidatos, "
                f"{len(folios['sin_folio'])} sin folio, {saltos} saltos"
                + ("" if args.write_meta else " (usa --write-meta para guardarlos)")
            )
    if pdf_origin and pdf_origin.get("paginacion_impresa") is False:
        print(f"Páginas:   {pdf_origin['aviso']}")
    if preservation_mode == "already_in_vault":
        print(f"Original:  ya estaba preservado en {copied_original}")
    elif copied_original:
        print(f"Original:  {copied_original}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
