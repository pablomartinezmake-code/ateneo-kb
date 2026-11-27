#!/usr/bin/env python3
"""Censa ficheros de 01_raw que tienen forma de resumen, no de fuente.

Un raw es una fuente inmutable: el texto del documento. Un resumen redactado por
un agente (título, autores, «Abstract», «Key Findings», «Implications for Ateneo»)
no preserva la fuente aunque viva en 01_raw, y una ficha curada encima de él
hereda una confianza que nadie ha cotejado.

Complementa a ``censo_ingestas.py``: aquel mide conversiones declaradas que no
se sostienen contra su origen; este mira lo que no declara conversión alguna.
Sólo lee. No mueve, no borra, no reescribe ningún raw.

Criterio (fijado antes de correr sobre la población):
- Fuera de sospecha: el ``.md`` tiene sidecar ``.meta.json`` que nombra el
  fichero o la URL de los que sale el texto (lo produjo la herramienta de
  ingesta), o vive en ``reference-links/`` (rastro declarado).
- ``sidecar sin procedencia``: hay sidecar, pero no dice de qué fichero sale el
  texto (``source_path: web_search``, campos vacíos). Añadido el 2026-09-17: dos
  resúmenes de agente llevaban un sidecar escrito
  a mano, ``converter: manual_research``, y el censo los dejaba pasar por tenerlo.
  Calibrado contra los 832 sidecars, no contra los dos casos: la primera versión
  acusó 7 y cuatro eran buenos (tres transcripciones de YouTube guardan el origen
  en ``source_url``; un PDF importado tenía extensión ``.txt__``). Tras ajustarlo
  acusa los dos resúmenes y un abstract.
- ``nota declarada``: frontmatter con ``tipo:`` — es una nota de trabajo que se
  dice nota, aunque hable de Ateneo.
- ``resumen con forma de fuente``: sin sidecar y con al menos DOS de estas
  señales: línea de autoría en negrita al principio; encabezado «Abstract»;
  encabezado que empieza por «Key »; encabezado que dirige el texto a Ateneo.
- ``sin sidecar, otra forma``: el resto de lo que no tiene sidecar. Deuda de
  trazabilidad, no hallazgo.
"""

from __future__ import annotations

import argparse
import collections
import json
import datetime as dt
import re
import subprocess
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = REPO_ROOT / "01_raw"
WIKI_ROOT = REPO_ROOT / "02_wiki"

AUTORIA = re.compile(r"^\*\*Authors?:\*\*", re.M)
ENCABEZADO = re.compile(r"^#{1,4} (.+)$", re.M)
DIRIGIDO_A_ATENEO = re.compile(
    r"(Implications? for Ateneo|Implicaci[oó]n(es)? para Ateneo|En el marco de Ateneo|"
    r"Relaci[oó]n con Ateneo|Resumen para Ateneo)", re.I)


@dataclass
class Registro:
    ruta: str
    bytes: int
    estado: str
    senales: list[str] = field(default_factory=list)
    citado_en: list[str] = field(default_factory=list)
    ficha: str = ""


def poblacion() -> list[Path]:
    rutas = [p for p in RAW_ROOT.rglob("*.md") if "reference-links" not in p.relative_to(RAW_ROOT).parts]
    return sorted(rutas, key=lambda p: p.relative_to(RAW_ROOT).as_posix())


def senales_de(texto: str) -> list[str]:
    cabeza = "\n".join(texto.splitlines()[:15])
    encabezados = ENCABEZADO.findall(texto)
    senales = []
    if AUTORIA.search(cabeza):
        senales.append("autoría en negrita")
    if any(h.strip().lower() == "abstract" for h in encabezados):
        senales.append("encabezado Abstract")
    if any(h.startswith("Key ") for h in encabezados):
        senales.append("encabezado Key …")
    if any(DIRIGIDO_A_ATENEO.search(h) for h in encabezados):
        senales.append("encabezado dirigido a Ateneo")
    return senales


ACUSADOS = ("resumen con forma de fuente", "sidecar sin procedencia")
CLAVES_DE_ORIGEN = ("source_path", "source_url", "original_copy", "source_epub", "derivado_de")


