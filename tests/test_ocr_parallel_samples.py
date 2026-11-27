"""Regression tests for bounded OCR jobs and representative PDF samples."""
import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ingest_to_raw as ingest


def test_parallel_failure_and_resume(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("runner", ROOT / "04_ops/scripts/ocr_tesseract.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    source = tmp_path / "blank.pdf"
    with pymupdf.open() as doc:
        for _ in range(3):
            doc.new_page(width=50, height=50)
        doc.save(source)
    output = tmp_path / "out.md"
    output.write_bytes(b"previous")
    cmd = [str(source), str(output), "--workers", "2", "--lang", "eng"]
    failed = threading.Event()
    calls = []

    def fake_ocr(command, **kwargs):
        if "--list-langs" in command:
            return subprocess.CompletedProcess(command, 0, "languages:\neng\n")
        number = int(Path(command[1]).stem[1:])
        calls.append(number)
        assert kwargs["env"]["OMP_THREAD_LIMIT"] == "1"
        if number == 1:
            assert failed.wait(5)  # Page 2 finishes first.
        if number == 2 and not failed.is_set():
            failed.set()
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, f"text {number}\n")

    monkeypatch.setattr(runner.subprocess, "run", fake_ocr)
    with pytest.raises(subprocess.CalledProcessError):
        runner.main(cmd)
    assert output.read_bytes() == b"previous"
    assert not list(tmp_path.rglob("p0002.txt"))
    assert not list(tmp_path.rglob("*.png"))
    runner.main(cmd)
    assert calls.count(1) == 1 and calls.count(2) == 2
    assert output.read_text(encoding="utf-8") == "\n".join(
        f"<!-- pdf_page: {n:03d} -->\n\ntext {n}\n" for n in range(1, 4)
    )
    with pytest.raises(SystemExit):
        runner.main([*cmd, "--workers", "0"])


def test_fallback_sample_matches_saved_ocr_and_console(tmp_path, monkeypatch, capsys):
    source = tmp_path / "scan.pdf"
    with pymupdf.open() as doc:
        for _ in range(20):
            doc.new_page()
        doc.save(source)
    pages = [f"OCR sheet {n}" if n != 11 else "" for n in range(1, 21)]
    markdown = "\n".join(f"<!-- pdf_page: {n:03d} -->\n\n{s}\n" for n, s in enumerate(pages, 1))
    monkeypatch.setattr(ingest, "convert_with_tesseract", lambda *args: markdown)
    monkeypatch.setattr(sys, "argv", ["ingest", str(source), "--out", str(tmp_path / "out"),
                                      "--skip-original-copy", "--write-meta"])
    ingest.main()
    console = capsys.readouterr().out
    meta = json.loads((tmp_path / "out/scan.meta.json").read_text(encoding="utf-8"))
    assert meta["muestra_hojas"] == [
        {"pagina_pdf": n, "renglon": pages[n - 1] or "(sin texto OCR)"} for n in (3, 7, 11, 15, 19)
    ]
    for item in meta["muestra_hojas"]:
        assert f"hoja {item['pagina_pdf']:3d}/20: {item['renglon']}" in console
    with pytest.raises(ValueError, match="anclas"):
        ingest.muestra_ocr(markdown.replace("011 -->", "012 -->"), 20)


def test_sample_skips_repeated_lines_but_preserves_original_layer():
    texts = [f"Header {n % 3}\nSection\nBody {n}\nFooter" for n in range(1, 21)]
    texts[18] = "Header 1\nFooter"
    original = texts.copy()
    sample, total = ingest.leer_muestra(None, texts, {"Section", "Header 0", "Footer"})
    assert total == 20
    assert [item["renglon"] for item in sample] == [
        "Body 3", "Body 7", "Body 11", "Body 15", "(solo líneas repetidas)"
    ]
    assert texts == original
