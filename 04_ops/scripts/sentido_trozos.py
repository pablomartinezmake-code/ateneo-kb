"""Trocea un raw de Markdown en pasajes solapados que guardan su posición exacta en la fuente.

Cada trozo cumple `texto[ini:fin] == trozo["texto"]`: el pasaje que devuelve la búsqueda por sentido
es un rango del fichero, no una paráfrasis, y siempre se puede volver a él. Lleva además la línea
donde empieza y la marca de hoja que lo precede (`<!-- pdf_page: N -->` → «PDF p. N»,
`<!-- page: N -->` → «p. N»; vacía si el texto no trae marcas). Esa marca es la dirección de vuelta
al original: la hoja del PDF, no el folio impreso, que se lee en la imagen.

Las marcas de hoja son fronteras duras: un trozo nunca cruza de una hoja a otra, para que la
dirección que lleva valga para todo el pasaje. Las fuentes declaradas en `folios_en_corchetes`
(marcas `[**N**]`, como la transcripción de Kant del ejemplo) usan esas marcas como «p. N».

Python puro: no necesita modelo ni tokenizador. El tamaño se mide en caracteres; 1.200 caracteres
quedan holgadamente por debajo de los 512 tokens con que se usa BGE-M3 en las lenguas europeas.
"""
from __future__ import annotations

import bisect
import re

CHUNKER_VERSION = "exact-source-ranges-public-v1"
MAXIMO = 1200  # caracteres por trozo
SOLAPE = 200   # caracteres que un trozo comparte, como mucho, con el anterior

MARCA_HOJA = re.compile(r"<!-- (pdf_)?page: (\d+)(?: \| printed_page: [^>]*?)? -->")
MARCA_FOLIO = re.compile(r"\[\*\*(\d+)\*\*\]")
PARRAFO = re.compile(r"\n[ \t\r]*\n")  # también con saltos de Windows (\r\n)
FRASE = re.compile(r"(?<=[.!?;:»])\s+")
CON_LETRA = re.compile(r"\w")


def _recortar(texto: str, a: int, b: int) -> tuple[int, int]:
    """El rango [a, b) sin el espacio en blanco de los bordes."""
    while a < b and texto[a].isspace():
        a += 1
    while b > a and texto[b - 1].isspace():
        b -= 1
    return a, b


def _partir_largo(texto: str, a: int, b: int, maximo: int) -> list[tuple[int, int]]:
    """Un párrafo más largo que `maximo` se corta por frases; una frase demasiado larga, por espacios."""
    cortes = [a] + [m.end() for m in FRASE.finditer(texto, a, b)] + [b]
    frases = [_recortar(texto, x, y) for x, y in zip(cortes, cortes[1:])]
    salida = []
    for x, y in frases:
        while y - x > maximo:
            corte = texto.rfind(" ", x + 1, x + maximo)
            corte = corte if corte > x else x + maximo
            xa, ya = _recortar(texto, x, corte)
            if ya > xa:
                salida.append((xa, ya))
            x, _ = _recortar(texto, corte, y)
        if y > x:
            salida.append((x, y))
    return salida


def _unidades(texto: str, a: int, b: int, maximo: int) -> list[tuple[int, int]]:
    """Párrafos (o pedazos de párrafo) de la región [a, b), con sus posiciones exactas."""
    cortes = [a] + [x for m in PARRAFO.finditer(texto, a, b) for x in (m.start(), m.end())] + [b]
    salida = []
    for x, y in zip(cortes[::2], cortes[1::2]):
        x, y = _recortar(texto, x, y)
        if y <= x:
            continue
        salida.extend(_partir_largo(texto, x, y, maximo) if y - x > maximo else [(x, y)])
    return salida


def _ventanas(unidades: list[tuple[int, int]], maximo: int, solape: int) -> list[tuple[int, int]]:
    """Junta unidades seguidas hasta `maximo`; la siguiente ventana repite las últimas que caben en `solape`."""
    salida, i, n = [], 0, len(unidades)
    while i < n:
        j = i
        while j + 1 < n and unidades[j + 1][1] - unidades[i][0] <= maximo:
            j += 1
        salida.append((unidades[i][0], unidades[j][1]))
        if j == n - 1:
            break
        k = j + 1
        while k - 1 > i and unidades[j][1] - unidades[k - 1][0] <= solape:
            k -= 1
        i = k
    return salida


def regiones(texto: str, folios: bool = False) -> list[tuple[int, int, str]]:
    """Tramos del texto entre marcas de hoja, cada uno con la etiqueta de la marca que lo abre."""
    marcas = []
    if folios:
        marcas = [(m.start(), m.end(), f"p. {int(m.group(1))}") for m in MARCA_FOLIO.finditer(texto)]
    else:
        marcas = [(m.start(), m.end(), ("PDF p. " if m.group(1) else "p. ") + str(int(m.group(2))))
                  for m in MARCA_HOJA.finditer(texto)]
    salida, desde, etiqueta = [], 0, ""
    for ini, fin, nueva in marcas:
        salida.append((desde, ini, etiqueta))
        desde, etiqueta = fin, nueva
    salida.append((desde, len(texto), etiqueta))
    return salida


def trocear(texto: str, folios: bool = False, maximo: int = MAXIMO, solape: int = SOLAPE) -> list[dict]:
    """Trozos de `texto` en orden: `texto`, `ini`, `fin`, `linea` (desde 1) y `pagina` (etiqueta o "")."""
    if maximo < 1 or solape < 0:
        raise ValueError("maximo tiene que ser positivo y solape no negativo")
    saltos = [m.start() for m in re.finditer("\n", texto)]
    trozos = []
    for a, b, etiqueta in regiones(texto, folios):
        for ini, fin in _ventanas(_unidades(texto, a, b, maximo), maximo, solape):
            pasaje = texto[ini:fin]
            if not CON_LETRA.search(pasaje):
                continue  # separadores, reglas «---», restos de maqueta: nada que buscar
            trozos.append({"texto": pasaje, "ini": ini, "fin": fin,
                           "linea": bisect.bisect_left(saltos, ini) + 1, "pagina": etiqueta})
    return trozos
