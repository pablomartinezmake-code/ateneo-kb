"""La regla 3 (palabra sin vocal) no debe confundir una SIGLA con un OCR roto.

En un artículo con capa intacta las 63 marcas eran cuatro
tokens: `LLMs` (45), `Psych` (15), `ssrn` (2), `PMLR` (1). Ni un solo resto de OCR.

Lo que este test fija es el LÍMITE del arreglo, no solo el arreglo:
una sigla en mayúsculas deja de contar; todo lo demás sigue contando, incluidas
las abreviaturas bibliográficas en minúscula o capitalizadas, porque
distinguirlas exigiría un diccionario y eso ya no es medir, es opinar.

    python -m pytest tests/test_fidelidad_siglas.py -q
"""

from __future__ import annotations

import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fidelidad_capa as fc


def marcas_sin_vocal(texto: str) -> int:
    return fc.marcas_de_unidad(texto)["vowelless"]


# --- Lo que el arreglo silencia: siglas en mayúsculas -----------------------

def test_sigla_en_mayusculas_no_es_marca():
    for sigla in ("LLM", "GPT", "NLP", "RLHF", "PMLR", "MLP", "CNN"):
        assert marcas_sin_vocal(f"El modelo {sigla} predice.") == 0, sigla


def test_sigla_con_plural_minusculo_no_es_marca():
    assert marcas_sin_vocal("Los LLMs predicen.") == 0


def test_sigla_pegada_a_puntuacion_no_es_marca():
    assert marcas_sin_vocal("(LLMs), «GPT»; RLHF.") == 0


# --- Lo que el arreglo NO debe silenciar: el OCR roto de siempre ------------

def test_ocr_roto_sigue_contando():
    # Los tres ejemplos que el propio detector documenta en la regla 3.
    assert marcas_sin_vocal("llnc ntrc dndcs") == 3


def test_palabra_larga_en_mayusculas_sigue_contando():
    # Un titular en versalitas destrozado por el OCR no es una sigla:
    # seis letras o más se siguen contando.
    assert marcas_sin_vocal("CNCLSN") == 1


def test_abreviatura_capitalizada_sin_y_sigue_contando():
    # Una abreviatura sin `y` no tiene excepción que la salve: se cuenta.
    # (`Psych` sí sale, pero por la regla de la `y`, no por ser abreviatura.)
    assert marcas_sin_vocal("Schmdt") == 1


def test_minusculas_sin_vocal_siguen_contando():
    assert marcas_sin_vocal("ssrn stsc") == 2


# --- La `y` inglesa: vocal cuando la palabra tiene forma de palabra ---------

def test_palabra_inglesa_con_y_no_es_marca():
    for palabra in ("myth", "rhythm", "hymn", "nymph", "crypt", "lynx", "tryst"):
        assert marcas_sin_vocal(f"a {palabra} here") == 0, palabra


def test_basura_larga_con_y_sigue_contando():
    # La cola del censo: siete letras o más, o varias `y`. Es ruido y se caza.
    assert marcas_sin_vocal("zlpwcfwkcyj") == 1
    assert marcas_sin_vocal("yyhhvvhh") == 1
    assert marcas_sin_vocal("ypvrrrtnm") == 1


def test_varias_y_siguen_contando():
    # `yyyy` (plantilla de fecha) y `lyly` (el dramaturgo) caen del mismo lado:
    # más de una `y` no tiene forma de palabra inglesa.
    assert marcas_sin_vocal("yyyy") == 1
    assert marcas_sin_vocal("lyly") == 1


def test_el_contador_de_la_y_se_declara():
    assert fc.marcas_de_unidad("myth rhythm hymn")["y_vowel"] == 3


# --- Las otras tres reglas no se tocan -------------------------------------

def test_digito_interior_intacto():
    assert fc.marcas_de_unidad("Mu11dial")["digit"] == 1


def test_signo_incrustado_intacto():
    assert fc.marcas_de_unidad("h11l·cmos")["sign"] >= 1


# --- El caso real, de punta a punta ----------------------------------------

def test_el_paper_de_felin_queda_limpio():
    ruta = (
        Path(__file__).resolve().parent.parent
        / "01_raw"
        / "web"
        / "felin-holweg-2024-theory-is-all-you-need.md"
    )
    if not ruta.exists():
        pytest.skip("el raw de Felin no está en este repositorio")
    medida = fc.medir_markdown(ruta.read_text(encoding="utf-8", errors="replace"))
    # De 2,635/mil y sello SUCIO a 0,084: 46 siglas y 15 `y` inglesas salieron
    # del cómputo y se declaran; las 2 marcas que quedan son `ssrn`, residuo de
    # URL. Cero dígitos rotos y cero signos, que es lo que se esperaba de un
    # PDF de editorial.
    assert medida["marks"] == {"digit": 0, "sign": 0, "vowelless": 2}, medida["marks"]
    assert medida["per_thousand_words"] < 2.0, medida["per_thousand_words"]
    assert medida["acronyms_skipped"] == 46, medida.get("acronyms_skipped")
    assert medida["y_as_vowel_skipped"] == 15, medida.get("y_as_vowel_skipped")
