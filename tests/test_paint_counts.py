"""Differential checks against the image and text APIs that define the detector."""
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capa_heredada import evaluar


def test_rendered_pages_match_reference(tmp_path):
    path = tmp_path / "mixed.pdf"
    with pymupdf.open() as doc:
        plain = doc.new_page(width=100, height=100)
        for mode in range(8):
            plain.insert_text((8, 10 + 10 * mode), "text", render_mode=mode)
        inline = doc.new_page(width=100, height=100)
        stream = doc.get_new_xref()
        doc.update_object(stream, "<<>>")
        doc.update_stream(stream, b"q 100 0 0 100 0 0 cm BI /W 1 /H 1 /BPC 8 /CS /G ID \x80 EI Q")
        inline.set_contents(stream)
        assert not inline.get_images()
        inline.insert_text((8, 20), "OCR", render_mode=3)
        with pymupdf.open(stream=doc.tobytes(), filetype="pdf") as source:
            form = doc.new_page(width=100, height=100)
            form.show_pdf_page(form.rect, source, 1)
            assert form.get_xobjects() and not form.get_images()
        doc.new_page()
        rotated = doc.new_page(width=100, height=100)
        rotated.insert_text((8, 20), "rotated")
        rotated.set_rotation(90)
        doc.save(path)

    result = evaluar(path, textos=[""] * 5)
    with pymupdf.open(path) as doc:
        for page, row in zip(doc, result["hojas"], strict=True):
            spans = page.get_texttrace()
            assert row["spans"] == len(spans)
            assert row["spans_invisibles"] == sum(s["type"] == 3 for s in spans)
            assert row["cobertura_maxima"] == (1 if page.get_image_info() else 0)
    assert [r["cobertura_maxima"] for r in result["hojas"]] == [0, 1, 1, 0, 0]
    assert result["hojas"][1]["spans_invisibles"] == 1
