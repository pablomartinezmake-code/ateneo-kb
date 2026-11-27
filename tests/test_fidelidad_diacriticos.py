"""La quinta marca: un testigo sin diacríticos NO está limpio (portada de la Mesa).

Un texto castellano sin tildes ni eñes puede superar las otras cuatro marcas.

Lo que fija este test: (1) la marca caza el caso, (2) no toca las cuatro marcas
viejas, (3) el inglés —sin diacríticos propios— NO se marca por no tenerlos,
(4) la detección de idioma no confunde inglés con castellano.

    python -m pytest tests/test_fidelidad_diacriticos.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fidelidad_capa as fc

BOOKS = Path(__file__).resolve().parent.parent / "01_raw" / "books"

ES_SANO = (
    "La intervención del analista sobre la transferencia no es una interpretación "
    "cualquiera: ¿qué es interpretar? El ruiseñor canta y el daño se hace público. "
) * 60
ES_SIN_TILDES = (
    "La intervencion del analista sobre la transferencia no es una interpretacion "
    "cualquiera: que es interpretar? El ruisenor canta y el dano se hace publico. "
) * 60
EN = (
    "The quick brown fox jumps over the lazy dog and it was not what they had "
    "expected from the one who would have been there. "
) * 60


def test_es_sin_tildes_se_marca():
    m = fc.medir_markdown(ES_SIN_TILDES)
    assert m["diacritics_language"] == "es"
    assert m["diacritics_per_thousand"] <= fc.umbral_diacritico("es")
    b = fc.banda(m["per_thousand_words"], m["distinct_mark_ratio"], m["words"],
                 m["diacritics_per_thousand"], m["diacritics_language"])
    assert b.startswith("SIN DIACR")


def test_es_sano_no_se_marca():
    m = fc.medir_markdown(ES_SANO)
    assert m["diacritics_per_thousand"] > fc.umbral_diacritico("es")
    b = fc.banda(m["per_thousand_words"], m["distinct_mark_ratio"], m["words"],
                 m["diacritics_per_thousand"], m["diacritics_language"])
    assert not b.startswith("SIN DIACR")


def test_ingles_no_aplica_ni_se_marca():
    assert fc.alfabeto_diacritico("en") is None
    assert fc.detectar_idioma(EN) == "en"
    m = fc.medir_markdown(EN)
    assert "diacritics_per_thousand" not in m


def test_la_medida_vieja_no_se_mueve():
    """La quinta marca es APARTE: `per_thousand_words` es idéntico con y sin idioma."""
    base = fc.medir_markdown(ES_SANO, idioma="en")  # idioma sin alfabeto: sin marca
    con = fc.medir_markdown(ES_SANO, idioma="es")
    for campo in ("words", "marks", "strong_marks", "per_thousand_words", "distinct_mark_ratio"):
        assert base[campo] == con[campo], campo


def test_banda_sin_diacriticos_es_retrocompatible():
    """Llamada de tres argumentos, como antes: mismo veredicto que antes."""
    assert fc.banda(0.1, 1.0, 5000) == "limpio"
    assert fc.banda(0.208, 1.0, 19218) == "aceptable — ruido de fondo normal en escaneo"


def test_idioma_explicito_manda_sobre_la_deteccion():
    m = fc.medir_markdown(ES_SIN_TILDES, idioma="es")
    assert "diacritics_language_detected" not in m


@pytest.mark.skipif(not (BOOKS / "shakespeare-sueno-noche-san-juan-astrana-1922.md").exists(),
                    reason="raw no disponible")
def test_astrana_deja_de_salir_aceptable():
    m = fc.medir_markdown((BOOKS / "shakespeare-sueno-noche-san-juan-astrana-1922.md")
                          .read_text(encoding="utf-8"))
    b = fc.banda(m["per_thousand_words"], m["distinct_mark_ratio"], m["words"],
                 m.get("diacritics_per_thousand"), m.get("diacritics_language"))
    assert b.startswith("SIN DIACR")
