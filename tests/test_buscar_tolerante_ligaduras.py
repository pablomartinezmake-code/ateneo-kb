"""Ligaduras intactas: búsqueda sobre la expansión, coordenadas del testigo."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import buscar_tolerante as B  # noqa: E402


@pytest.mark.parametrize("ligature, expanded", [
    ("ﬀ", "ff"), ("ﬁ", "fi"), ("ﬂ", "fl"), ("ﬃ", "ffi"),
    ("ﬄ", "ffl"), ("ﬅ", "st"), ("ﬆ", "st"),
])
def test_ligaduras_y_bordes(ligature, expanded):
    text = "ﬃx " + ligature + " " + ligature + "x"
    found = list(B.hallazgos(text, expanded, plural=""))
    assert [(h.inicio, h.fin, h.forma) for h in found] == [(3, 4, ligature)]
    assert list(B.hallazgos(ligature, expanded[0], plural="")) == []
    assert list(B.hallazgos(ligature, expanded[-1], plural="")) == []
    assert B.contar(expanded, ligature, plural="") == (1, 0)


def test_ligadura_con_cortes_y_forma_original():
    text = "ﬀ artiﬁ cielle artiﬁ\xad\ncielle"
    found = list(B.hallazgos(text, "artificielle", plural=""))
    assert [(h.forma, h.motivo) for h in found] == [
        ("artiﬁ cielle", "corte de OCR"), ("artiﬁ\xad\ncielle", "guion blando")]
    assert all(text[h.inicio:h.fin] == h.forma for h in found)


def test_sin_normalizacion_de_compatibilidad_general():
    assert B.contar("①", "1", plural="") == (0, 0)