def procedencia_registrada(sidecar: Path) -> bool:
    """Un sidecar vale si nombra el fichero o la URL de los que sale el texto, o si lo escribió
    la reparación de capa sobre un raw que ya existía. `web_search` o un campo vacío no valen."""
    try:
        datos = json.loads(sidecar.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(datos, dict):
        return False
    if datos.get("text_layer_repair"):
        return True
    for clave in CLAVES_DE_ORIGEN:
        valor = str(datos.get(clave) or "").strip()
        if valor.startswith(("http://", "https://")) or re.search(r"\.\w{2,6}(\s|\(|$)", valor):
            return True
    return False


def clasificar(md: Path) -> Registro:
    try:
        rel = md.relative_to(RAW_ROOT).as_posix()
    except ValueError:  # fixtures de prueba fuera de la vault
        rel = md.name
    tam = md.stat().st_size
    sidecar = md.with_suffix(".meta.json")
    if sidecar.exists():
        if procedencia_registrada(sidecar):
            return Registro(rel, tam, "con sidecar")
        return Registro(rel, tam, "sidecar sin procedencia", ["el sidecar no dice de qué fichero sale el texto"])
    texto = md.read_text(encoding="utf-8-sig", errors="replace")
    senales = senales_de(texto)
    if texto.startswith("---") and re.search(r"^tipo:", texto[:400], re.M):
        return Registro(rel, tam, "nota declarada", senales)
    if len(senales) >= 2:
        return Registro(rel, tam, "resumen con forma de fuente", senales)
    return Registro(rel, tam, "sin sidecar, otra forma", senales)


def quien_lo_cita(slug: str) -> list[str]:
    try:
        salida = subprocess.run(
            ["rg", "-l", "--fixed-strings", slug, str(WIKI_ROOT)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        ).stdout
    except FileNotFoundError:  # sin ripgrep en el PATH: se degrada a Python
        salida = "\n".join(str(p) for p in WIKI_ROOT.rglob("*.md")
                           if slug in p.read_text(encoding="utf-8", errors="replace"))
    rutas = [Path(l).resolve() for l in salida.splitlines() if l.strip()]
    return sorted(p.relative_to(REPO_ROOT.resolve()).as_posix() for p in rutas)


def ficha_de(slug: str) -> str:
    ficha = WIKI_ROOT / "fuentes" / f"{slug}.md"
    if not ficha.exists():
        return "sin ficha"
    cabeza = ficha.read_text(encoding="utf-8", errors="replace")[:600]
    campos = dict(re.findall(r"^(estado|confianza):\s*(\S+)", cabeza, re.M))
    return f"{campos.get('estado', '?')} · {campos.get('confianza', '?')}"


def enriquecer(registros: list[Registro]) -> None:
    for r in registros:
        if r.estado in ACUSADOS:
            slug = Path(r.ruta).stem
            r.ficha = ficha_de(slug)
            r.citado_en = [c for c in quien_lo_cita(slug) if not c.endswith(f"fuentes/{slug}.md")]


def informe(registros: list[Registro], instante: str, head: str) -> str:
    cuenta = collections.Counter(r.estado for r in registros)
    sospechosos = [r for r in registros if r.estado in ACUSADOS]
    lineas = [
        "# Censo · Resúmenes con forma de fuente en 01_raw", "",
        f"**Pasada:** `{instante}` · **HEAD:** `{head}` · árbol de trabajo vivo.", "",
        "Orden: `python 04_ops/scripts/censo_resumenes_en_raw.py --report <ruta>`. "
        "Candado: `--self-test`. Criterio en el docstring del script.", "",
        f"Población `01_raw/**/*.md` sin `reference-links/`: **{len(registros)}**.", "",
        "| estado | cantidad |", "|---|---:|",
    ]
    for estado in ("con sidecar", "sidecar sin procedencia", "nota declarada", "resumen con forma de fuente",
                   "sin sidecar, otra forma"):
        lineas.append(f"| {estado} | {cuenta[estado]} |")
    lineas += ["", "## Resúmenes con forma de fuente y sidecars sin procedencia", "",
               "| raw | bytes | estado · señales | ficha (estado · confianza) | la citan además |",
               "|---|---:|---|---|---|"]
    for r in sospechosos:
        citan = "<br>".join(f"`{c}`" for c in r.citado_en) or "—"
        lineas.append(f"| `{r.ruta}` | {r.bytes:,} | {r.estado}: {', '.join(r.senales)} | {r.ficha} | {citan} |".replace(",", ".", 1))
    lineas += ["", "## Límites", "",
               "- La forma no prueba que el contenido sea falso: prueba que la fuente no está preservada. "
               "Lo que diga cada resumen hay que cotejarlo contra el artículo.",
               "- Un resumen escrito con otra plantilla (sin esas señales) cae en «sin sidecar, otra forma» "
               "y no se acusa aquí.",
               "- `nota declarada` confía en el frontmatter: una nota que se dice nota no se inspecciona más.", ""]
    return "\n".join(lineas)


def rutas_staged() -> list[Path]:
    """Ficheros .md de 01_raw añadidos, copiados, modificados o renombrados en el índice de git."""
    salida = subprocess.run(
        ["git", "-c", "core.quotepath=off", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "--", "01_raw"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    rutas = []
    for linea in salida.splitlines():
        p = REPO_ROOT / linea.strip()
        if p.suffix == ".md" and "reference-links" not in p.parts and p.exists():
            rutas.append(p)
    return rutas


def veredicto_staged(rutas: list[Path]) -> list[Registro]:
    return [r for r in (clasificar(p) for p in rutas) if r.estado in ACUSADOS]


def candado_staged() -> int:
    malos = veredicto_staged(rutas_staged())
    if not malos:
        return 0
    print("ABORTADO: raws con forma de resumen o sin procedencia en el sidecar (AGENTS.md, «Ingerir una fuente», paso 1):")
    for r in malos:
        print(f"  01_raw/{r.ruta}  [{', '.join(r.senales)}]")
    print("Ingiere el original con ingest_to_raw.py --write-meta o abre un rastro en 01_raw/reference-links/.")
    return 1


def _ingesta_y_nota(tmp: str) -> tuple[Path, Path]:
    """Dos fixtures fabricados: una ingesta con su sidecar y una nota que se declara nota."""
    ingesta = Path(tmp) / "autor-2024-articulo.md"
    ingesta.write_text("# Un artículo\n\nTexto convertido del original.\n", encoding="utf-8")
    ingesta.with_suffix(".meta.json").write_text(
        '{"source_path": "C:/Users/usuario/Downloads/autor (2024).pdf", "converter": "pdf_pages"}', encoding="utf-8")
    nota = Path(tmp) / "nota-de-trabajo.md"
    nota.write_text("---\ntipo: fuente\n---\n\n# Nota\n\n**Authors:** A\n\n## Abstract\n\nx\n", encoding="utf-8")
    return ingesta, nota


class Candado(unittest.TestCase):
    def test_staged_acusa_resumen_y_deja_pasar_ingesta(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(veredicto_staged(list(_ingesta_y_nota(tmp))), [])

    def test_resumen_conocido(self) -> None:
        # Fixture propio: un raw reparado ya no reproduce el defecto original
        # y un candado que depende de que el defecto siga vivo se pone rojo al arreglarlo.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            falso = Path(tmp) / "falso-resumen.md"
            falso.write_text("# Fabrication and errors\n\n**Authors:** W. H. Walters\n\n## Abstract\n\nx\n\n"
                             "## Key Findings\n\n- 55%\n\n## Implications for Ateneo\n\n- y\n", encoding="utf-8")
            self.assertEqual(clasificar(falso).estado, "resumen con forma de fuente")

    def test_ingesta_real_no_se_acusa(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(clasificar(_ingesta_y_nota(tmp)[0]).estado, "con sidecar")

    def test_nota_que_se_dice_nota(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(clasificar(_ingesta_y_nota(tmp)[1]).estado, "nota declarada")

    def test_sidecar_sin_procedencia(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "lutz-source.md"
            md.write_text("# Theo Lutz [Source Pack]\n\n**Source Info**: Web compilation.\n", encoding="utf-8")
            md.with_suffix(".meta.json").write_text(
                '{"source_path": "web_search", "converter": "manual_research"}', encoding="utf-8")
            self.assertEqual(clasificar(md).estado, "sidecar sin procedencia")
            self.assertEqual(len(veredicto_staged([md])), 1)
            md.with_suffix(".meta.json").write_text(
                '{"source_path": "C:/Users/usuario/Downloads/lutz (1959).pdf", "converter": "pdf_pages"}', encoding="utf-8")
            self.assertEqual(clasificar(md).estado, "con sidecar")

    def test_una_sola_senal_no_basta(self) -> None:
        self.assertLess(len(senales_de("# Título\n\n## Abstract\n\nTexto del artículo.\n")), 2)

    def test_dos_senales_si(self) -> None:
        texto = "# T\n\n**Authors:** A\n\n## Abstract\n\nx\n\n## Key Findings\n"
        self.assertGreaterEqual(len(senales_de(texto)), 2)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--muestra", nargs="*", help="rutas relativas a 01_raw para verificar en pequeño")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--staged", action="store_true",
                    help="candado pre-commit: aborta (exit 1) si algún raw preparado para commit tiene forma de resumen")
    args = ap.parse_args()

    if args.staged:
        return candado_staged()
    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Candado)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if args.muestra is not None:
        for rel in args.muestra:
            r = clasificar(RAW_ROOT / rel)
            print(f"{r.estado}\t{r.bytes}\t{r.ruta}\t{', '.join(r.senales)}")
        return 0

    registros = [clasificar(p) for p in poblacion()]
    enriquecer(registros)
    if args.report:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True).stdout.strip()
        instante = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        destino = args.report if args.report.is_absolute() else REPO_ROOT / args.report
        destino.write_text(informe(registros, instante, head), encoding="utf-8", newline="\n")
        print(destino)
    cuenta = collections.Counter(r.estado for r in registros)
    print(f"población {len(registros)} · " + " · ".join(f"{k} {v}" for k, v in cuenta.items()))
    for r in registros:
        if r.estado == "resumen con forma de fuente":
            print(f"  {r.ruta}\t{r.ficha}\tcitada en {len(r.citado_en)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
