import os
import subprocess
import threading
from types import SimpleNamespace
from pathlib import Path
import sys

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def sample(tmp_path, monkeypatch):
    path = tmp_path / "sample.pdf"
    with pymupdf.open() as doc:
        for line in ("oak ash maple beech hazel willow", "", "moon mars venus saturn earth jupiter",
                     "pear plum peach apple mango lemon"):
            doc.new_page().insert_text((30, 40), line)
        doc.save(path)

    rendered = []

    def render(page, **kwargs):
        assert threading.current_thread() is threading.main_thread()
        sheet = page.number + 1
        rendered.append(sheet)
        return SimpleNamespace(tobytes=lambda fmt: bytes([sheet]))

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", render)
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    return path, rendered


def test_parallel_order_and_omp(sample, monkeypatch):
    from capa_desfasada import evaluar

    path, rendered = sample
    overlap = threading.Barrier(2, timeout=4)
    later_done = threading.Event()
    monkeypatch.setenv("OMP_THREAD_LIMIT", "8")

    def recognize(cmd, *, input, env, **kwargs):
        overlap.wait()
        assert env["OMP_THREAD_LIMIT"] == "1"
        if input == bytes([3]):
            assert later_done.wait(4)
            output = b"moon mars venus saturn earth jupiter"
        else:
            later_done.set()
            output = b"oak ash maple beech hazel willow"
        return subprocess.CompletedProcess(cmd, 0, stdout=output)

    monkeypatch.setattr(subprocess, "run", recognize)
    result = evaluar(path, [3, 2, 1, 3], "eng")
    assert rendered == [3, 1]
    assert result["hojas"] == [
        {"imagen": 3, "propia": 1.0, "vecinas": {"2": 0.0, "4": 0.0}},
        {"imagen": 2, "omitida": "capa insuficiente"},
        {"imagen": 1, "propia": 1.0, "vecinas": {"2": 0.0}},
    ]


@pytest.mark.parametrize("failure, message", [
    (OSError("missing executable"), "missing executable"),
    (subprocess.CalledProcessError(1, "ocr", stderr=b"first failure\n"), "first failure"),
    (subprocess.CalledProcessError(1, "ocr"), "returned non-zero"),
    (subprocess.TimeoutExpired("ocr", 20), "timed out"),
])
def test_first_error_in_sheet_order(sample, monkeypatch, failure, message):
    from capa_desfasada import evaluar

    path, _ = sample
    later_failed = threading.Event()

    def recognize(cmd, *, input, **kwargs):
        if input == bytes([3]):
            assert later_failed.wait(4)
            raise failure
        if input == bytes([1]):
            later_failed.set()
            raise OSError("later failure")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"moon mars venus saturn earth jupiter")

    monkeypatch.setattr(subprocess, "run", recognize)
    result = evaluar(path, [3, 1, 4], "eng")
    assert message in result["error"]
    assert result["hojas"][:2] == [{"imagen": n, "omitida": "OCR no disponible"} for n in [3, 1]]
    assert result["hojas"][2]["desplazamiento"] == -1
    assert result["estado"] == "capa desfasada"


def test_bounds_checked_before_rendering(sample, monkeypatch):
    from capa_desfasada import evaluar

    path, rendered = sample
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("unexpected OCR"))
    with pytest.raises(ValueError, match="Hoja 5"):
        evaluar(path, [1, 5], "eng")
    assert rendered == []
