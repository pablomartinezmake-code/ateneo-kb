#!/usr/bin/env python3
"""Censa las fichas de fuente de 02_wiki: qué forma tienen y si hay texto detrás.

Hasta el 2026-09-17 las fichas no decían si eran un libro o un artículo, y la
cifra sólo se podía adivinar por la forma de la cita (cursiva o comillas). Ese
día se escribió ``forma:`` en las 558 fichas —propuesta por la cita, dudosas
decididas leyendo la ficha— y nace este censo para que la cifra salga de una
orden. Caso que lo pidió: dar en un currículum cuántos libros y artículos hay.

Sólo lee. No escribe en ninguna ficha ni en 01_raw.

Criterio:
- ``forma`` (valor cerrado): libro · capitulo (texto breve que forma parte de un
  volumen: capítulo, ensayo, cuento, conferencia recogida, aunque se tenga
  suelto) · articulo (revista académica, actas, preprint) · prensa (periódico o
  revista general) · web (blog, boletín, página, publicación en red) ·
  audiovisual · informe (institucional, técnico, guía, informe generado) · tesis
  · inedito (borrador, documento interno, comentario circulado) · otro.
- ``texto`` se mira en el frontmatter, que es el contrato (AGENTS.md, «Ingerir una fuente», paso 4):
  ``texto`` = alguna ruta a un ``.md`` de 01_raw que existe fuera de
  ``reference-links/``; ``solo original`` = el PDF/EPUB/HTML existe pero no su
  ``.md``; ``solo rastro`` = sólo ``reference-links/``; ``ruta rota`` = declara
  01_raw y no existe; ``sin ruta`` = no declara nada en 01_raw. Si el frontmatter
  no lleva al texto pero el cuerpo enlaza un ``.md`` de 01_raw que existe (fichas
  antiguas con «Enlace (Local)»), el estado es ``texto en el cuerpo``: el texto
  está, la trazabilidad no cumple el contrato.
- ``texto`` dice que hay un ``.md`` detrás, no que esté completo ni que sea fiel:
  eso lo miden ``censo_ingestas.py`` y ``text_layer_fidelity``.
- Las fichas con ``superado_por`` no cuentan en los totales (son alias).
- ``posibles duplicados``: misma forma, mismo tomo y mismo título normalizado (o uno es el otro sin subtítulo). Los inéditos no se comparan: sus versiones conviven a propósito (2026-09-17, un artículo en curso).
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import re
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = REPO_ROOT / "01_raw"
FUENTES = REPO_ROOT / "02_wiki" / "fuentes"

FORMAS = ("libro", "capitulo", "articulo", "prensa", "web", "audiovisual", "informe", "tesis", "inedito", "otro")
RUTA_RAW = re.compile(r"(?:\.\./)*(01_raw/[^\r\n'\"\]|)>]+)")
WIKILINK = re.compile(r"\[\[([^\]|#]+)")
TITULO_CURSIVA = re.compile(r"\*([^*\s][^*]{3,}?)\*")
TITULO_COMILLAS = re.compile(r"[\"“«]([^\"”»]{4,})[\"”»]")


@dataclass
class Ficha:
    slug: str
    forma: str
    texto: str
    superada: bool
    titulo: str = ""
    rutas_cuerpo: list[str] = field(default_factory=list)
    tomo: int | None = None


def partir(t: str) -> tuple[str, str]:
    if not t.startswith("---"):
        return "", t
    fin = t.find("\n---", 3)
    return (t[:fin], t[fin + 4:]) if fin != -1 else ("", t)


def es_fuente(fm: str) -> bool:
    return re.search(r"^tipo:\s*fuente\s*$", fm, re.M) is not None


def campo(fm: str, nombre: str) -> str:
    m = re.search(rf"^{nombre}:[ \t]*(.*?)\s*$", fm, re.M)
    return m.group(1).strip().strip("'\"") if m else ""


def rutas_raw(texto: str, raiz: Path) -> list[Path]:
    """Rutas a 01_raw dentro de prosa o enlaces markdown (se cortan en paréntesis)."""
    return [raiz / m.group(1).strip().rstrip(".,;") for m in RUTA_RAW.finditer(texto)]


def rutas_frontmatter(fm: str, raiz: Path) -> list[Path]:
    """En el frontmatter la ruta ocupa el resto de la línea: los PDF llevan paréntesis en el nombre."""
    rutas = []
    for linea in fm.splitlines():
        i = linea.find("01_raw/")
        if i != -1:
            rutas.append(raiz / linea[i:].strip().rstrip("'\""))
    return rutas


def estado_texto(fm: str, raiz: Path, md_por_slug: dict[str, Path]) -> str:
    rutas = rutas_frontmatter(fm, raiz)
    enlazados = [md_por_slug[w.strip()] for w in WIKILINK.findall(fm) if w.strip() in md_por_slug]
    if enlazados or any(p.suffix == ".md" and p.is_file() and "reference-links" not in p.parts for p in rutas):
        return "texto"
    if any(p.is_file() and "reference-links" not in p.parts for p in rutas):
        return "solo original"
    if rutas and all("reference-links" in p.parts for p in rutas):
        return "solo rastro"
    if rutas:
        return "ruta rota" if not any(p.exists() for p in rutas) else "solo rastro"
    return "sin ruta"


def normalizar(titulo: str) -> str:
    t = unicodedata.normalize("NFD", titulo.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def titulo_de(cita: str) -> str:
    """El título es lo primero que aparece: en cursiva (obra entera) o entre comillas (parte).
    Si va entre comillas, la cursiva que sigue es la revista o el volumen, no el título."""
    cursiva = TITULO_CURSIVA.search(cita)
    comillas = TITULO_COMILLAS.search(cita)
    if comillas and (not cursiva or comillas.start() < cursiva.start()):
        return comillas.group(1)
    return cursiva.group(1) if cursiva else ""


def leer(p: Path, raiz: Path, md_por_slug: dict[str, Path]) -> Ficha | None:
    t = p.read_bytes().decode("utf-8", errors="replace")
    fm, cuerpo = partir(t)
    if not es_fuente(fm):
        return None
    cita = re.search(r"^## Cita[^\n]*\n+\s*(?:>\s*)?(.+)", cuerpo, re.M)
    cuerpo_md = [q for q in rutas_raw(cuerpo, raiz)
                 if q.suffix == ".md" and q.is_file() and "reference-links" not in q.parts]
    texto = estado_texto(fm, raiz, md_por_slug)
    if texto in ("sin ruta", "ruta rota", "solo rastro") and cuerpo_md:
        texto = "texto en el cuerpo"
    return Ficha(
        slug=p.stem,
        forma=campo(fm, "forma"),
        texto=texto,
        superada=bool(campo(fm, "superado_por")),
        titulo=normalizar(titulo_de(cita.group(1))) if cita else "",
        rutas_cuerpo=[q.relative_to(raiz).as_posix() for q in rutas_raw(cuerpo, raiz) if q.is_file()],
        tomo=tomo_de(cita.group(1)) if cita else None,
    )


def indice_raw(raiz: Path) -> dict[str, Path]:
    raw = raiz / "01_raw"
    return {q.stem: q for q in raw.rglob("*.md") if "reference-links" not in q.relative_to(raw).parts}


def censar(raiz: Path = REPO_ROOT) -> list[Ficha]:
    md = indice_raw(raiz)
    fichas = (leer(p, raiz, md) for p in sorted((raiz / "02_wiki" / "fuentes").glob("*.md")))
    return [f for f in fichas if f is not None]


TOMO = re.compile(r"\b(?:Vol\.|vol\.|Volume|Tome|tome|Tomo|tomo|Band|Libro|Book)\s*([IVXLCDM]+|\d+)\b")
ROMANOS = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def tomo_de(cita: str) -> int | None:
    """Número de tomo o volumen de la cita (romano o arábigo), o None."""
    m = TOMO.search(cita)
    if not m:
        return None
    s = m.group(1)
    if s.isdigit():
        return int(s)
    total = 0
    for i, c in enumerate(s):
        v = ROMANOS[c]
        total += -v if i + 1 < len(s) and ROMANOS[s[i + 1]] > v else v
    return total


def mismo_titulo(a: str, b: str) -> bool:
    """Igual, o uno empieza por el otro y el corto tiene al menos tres palabras (título sin subtítulo)."""
    corto, largo = sorted((a, b), key=len)
    return a == b or (largo.startswith(corto + " ") and len(corto.split()) >= 3)


def duplicados(fichas: list[Ficha]) -> list[list[Ficha]]:
    grupos: list[list[Ficha]] = []
    for f in fichas:
        # los inéditos son versiones de un mismo borrador a propósito (puntero estable + una ficha por versión)
        if not f.titulo or f.superada or f.forma == "inedito":
            continue
        for g in grupos:
            if g[0].forma == f.forma and g[0].tomo == f.tomo and mismo_titulo(g[0].titulo, f.titulo):
                g.append(f)
                break
        else:
            grupos.append([f])
    return [g for g in grupos if len(g) > 1]


def informe(fichas: list[Ficha], instante: str, head: str) -> str:
    vivas = [f for f in fichas if not f.superada]
    por_forma = collections.Counter(f.forma or "(sin forma)" for f in vivas)
    cruce = collections.Counter((f.forma or "(sin forma)", f.texto) for f in vivas)
    estados = ["texto", "texto en el cuerpo", "solo original", "solo rastro", "ruta rota", "sin ruta"]
    lineas = [
        f"# Censo de fichas de fuente — {instante[:10]}", "",
        f"- Instante: `{instante}` · HEAD `{head}` · máquina: esta copia de trabajo.",
        f"- Población: {len(fichas)} fichas `tipo: fuente` en `02_wiki/fuentes/`; "
        f"{len(fichas) - len(vivas)} con `superado_por` fuera de los totales.",
        "- Orden: `python 04_ops/scripts/censo_fuentes.py --report <ruta>`.", "",
        "## Forma y texto", "",
        "| forma | fichas | " + " | ".join(estados) + " |",
        "|---|---:|" + "---:|" * len(estados),
    ]
    for forma, n in por_forma.most_common():
        lineas.append(f"| {forma} | {n} | " + " | ".join(str(cruce[(forma, e)]) for e in estados) + " |")
    lineas += ["", "`texto` quiere decir que hay un `.md` en 01_raw detrás, no que esté completo ni que sea fiel.", ""]

    malas = [f for f in fichas if f.forma not in FORMAS]
    lineas += [f"## Sin forma válida ({len(malas)})", ""] + [f"- `{f.slug}` — `{f.forma or '(vacía)'}`" for f in malas] + [""]

    for estado in ("sin ruta", "ruta rota", "solo rastro", "solo original", "texto en el cuerpo"):
        grupo = [f for f in vivas if f.texto == estado and f.forma in ("libro", "capitulo", "articulo")]
        lineas += [f"## Libros, capítulos y artículos en estado «{estado}» ({len(grupo)})", ""]
        for f in grupo:
            pista = f" · ruta en el cuerpo: `{f.rutas_cuerpo[0]}`" if f.rutas_cuerpo else ""
            lineas.append(f"- `{f.slug}` ({f.forma}){pista}")
        lineas.append("")

    dups = duplicados(fichas)
    lineas += [f"## Posibles duplicados ({len(dups)})", ""]
    for g in dups:
        lineas.append(f"- {g[0].forma}: " + " · ".join(f"`{f.slug}` ({f.texto})" for f in g))
    lineas += ["", "## Límites", "",
               "- El duplicado se detecta por título normalizado y tomo: dos ediciones o traducciones "
               "distintas de la misma obra salen aquí y pueden ser legítimas.",
               "- La forma se escribió a mano el 2026-09-17; una ficha nueva sin `forma:` no entra en commit "
               "(candado `--staged`).", ""]
    return "\n".join(lineas)


def rutas_staged() -> list[Path]:
    salida = subprocess.run(
        ["git", "-c", "core.quotepath=off", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "--",
         "02_wiki/fuentes"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    return [REPO_ROOT / l.strip() for l in salida.splitlines() if l.strip().endswith(".md")]


def sin_forma_valida(rutas: list[Path]) -> list[tuple[str, str]]:
    malas = []
    for p in rutas:
        if not p.is_file():
            continue
        fm, _ = partir(p.read_bytes().decode("utf-8", errors="replace"))
        if es_fuente(fm) and campo(fm, "forma") not in FORMAS:
            malas.append((p.relative_to(REPO_ROOT).as_posix() if p.is_relative_to(REPO_ROOT) else p.name,
                          campo(fm, "forma")))
    return malas


def candado_staged() -> int:
    malas = sin_forma_valida(rutas_staged())
    if not malas:
        return 0
    print("ABORTADO: fichas de fuente sin `forma:` válida (AGENTS.md, «Ingerir una fuente», paso 4):")
    for ruta, valor in malas:
        print(f"  {ruta}  [forma: {valor or '(falta)'}]")
    print("Valores: " + " | ".join(FORMAS))
    return 1


class Candado(unittest.TestCase):
    def vault(self, tmp: Path, fichas: dict[str, str], raws: dict[str, str]) -> Path:
        for rel, contenido in raws.items():
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp / rel).write_text(contenido, encoding="utf-8")
        (tmp / "02_wiki" / "fuentes").mkdir(parents=True, exist_ok=True)
        for slug, fm in fichas.items():
            (tmp / "02_wiki" / "fuentes" / f"{slug}.md").write_bytes(fm.encode("utf-8"))
        return tmp

    def test_estados_de_texto(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            raiz = self.vault(Path(d), {
                "a": "---\ntipo: fuente\nforma: libro\nfuentes:\n  - \"01_raw/books/a.md\"\n---\n",
                "b": "---\r\ntipo: fuente\r\nforma: libro\r\nfuentes:\r\n  - ../01_raw/books/b.md\r\n---\r\n",
                "c": "---\ntipo: fuente\nforma: articulo\nfuentes:\n  - 01_raw/pdf/c (copia).pdf\n---\n",
                "d": "---\ntipo: fuente\nforma: articulo\nfuentes:\n  - 01_raw/reference-links/d.md\n---\n",
                "e": "---\ntipo: fuente\nforma: libro\nfuentes: []\n---\n\n[x](../../01_raw/books/e.md)\n",
                "f": "---\ntipo: fuente\nforma: web\nfuentes:\n- '[[f]]'\n---\n",
            }, {"01_raw/books/a.md": "x", "01_raw/books/b.md": "x", "01_raw/pdf/c (copia).pdf": "x",
                "01_raw/reference-links/d.md": "x", "01_raw/books/e.md": "x", "01_raw/f.md": "x"})
            estados = {f.slug: f.texto for f in censar(raiz)}
            self.assertEqual(estados, {"a": "texto", "b": "texto", "c": "solo original",
                                       "d": "solo rastro", "e": "texto en el cuerpo", "f": "texto"})
            self.assertEqual({f.slug: f.rutas_cuerpo for f in censar(raiz)}["e"], ["01_raw/books/e.md"])

    def test_staged_acusa_ficha_sin_forma(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            raiz = self.vault(Path(d), {
                "buena": "---\ntipo: fuente\nforma: capitulo\n---\n",
                "sin": "---\ntipo: fuente\nfuentes: []\n---\n",
                "rara": "---\r\ntipo: fuente\r\nforma: paper\r\n---\r\n",
                "concepto": "---\ntipo: concepto\n---\n",
            }, {})
            fu = raiz / "02_wiki" / "fuentes"
            malas = sin_forma_valida([fu / f"{s}.md" for s in ("buena", "sin", "rara", "concepto")])
            self.assertEqual(sorted(v for _, v in malas), ["", "paper"])

    def test_duplicado_por_titulo_normalizado(self) -> None:
        fichas = [Ficha("x", "libro", "texto", False, normalizar("Una teoría crítica de la IA")),
                  Ficha("y", "libro", "sin ruta", False, normalizar("Una teoria critica de la IA: ensayo")),
                  Ficha("z", "articulo", "texto", False, normalizar("Una teoría crítica de la IA")),
                  Ficha("w", "libro", "texto", False, normalizar("Una teoría"))]
        self.assertEqual([[f.slug for f in g] for g in duplicados(fichas)], [["x", "y"]])

    def test_tomos_distintos_e_ineditos_no_son_duplicados(self) -> None:
        self.assertEqual(tomo_de("Foucault. *Dits et écrits*. Tome II, *1970–1975*."), 2)
        self.assertEqual(tomo_de("Foucault. *Dits et écrits, 1954–1988*. Vol. 4, *1980–1988*."), 4)
        titulo = normalizar("Dits et écrits")
        fichas = [Ficha("i", "libro", "texto", False, titulo, tomo=1), Ficha("ii", "libro", "texto", False, titulo, tomo=2),
                  Ficha("ii-bis", "libro", "texto", False, titulo, tomo=2),
                  Ficha("v10", "inedito", "sin ruta", False, "un articulo"), Ficha("paper", "inedito", "sin ruta", False, "un articulo")]
        self.assertEqual([[f.slug for f in g] for g in duplicados(fichas)], [["ii", "ii-bis"]])

    def test_titulo_entre_comillas_no_es_la_revista(self) -> None:
        self.assertEqual(titulo_de('Kalai, A. 2026. "Evaluating LLMs." *Nature* 653.'), "Evaluating LLMs.")
        self.assertEqual(titulo_de("Han, B.-C. 2021. *No-cosas*. Madrid: Taurus."), "No-cosas")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--staged", action="store_true",
                    help="candado pre-commit: aborta (exit 1) si una ficha de fuente preparada no tiene forma válida")
    args = ap.parse_args()

    if args.staged:
        return candado_staged()
    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Candado)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1

    fichas = censar()
    if args.report:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True).stdout.strip()
        instante = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        destino = args.report if args.report.is_absolute() else REPO_ROOT / args.report
        destino.write_text(informe(fichas, instante, head), encoding="utf-8", newline="\n")
        print(destino)
    vivas = [f for f in fichas if not f.superada]
    print(f"fichas {len(fichas)} · vivas {len(vivas)} · sin forma válida {sum(f.forma not in FORMAS for f in fichas)}")
    for forma, n in collections.Counter(f.forma for f in vivas).most_common():
        textos = collections.Counter(f.texto for f in vivas if f.forma == forma)
        print(f"  {forma}: {n} · " + " · ".join(f"{k} {v}" for k, v in textos.most_common()))
    print(f"  posibles duplicados: {len(duplicados(fichas))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
