"""La ingesta declara guion blando, NUL y ligaduras.

Los ejemplos cubren guiones blandos, ligaduras y secuencias con NUL.
"""
import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
INGEST = RAIZ / "ingest_to_raw.py"
sys.path.insert(0, str(RAIZ))
import ingest_to_raw as ingest  # noqa: E402

# Línea 197, tal cual.
DAY_197 = (
    "terms of metaphysical presence and repre\xadsen\xadta\xadtion as "
    "\u201cinformation.\u201d But "
)
# Línea 237 y el arranque de la 238, con el CRLF del fichero.
DAY_237 = "under\xad\r\nstanding"
# Línea 144: el guion blando va delante de la palabra.
DAY_144 = "who \xadorganized"
# Detrás de un guion normal: no parte palabra.
DAY_NEO = "neo-\xaddocumentation"
DAY_TRAS_GUION = "nineteenth-\xad and"
# «con blanco detrás»: no está en esos tres raws; cierra esa cláusula de la medida.
DAY_CON_BLANCO = "repre\xad sentation"
MARONET = "d\u2019intelligence\r\narti\ufb01cielle. "
LACRUZ = (
    "\u201d\r\nChinese: \u201c\u5404\x00\x00\x00\u5e72\u90e8\u5fc5\x00"
    "\u63d0\u9ad8\x00\x00\u6c34\u5e73\u3002\u201d\r\nLanguage"
)

CERO = {
    "guion_blando": 0,
    "guion_blando_parte_palabra": 0,
    "nul": 0,
    "ligaduras": 0,
}


def test_day_197_guion_blando_dentro_de_la_linea():
    assert ingest.medir_invisibles(DAY_197) == {
        "guion_blando": 3,
        "guion_blando_parte_palabra": 3,
        "nul": 0,
        "ligaduras": 0,
    }


def test_day_237_guion_blando_a_final_de_linea():
    assert ingest.medir_invisibles(DAY_237) == {
        "guion_blando": 1,
        "guion_blando_parte_palabra": 1,
        "nul": 0,
        "ligaduras": 0,
    }


def test_day_144_delante_de_palabra_no_la_parte():
    assert ingest.medir_invisibles(DAY_144) == {
        **CERO,
        "guion_blando": 1,
    }


def test_guion_tras_guion_normal_no_parte():
    assert ingest.medir_invisibles(DAY_NEO) == {**CERO, "guion_blando": 1}
    assert ingest.medir_invisibles(DAY_TRAS_GUION) == {**CERO, "guion_blando": 1}


def test_guion_con_blanco_sin_salto_no_parte():
    # Horizontal whitespace after a soft hyphen alone does not establish a word break.
    assert ingest.medir_invisibles(DAY_CON_BLANCO) == {
        "guion_blando": 1,
        "guion_blando_parte_palabra": 0,
        "nul": 0,
        "ligaduras": 0,
    }
    assert ingest.medir_invisibles("repre\xad \nsentation")["guion_blando_parte_palabra"] == 1


def test_maronet_cuenta_la_ligadura():
    assert ingest.medir_invisibles(MARONET) == {**CERO, "ligaduras": 1}


def test_lacruz_cuenta_los_nul():
    assert ingest.medir_invisibles(LACRUZ) == {**CERO, "nul": 6}


def test_texto_sin_invisibles_da_cero_y_la_linea_dice_ninguno():
    medida = ingest.medir_invisibles("ningún carácter escondido")
    assert medida == CERO
    assert ingest.linea_invisibles(medida) == "Invisibles: ninguno"


def test_linea_con_invisibles_da_las_cuatro_cifras_y_que_hacer():
    medida = ingest.medir_invisibles(DAY_197 + DAY_144 + DAY_237 + MARONET + LACRUZ)
    assert medida == {
        "guion_blando": 5,
        "guion_blando_parte_palabra": 4,
        "nul": 6,
        "ligaduras": 1,
    }
    assert ingest.linea_invisibles(medida) == (
        "Invisibles: guion_blando=5, guion_blando_parte_palabra=4, "
        "nul=6, ligaduras=1. "
        + ingest.CONSEJO_GUION_BLANDO + " " + ingest.CONSEJO_LIGADURAS + " " + ingest.CONSEJO_NUL
    )


