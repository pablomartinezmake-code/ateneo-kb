"""
Deep OCR extraction for Ateneo-KB.
Uses PyMuPDF (fitz) for page rendering and RapidOCR for text extraction.

The extraction is checkpointed per page region so a long scanned book can be
resumed without repeating completed OCR work. Landscape scans are treated as
two-page spreads and read left-to-right.
"""

import os
import sys
from pathlib import Path


RENDER_SCALE = 2


def _cache_dir(pdf_path: Path) -> Path:
    return pdf_path.with_suffix(".rapidocr-cache")


def _write_atomic(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _page_regions(page, fitz):
    """Return one portrait region or the two halves of a landscape spread."""
    rect = page.rect
    if rect.width <= rect.height:
        return [("", rect)]

    middle = rect.x0 + rect.width / 2
    return [
        ("izquierda", fitz.Rect(rect.x0, rect.y0, middle, rect.y1)),
        ("derecha", fitz.Rect(middle, rect.y0, rect.x1, rect.y1)),
    ]

def convert_pdf_to_markdown_ocr(pdf_path: str) -> str:
    """
    Renders PDF pages to images and performs OCR to extract text.
    Returns a unified Markdown string with page headers.
    """
    try:
        import fitz
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return (
            "Error: Dependencias de OCR no encontradas. "
            "Ejecuta: pip install pymupdf rapidocr-onnxruntime"
        )

    source = Path(pdf_path)
    cache_dir = _cache_dir(source)
    cache_dir.mkdir(parents=True, exist_ok=True)

    engine = RapidOCR()
    doc = fitz.open(source)
    full_text = []
    failures = []

    start_page = max(int(os.environ.get("RAPIDOCR_START_PAGE", "1")) - 1, 0)
    end_page = min(int(os.environ.get("RAPIDOCR_END_PAGE", str(len(doc)))), len(doc))
    if start_page >= end_page:
        raise ValueError(
            f"Rango OCR invalido: {start_page + 1}-{end_page} para {len(doc)} paginas"
        )

    print(
        "Iniciando OCR profundo reanudable "
        f"(paginas PDF {start_page + 1}-{end_page} de {len(doc)})...",
        file=sys.stderr,
    )

    for i in range(start_page, end_page):
        page = doc.load_page(i)
        regions = _page_regions(page, fitz)

        for region_index, (label, clip) in enumerate(regions):
            suffix = f"-{label}" if label else ""
            cache_path = cache_dir / f"page-{i + 1:04d}{suffix}.md"

            if cache_path.exists():
                full_text.append(cache_path.read_text(encoding="utf-8"))
                continue

            header = f"## Página PDF {i + 1}"
            if label:
                header += f" · {label}"

            try:
                pix = page.get_pixmap(
                    matrix=fitz.Matrix(RENDER_SCALE, RENDER_SCALE),
                    clip=clip,
                    alpha=False,
                )
                result, _ = engine(pix.tobytes("png"))
                if result:
                    region_text = "\n".join(line[1] for line in result)
                else:
                    region_text = "[Página vacía o sin texto detectable]"

                page_markdown = f"{header}\n\n{region_text}"
                _write_atomic(cache_path, page_markdown)
                full_text.append(page_markdown)
            except Exception as exc:
                failures.append(f"{i + 1}{suffix}: {exc}")
                print(
                    f"Error OCR en página {i + 1}{suffix}: {exc}",
                    file=sys.stderr,
                )

        print(
            f"Procesada página PDF {i + 1}/{len(doc)} "
            f"({len(list(cache_dir.glob('page-*.md')))} regiones en caché)...",
            file=sys.stderr,
        )

    doc.close()

    if failures:
        raise RuntimeError(
            "OCR incompleto; la caché conserva las regiones correctas. "
            + " | ".join(failures)
        )

    return "\n\n".join(full_text)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python rapidocr_pdf.py <archivo.pdf>")
        sys.exit(1)
    
    print(convert_pdf_to_markdown_ocr(sys.argv[1]))
