"""¿Este PDF salió de una imprenta o lo fabricó un conversor desde un libro electrónico?

Un PDF que calibre (u otro conversor) hizo desde un EPUB pagina a su manera: sus
páginas pueden diferir de las impresas aunque el EPUB conserve marcas de folio.

Lo dice el propio PDF en sus metadatos (`Producer` / `Creator`). Es un indicio, no
una prueba: hay PDF de calibre que sí conservan el folio impreso. Por eso la ficha
dice «leer el folio en la imagen», no «es falso».

    python productor_pdf.py carpeta_o_pdf [...]   # censo
"""
from __future__ import annotations

import sys
from pathlib import Path

# Conversores que paginan por su cuenta al pasar un libro electrónico a PDF. Prince,
# pandoc o wkhtmltopdf quedan fuera: con ellos hacen las editoriales su PDF oficial
# (censo del 2026-10-13: un informe web hecho con Prince, sin edición impresa).
CONVERSORES = ("calibre", "ebook-convert")

# «Probable», no «es»: un PDF hecho con calibre puede traer el folio impreso visible
# y coincidir con la página del PDF (comprobado en tres anclas de un caso real).
AVISO = ("paginación probablemente no impresa: PDF fabricado por {conversor}; antes de "
         "citar una página, leer el folio en la imagen o citar por el EPUB")


def leer_productor(pdf: Path) -> dict:
    import pymupdf

    with pymupdf.open(pdf) as doc:
        meta = doc.metadata or {}
    return {"producer": (meta.get("producer") or "").strip(),
            "creator": (meta.get("creator") or "").strip()}


def conversor(productor: dict) -> str | None:
    """El conversor que nombran Producer o Creator, o None si no nombran ninguno."""
    for campo in ("producer", "creator"):
        valor = productor.get(campo, "")
        if any(c in valor.lower() for c in CONVERSORES):
            return valor
    return None


def evaluar(pdf: Path) -> dict:
    """Lo que va a la ficha: los metadatos tal cual y, si hay conversor, el aviso."""
    productor = leer_productor(pdf)
    hecho_por = conversor(productor)
    out = {"producer": productor["producer"], "creator": productor["creator"],
           "paginacion_impresa": hecho_por is None}
    if hecho_por:
        out["aviso"] = AVISO.format(conversor=hecho_por)
    return out


def main(argv: list[str]) -> int:
    pdfs: list[Path] = []
    for arg in argv:
        p = Path(arg)
        pdfs += sorted(p.rglob("*.pdf")) if p.is_dir() else [p]
    marcados = 0
    for pdf in pdfs:
        try:
            r = evaluar(pdf)
        except Exception as exc:  # un PDF roto no para el censo
            print(f"ERROR\t{pdf}\t{exc}")
            continue
        if not r["paginacion_impresa"]:
            marcados += 1
            print(f"CONVERSOR\t{pdf}\t{r['producer'] or r['creator']}")
    print(f"{marcados} de {len(pdfs)} PDF fabricados por un conversor")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
