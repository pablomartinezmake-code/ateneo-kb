"""OCR con Tesseract del PDF ya partido en páginas, con marca de página.

Escribe un Markdown con `<!-- pdf_page: NNN -->` antes de cada página, como
`ingest_to_raw.py --converter pdf_pages`, y una caché por página para poder
reanudar. Tesseract conserva las tildes; RapidOCR las pierde (medido en la
p. 28 impresa el 2026-10-27).

Uso: python ocr_tesseract.py ENTRADA.pdf SALIDA.md [desde hasta] [--lang spa]
Si TESSDATA_PREFIX está definido, manda. Si no, se usa 04_ops/tessdata-local
cuando existe; en su ausencia Tesseract usa su directorio predeterminado.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fitz

# Orden: variable TESSERACT, el ejecutable en el PATH, la ruta habitual de Windows.
TESSERACT = (os.environ.get("TESSERACT") or shutil.which("tesseract")
             or r"C:\Program Files\Tesseract-OCR\tesseract.exe")
DPI = 300
LOCAL_TESSDATA = Path(__file__).resolve().parents[1] / "tessdata-local"


def tesseract_env():
    env = os.environ.copy()
    if "TESSDATA_PREFIX" not in env and LOCAL_TESSDATA.is_dir():
        env["TESSDATA_PREFIX"] = str(LOCAL_TESSDATA)
    return env


def check_language(language, env):
    """Fail before using a cache or writing output if a requested model is absent."""
    try:
        result = subprocess.run(
            [TESSERACT, "--list-langs"], capture_output=True, text=True,
            encoding="utf-8", check=True, env=env,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"No se pudo ejecutar Tesseract en {TESSERACT}: {exc}") from exc
    available = set(result.stdout.splitlines()[1:])
    missing = [lang for lang in language.split("+") if not lang or lang not in available]
    if missing:
        raise RuntimeError(
            f"Idioma(s) Tesseract no disponible(s): {', '.join(missing)}. "
            "Instala los datos traineddata oficiales o indica un idioma instalado con --ocr-lang/--lang; "
            f"disponibles: {', '.join(sorted(available)) or '(ninguno)'}."
        )

def recognize(png, target, language, env):
    try:
        text = subprocess.run(
            [TESSERACT, str(png), "-", "-l", language, "--psm", "3"],
            capture_output=True, text=True, encoding="utf-8", check=True, env=env,
        ).stdout
        staged = png.with_suffix(".txt")
        staged.write_text(text, encoding="utf-8")
        staged.replace(target)
    finally:
        png.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("from_page", nargs="?", type=int, default=1)
    parser.add_argument("to_page", nargs="?", type=int)
    parser.add_argument("--lang", default=os.environ.get("TESS_LANG", "spa"), help="Código de idioma Tesseract (default: spa).")
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1),
                        help="Máximo de procesos Tesseract simultáneos (por defecto, hasta 4).")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers debe ser positivo")

    with fitz.open(args.input) as doc:
        run(args, parser, doc)


def run(args, parser, doc):
    desde = args.from_page
    hasta = args.to_page if args.to_page is not None else doc.page_count
    if desde < 1 or hasta < desde or hasta > doc.page_count:
        parser.error(f"rango inválido: {desde}-{hasta} (PDF de {doc.page_count} páginas)")
    env = tesseract_env()
    if args.workers > 1:
        env["OMP_THREAD_LIMIT"] = "1"
    check_language(args.lang, env)
    # Old unlabelled page caches cannot establish which language produced them.
    cache_identity = f"{args.lang}\0{env.get('TESSDATA_PREFIX', '')}"
    language_key = hashlib.sha256(cache_identity.encode("utf-8")).hexdigest()
    cache = args.output.with_suffix(".tess-cache") / language_key
    cache.mkdir(parents=True, exist_ok=True)

    # Only this thread accesses PyMuPDF. Workers receive closed PNG files.
    with tempfile.TemporaryDirectory(dir=cache) as tmp:
        pending = deque()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for n in range(desde, hasta + 1):
                destino = cache / f"p{n:04d}.txt"
                if destino.exists():
                    continue
                png = Path(tmp) / f"p{n:04d}.png"
                doc[n - 1].get_pixmap(dpi=DPI, colorspace=fitz.csGRAY).save(png)
                pending.append((n, pool.submit(recognize, png, destino, args.lang, env)))
                if len(pending) == args.workers:
                    page, future = pending.popleft()
                    future.result()
                    print(f"página {page}/{doc.page_count}", file=sys.stderr, flush=True)
            for page, future in pending:
                future.result()
                print(f"página {page}/{doc.page_count}", file=sys.stderr, flush=True)

        partes = []
        for n in range(1, doc.page_count + 1):
            f = cache / f"p{n:04d}.txt"
            if f.exists():
                partes.append(f"<!-- pdf_page: {n:03d} -->\n\n{f.read_text(encoding='utf-8').strip()}\n")
        staged = Path(tmp) / "result.md"
        staged.write_text("\n".join(partes), encoding="utf-8")
        staged.replace(args.output)
    print(f"{len(partes)} páginas en {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
