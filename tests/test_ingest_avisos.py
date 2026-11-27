"""Avisos de la ingesta: intérprete que falta, y cinco renglones del PDF.

Nacieron rojas contra 6b815e0. El bloqueo de un módulo se hace en un
subproceso: poner None en sys.modules del proceso de pytest rompería el
resto de la suite.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pymupdf

RAIZ = Path(__file__).resolve().parents[1]
INGEST = RAIZ / "ingest_to_raw.py"
RAW = RAIZ / "01_raw"
sys.path.insert(0, str(RAIZ))
import ingest_to_raw as ingest  # noqa: E402
INTERPRETE = ingest.INTERPRETE_CON_LECTURA_PDF


def pdf_con_hojas(tmp_path, hojas, nombre="libro.pdf"):
    """hojas: una lista de renglones por hoja. Lista vacía = hoja en blanco."""
    ruta = tmp_path / nombre
    doc = pymupdf.open()
    for renglones in hojas:
        pagina = doc.new_page(width=400, height=600)
        y = 80
        for renglon in renglones:
            pagina.insert_text((40, y), renglon, fontsize=10)
            y += 20
    doc.save(ruta)
    doc.close()
    return ruta


def bloquear_modulo(tmp_path, modulo, argv):
    lanzador = tmp_path / f"bloquear-{modulo}.py"
    # run_path de un .py no mete su carpeta en sys.path (runpy.py, rama
    # «Not a valid sys.path entry»). `python script.py` sí. Sin esto el
    # fallo es «no module named pdf2md» y no se llega al módulo bloqueado.
    lanzador.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "import runpy\n"
        f"sys.path.insert(0, str(Path({str(INGEST)!r}).resolve().parent))\n"
        f"sys.modules[{modulo!r}] = None\n"
        f"sys.argv = {argv!r}\n"
        f"runpy.run_path({str(INGEST)!r}, run_name='__main__')\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, str(lanzador)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def exigir_aviso(resultado, modulo):
    detalle = f"code={resultado.returncode}\nSTDERR:\n{resultado.stderr}\nSTDOUT:\n{resultado.stdout}"
    assert resultado.returncode == 1, detalle
    assert "Traceback" not in resultado.stderr, detalle
    assert modulo in resultado.stderr, detalle
    assert sys.executable in resultado.stderr, detalle
    assert INTERPRETE in resultado.stderr, detalle


def inventario_raw():
    vistos = set()

    def saltar(error):
        del error

    for carpeta, _dirnames, nombres in os.walk(RAW, onerror=saltar):
        for nombre in nombres:
            vistos.add(str(Path(carpeta) / nombre))
    return vistos


def test_sin_pymupdf4llm_avisa_el_interprete_y_no_hay_traceback(tmp_path):
    resultado = bloquear_modulo(tmp_path, "pymupdf4llm", ["ingest_to_raw.py", "cualquiera.pdf"])
    exigir_aviso(resultado, "pymupdf4llm")


def test_sin_markitdown_el_aviso_llega_al_convertir(tmp_path):
    fuente = tmp_path / "nota.txt"
    fuente.write_text("hola", encoding="utf-8")
    resultado = bloquear_modulo(
        tmp_path,
        "markitdown",
        [
            "ingest_to_raw.py",
            str(fuente),
            "--converter",
            "markitdown",
            "--out",
            str(tmp_path / "salida"),
            "--skip-original-copy",
        ],
    )
    exigir_aviso(resultado, "markitdown")


def test_muestra_de_doscientas_hojas(tmp_path):
    ruta = pdf_con_hojas(
        tmp_path, [[f"Hoja {i} del libro"] for i in range(1, 201)]
    )
    assert ingest.muestra_de_hojas(ruta) == [
        {"pagina_pdf": 30, "renglon": "Hoja 30 del libro"},
        {"pagina_pdf": 70, "renglon": "Hoja 70 del libro"},
        {"pagina_pdf": 110, "renglon": "Hoja 110 del libro"},
        {"pagina_pdf": 150, "renglon": "Hoja 150 del libro"},
        {"pagina_pdf": 190, "renglon": "Hoja 190 del libro"},
    ]


def test_muestra_de_veinte_hojas(tmp_path):
    ruta = pdf_con_hojas(
        tmp_path, [[f"Hoja {i} del libro"] for i in range(1, 21)]
    )
    assert [item["pagina_pdf"] for item in ingest.muestra_de_hojas(ruta)] == [3, 7, 11, 15, 19]


def test_muestra_de_tres_hojas_no_repite(tmp_path):
    ruta = pdf_con_hojas(tmp_path, [["Una"], ["Dos"], ["Tres"]])
    paginas = [item["pagina_pdf"] for item in ingest.muestra_de_hojas(ruta)]
    assert len(paginas) == len(set(paginas))
    assert paginas
    assert all(1 <= pagina <= 3 for pagina in paginas)


def test_hoja_en_blanco_se_dice(tmp_path):
    hojas = [[f"Hoja {i} del libro"] for i in range(1, 201)]
    hojas[69] = []
    ruta = pdf_con_hojas(tmp_path, hojas)
    muestra = ingest.muestra_de_hojas(ruta)
    hoja = next(item for item in muestra if item["pagina_pdf"] == 70)
    assert hoja["renglon"] == "(sin capa de texto)"


def test_renglon_de_solo_numeros_no_cuenta(tmp_path):
    hojas = [[f"Hoja {i} del libro"] for i in range(1, 201)]
    hojas[29] = ["  123  ", "Texto"]
    ruta = pdf_con_hojas(tmp_path, hojas)
    muestra = ingest.muestra_de_hojas(ruta)
    hoja = next(item for item in muestra if item["pagina_pdf"] == 30)
    assert hoja["renglon"] == "Texto"


def test_el_alta_imprime_la_muestra_y_la_guarda_fuera_de_raw(tmp_path):
    antes = inventario_raw()
    ruta = pdf_con_hojas(
        tmp_path, [[f"Hoja {i} del libro"] for i in range(1, 21)], nombre="aviso.pdf"
    )
    salida = tmp_path / "salida"
    resultado = subprocess.run(
        [
            sys.executable,
            str(INGEST),
            str(ruta),
            "--out",
            str(salida),
            "--converter",
            "pdf_pages",
            "--skip-original-copy",
            "--write-meta",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    detalle = f"code={resultado.returncode}\nSTDERR:\n{resultado.stderr}\nSTDOUT:\n{resultado.stdout}"
    assert resultado.returncode == 0, detalle
    assert "Muestra:" in resultado.stdout, detalle
    meta = json.loads((salida / "aviso.meta.json").read_text(encoding="utf-8"))
    assert len(meta["muestra_hojas"]) == 5
    assert [item["pagina_pdf"] for item in meta["muestra_hojas"]] == [3, 7, 11, 15, 19]
    assert "page_model" in meta
    assert inventario_raw() == antes