def test_el_consejo_dice_solo_lo_que_sirve():
    # La búsqueda tolerante ya pliega las ligaduras; el aviso debe recomendarla.
    solo_lig = ingest.linea_invisibles(ingest.medir_invisibles(MARONET))
    assert "buscar_tolerante.py" in solo_lig and "ninguna herramienta" not in solo_lig and "rg -a" not in solo_lig
    solo_nul = ingest.linea_invisibles(ingest.medir_invisibles(LACRUZ))
    assert "rg -a" in solo_nul and "buscar_tolerante" not in solo_nul
    solo_blando = ingest.linea_invisibles(ingest.medir_invisibles(DAY_197))
    assert "buscar_tolerante" in solo_blando and "rg -a" not in solo_blando and "NFKC" not in solo_blando


def _ingerir(tmp_path, monkeypatch, texto, stem):
    fuente = tmp_path / f"{stem}.txt"
    fuente.write_text(texto, encoding="utf-8", newline="\n")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ingest_to_raw.py",
            str(fuente),
            "--out",
            str(tmp_path),
            "--stem",
            stem,
            "--skip-original-copy",
            "--write-meta",
        ],
    )
    ingest.main()
    return tmp_path / f"{stem}.meta.json"


def test_alta_sin_invisibles_dice_ninguno_y_deja_el_limpio(tmp_path, monkeypatch, capsys):
    meta_path = _ingerir(tmp_path, monkeypatch, "the " * 2500, "limpio")
    salida = capsys.readouterr().out
    lineas = salida.splitlines()
    i_capa = next(i for i, ln in enumerate(lineas) if ln.startswith("Capa:"))
    assert lineas[i_capa + 1] == "Invisibles: ninguno"
    assert lineas[i_capa].rstrip().endswith("limpio")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["invisibles"] == CERO


def test_capa_limpia_con_invisibles_remite_y_guarda_la_medida(tmp_path, monkeypatch, capsys):
    meta_path = _ingerir(tmp_path, monkeypatch, ("the " * 2500) + "repre\xadsentation", "sucio")
    salida = capsys.readouterr().out
    lineas = salida.splitlines()
    i_capa = next(i for i, ln in enumerate(lineas) if ln.startswith("Capa:"))
    capa = lineas[i_capa]
    assert "limpio" in capa
    assert capa.endswith("limpio -- ver Invisibles:")
    assert "/mil palabras" in capa
    assert lineas[i_capa + 1] == (
        "Invisibles: guion_blando=1, guion_blando_parte_palabra=1, "
        "nul=0, ligaduras=0. " + ingest.CONSEJO_GUION_BLANDO
    )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["invisibles"] == {
        "guion_blando": 1,
        "guion_blando_parte_palabra": 1,
        "nul": 0,
        "ligaduras": 0,
    }


def test_solo_invisibles_imprime_y_no_reescribe(tmp_path):
    primero = tmp_path / "day.md"
    segundo = tmp_path / "limpio.md"
    primero.write_text(DAY_197, encoding="utf-8", newline="\n")
    segundo.write_text("limpio del todo", encoding="utf-8", newline="\n")
    antes = primero.read_bytes()
    resultado = subprocess.run(
        [sys.executable, str(INGEST), "--solo-invisibles", str(primero), str(segundo)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    detalle = f"code={resultado.returncode}\nSTDERR:\n{resultado.stderr}\nSTDOUT:\n{resultado.stdout}"
    assert resultado.returncode == 0, detalle
    lineas = [ln for ln in resultado.stdout.splitlines() if ln.startswith("Invisibles:")]
    assert lineas == [
        f"Invisibles: {primero} -- guion_blando=3, guion_blando_parte_palabra=3, "
        "nul=0, ligaduras=0. " + ingest.CONSEJO_GUION_BLANDO,
        f"Invisibles: {segundo} -- ninguno",
    ], detalle
    assert primero.read_bytes() == antes
    assert list(tmp_path.glob("*.meta.json")) == []
