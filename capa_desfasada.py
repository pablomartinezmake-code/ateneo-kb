"""Contrast rendered PDF sheets with their own and adjacent text layers."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import re
import shutil
import subprocess

import pymupdf


def words(text):
    return set(re.findall(r"[^\W\d_]{2,}", text.casefold()))


def _record_ocr(result, row, layers, future):
    n = row["imagen"]
    try:
        proc = future.result()
    except (OSError, subprocess.SubprocessError) as exc:
        error = str(exc)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            error = exc.stderr.decode("utf-8", "replace").strip() or error
        result.setdefault("error", error)
        row["omitida"] = "OCR no disponible"
        return
    tokens = words(proc.stdout.decode("utf-8", "replace"))
    if len(tokens) < 6:
        row["omitida"] = "OCR insuficiente"
        return
    # Dice overlap tolerates differences in OCR reading order.
    scores = {i: 2 * len(tokens & layer) / (len(tokens) + len(layer))
              for i, layer in layers.items()}
    row["propia"] = round(scores[n], 3)
    row["vecinas"] = {str(i): round(s, 3) for i, s in scores.items() if i != n}
    ranked = sorted(scores, key=scores.get, reverse=True)
    best = ranked[0]
    if best != n and scores[best] >= .45 and scores[best] - scores[ranked[1]] >= .2:
        row["desplazamiento"] = best - n
    elif scores[n] < .12 and len(tokens) >= 15:
        row["discrepancia"] = True


def evaluar(path, hojas, lengua, textos=None):
    """Sheet numbers are one-based. Offsets are layer minus image."""
    rows = []
    result = {"estado": "muestra sin texto comparable", "lengua": lengua, "hojas": rows}
    executable = shutil.which("tesseract") or r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    env = os.environ.copy()
    local = Path(__file__).resolve().parent / "04_ops/tessdata-local"
    if local.is_dir() and all((local / f"{lang}.traineddata").is_file() for lang in lengua.split("+")):
        env.setdefault("TESSDATA_PREFIX", str(local))
    with pymupdf.open(path) as doc:
        sheets = dict.fromkeys(hojas)
        for n in sheets:
            if not 1 <= n <= len(doc):
                raise ValueError(f"Hoja {n} fuera de 1–{len(doc)}")
        jobs = []
        for n in sheets:
            layers = {i: words(doc[i - 1].get_text() if textos is None else textos[i - 1]) for i in range(
                max(1, n - 1), min(len(doc), n + 1) + 1)}
            row = {"imagen": n}
            rows.append(row)
            if len(layers[n]) < 6:
                row["omitida"] = "capa insuficiente"
                continue
            jobs.append((row, layers))

        if jobs:
            workers = min(5, len(jobs), os.cpu_count() or 1)
            if workers > 1:
                env["OMP_THREAD_LIMIT"] = "1"
            command = [executable, "stdin", "stdout", "-l", lengua]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                # Bound the queued PNGs even for a large explicit sample.
                for start in range(0, len(jobs), workers):
                    pending = []
                    for row, layers in jobs[start:start + workers]:
                        page = doc[row["imagen"] - 1]
                        scale = min(2.5, 3200 / max(page.rect.width, page.rect.height))
                        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY)
                        future = pool.submit(subprocess.run, command, input=pix.tobytes("png"),
                                             capture_output=True, timeout=20, env=env, check=True)
                        pending.append((row, layers, future))
                    for row, layers, future in pending:
                        _record_ocr(result, row, layers, future)
    if any("desplazamiento" in row for row in rows):
        result["estado"] = "capa desfasada"
    elif any(row.get("discrepancia") for row in rows):
        result["estado"] = "posible capa desfasada, desplazamiento indeterminado"
    elif "error" in result:
        result["estado"] = "no se pudo evaluar"
    elif any("propia" in row for row in rows):
        result["estado"] = "sin indicios en la muestra"
    return result


def aviso(result):
    details = []
    for row in result.get("hojas", []):
        n = row["imagen"]
        if "desplazamiento" in row:
            shift = row["desplazamiento"]
            details.append(f"imagen {n} -> capa {n + shift} ({shift:+d})")
        elif row.get("discrepancia"):
            details.append(f"imagen {n} sin correspondencia clara")
    if result.get("error"):
        details.append(result["error"])
    return "Alineación de la capa: " + ". ".join([result["estado"], *details])
