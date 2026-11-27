"""A single PDF layer must yield identical anchors, folios and diagnostics."""
import subprocess

import pymupdf
import pytest

import capa_desfasada
import capa_heredada
import ingest_to_raw as ingest


@pytest.fixture
def witness(tmp_path):
    path = tmp_path / "folios.pdf"
    with pymupdf.open() as doc:
        for n in range(1, 8):
            page = doc.new_page()
            if n == 4:
                continue
            page.insert_text((50, 100), "  alpha beta gamma delta epsilon zeta  ")
            page.insert_text((290, 815), str(n))
            if n == 7:
                page.set_rotation(90)
        doc.save(path)
    return path


def test_shared_and_direct_textpages(witness, monkeypatch):
    with pymupdf.open(witness) as doc:
        direct = [page.get_text() for page in doc]
    chunks = [f"<!-- pdf_page: {n:03d} -->\n{text.strip()}"
              for n, text in enumerate(direct, 1) if text.strip()]
    calls = []
    original = pymupdf.Page.get_textpage

    def count_textpages(page, *args, **kwargs):
        calls.append(page.number)
        return original(page, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_textpage", count_textpages)
    texts = []
    markdown, model = ingest.convert_with_pdf_pages(witness, texts)
    if pymupdf.TEXTFLAGS_TEXT == pymupdf.TEXTFLAGS_WORDS:
        assert calls == list(range(len(direct)))
    assert texts == direct
    assert markdown == "\n".join(chunks)
    assert model["folio_candidato"] == ingest.folio_candidates(witness)
    assert model["folio_candidato"]["paginas"]
    monkeypatch.setattr(pymupdf, "TEXTFLAGS_WORDS", pymupdf.TEXTFLAGS_WORDS | pymupdf.TEXT_PRESERVE_IMAGES)
    assert ingest.convert_with_pdf_pages(witness) == (markdown, model)


def test_detectors_with_and_without_raw_text(witness, monkeypatch):
    with pymupdf.open(witness) as doc:
        texts = [page.get_text() for page in doc]
    assert ingest.leer_muestra(witness, texts) == ingest.leer_muestra(witness)
    direct = capa_heredada.evaluar(witness)
    cached = capa_heredada.evaluar(witness, texts)
    del direct["segundos"], cached["segundos"]
    assert cached == direct

    renders = []

    def ocr(command, **kwargs):
        renders.append(kwargs["input"])
        return subprocess.CompletedProcess(command, 0, stdout=b"alpha beta gamma delta epsilon zeta")

    monkeypatch.setattr(capa_desfasada.subprocess, "run", ocr)
    pages = [1, 2, 3, 5, 7]
    direct = capa_desfasada.evaluar(witness, pages, "eng")
    assert capa_desfasada.evaluar(witness, pages, "eng", texts) == direct
    assert len(renders) == 10 and renders[:5] == renders[5:]
