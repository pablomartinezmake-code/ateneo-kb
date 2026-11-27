#!/usr/bin/env python3
"""Buscar un término en un raw sin perder lo que el OCR partió.

Por qué existe
--------------
`palabras_partidas.py` dice CUÁNTO parte un testigo. Esto sirve para leerlo de
todos modos. Son las dos mitades del mismo problema: el diagnóstico y la lectura.

Una palabra cortada por OCR puede desaparecer de los resultados de búsqueda literal.

El guion blando (U+00AD) es otra cosa: el PDF nacido digital lo mete dentro
de la palabra, invisible, para marcar que los dos lados son una sola. No es
un corte de OCR. Si el término contiene uno, se quita con el blanco o el salto
que lo sigue. El hallazgo queda
seguro, motivo «guion blando», aunque el primer trozo sea frecuente. El
guion normal de fin de línea no entra: juntarlo pegaría también el compuesto.

Por qué NO decide sola
-----------------------
Tolerar un corte en cualquier posición interior genera falsos, y no hay regla
que los quite todos. Los dos que aparecieron, medidos:

  · `sugestión es` — la palabra ya está entera y el `es` es el verbo siguiente.
    Este se filtra solo, y se filtra aquí.
  · `su gestión ministerial` puede contener un posesivo y un sustantivo. Solo
    el contexto permite distinguirlo de una palabra partida.

Por eso esta sonda **separa lo seguro de lo que hay que mirar** en vez de dar un
número y callarse. `hallazgos()` marca cada resultado con `seguro=True/False`, y
`auditar()` imprime los dudosos con su contexto para que los mire una persona.
Es lo que se hizo a mano con los 56 candidatos de una traducción y los 2 de
la otra; el módulo automatiza el trabajo, no el juicio.

Uso:
    python buscar_tolerante.py sugestión 01_raw/books/obras-completas-vol-*.md
    python buscar_tolerante.py --auditar sugestión 01_raw/books/obras-completas-*.md
    python buscar_tolerante.py --kwic Suggestion 01_raw/books/vol-*.md

Desde otro script:
    import buscar_tolerante as B
    seguros, dudosos = B.contar(texto, "sugestión")
"""

from __future__ import annotations

import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

VERSION = "2026-11-04.4"

# Solo las ligaduras tipográficas latinas: NFKC general también transforma
# números, signos y otros caracteres que no son equivalentes para esta sonda.
_LIGADURAS = dict(zip("ﬀﬁﬂﬃﬄﬅﬆ", ("ff", "fi", "fl", "ffi", "ffl", "st", "st")))
_RE_LIGADURAS = re.compile("[ﬀﬁﬂﬃﬄﬅﬆ]")


def _expandir_ligaduras(texto: str) -> tuple[str, list[int] | None]:
    """Devuelve la vista de búsqueda y el índice original de cada carácter."""
    if not _RE_LIGADURAS.search(texto):
        return texto, None
    partes = []
    indices = []
    for i, letra in enumerate(texto):
        parte = _LIGADURAS.get(letra, letra)
        partes.append(parte)
        indices.extend([i] * len(parte))
    return "".join(partes), indices

# Palabras castellanas tan frecuentes que, si son el primer trozo, la pareja casi
# nunca es un corte: 'su gestión', 'la mentable', 'de creto'. No se descartan —el
# tomo I trae «en la su gestión está todo», que sí lo es—: se marcan para auditar.
FRECUENTES = {
    "su", "la", "el", "de", "en", "un", "una", "los", "las", "por", "con", "al",
    "se", "lo", "es", "no", "ni", "que", "del", "mi", "tu", "y", "o", "a",
    "der", "die", "das", "ein", "und", "im", "zu", "den", "dem", "des",
    "il", "lo", "gli", "che", "di", "da", "per",
}


def sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


@dataclass
class Hallazgo:
    inicio: int
    fin: int
    forma: str        # tal como está en el fichero, con el corte si lo hay
    seguro: bool      # False = hay que mirarlo con ojos
    motivo: str


# El guion blando, U+00AD. En una raw string `\u00ad` no es el carácter:
# la barra se queda y el patrón buscaría seis signos que no están en el texto.
_GUION_BLANDO = "\xad"
# El corte del maquetador: el guion blando pegado a la letra siguiente, o seguido de un SALTO de línea (con o sin
# blancos alrededor, y con CRLF). Un guion blando seguido de espacio sin salto NO es un corte: en la biblioteca es
# una palabra entera o truncada. Unirlo pegaría dos palabras.
_CORTE_BLANDO = rf"{_GUION_BLANDO}(?:[ \t]*\r?\n[ \t]*)?"
# Entre letra y letra: el corte del maquetador, o el blanco del OCR. El guion normal no está aquí.
_ENTRE_LETRAS = rf"(?:{_CORTE_BLANDO}|[ \t]*\r?\n?[ \t]*)"
# El plural va pegado a la última letra. Solo puede separarlo un corte del maquetador.
# Un blanco aquí se comería el «es» de «sugestión es» y la palabra desaparecería.
_ANTES_DEL_PLURAL = rf"(?:{_CORTE_BLANDO})?"
_GUION_BLANDO_Y_DESPUES = re.compile(_CORTE_BLANDO)
# Los bordes del término: si un corte del maquetador lo une a otra letra, la palabra es otra.
_LETRA_TRAS_GUION_BLANDO = re.compile(rf"{_CORTE_BLANDO}\w")
_GUION_BLANDO_AL_FINAL = re.compile(rf"{_CORTE_BLANDO}\Z")


def _pegado_delante(texto: str, i: int) -> bool:
    """¿Un corte del maquetador une la posición i a la letra de antes? Mira hacia atrás sin ventana fija
    (Sol, S09: con 40 caracteres, una sangría larga tras el salto escapaba)."""
    k = i
    while k > 0 and texto[k - 1] in " \t":
        k -= 1
    if k > 0 and texto[k - 1] == "\n":
        k -= 1
        if k > 0 and texto[k - 1] == "\r":
            k -= 1
        while k > 0 and texto[k - 1] in " \t":
            k -= 1
    elif k != i:
        return False  # espacio sin salto: no es un corte
    return k >= 2 and texto[k - 1] == _GUION_BLANDO and re.match(r"\w", texto[k - 2]) is not None


def _letras(palabra: str) -> str:
    """Las letras de una palabra, separables por el espacio del OCR o por un guion blando."""
    piezas = []
    for ch in palabra:
        opciones = {ch, sin_tildes(ch)}
        clase = "".join(sorted(opciones))
        piezas.append(f"[{re.escape(clase)}]" if len(clase) > 1 else re.escape(ch))
    return _ENTRE_LETRAS.join(piezas)


def patron(termino: str, plural: str = r"(?:e?s)?") -> re.Pattern:
    """Un regex que admite espacios de OCR entre letra y letra del término.

    Para reconocer ligaduras en el texto y recuperar posiciones originales,
    usar hallazgos(): este regex, aplicado directamente, no expande el texto.

    El término se da con su tilde ('sugestión'); se acepta también sin ella,
    porque el OCR se las come a menudo.

    Si el término es un SINTAGMA ('acto fallido'), la flexión se aplica a cada
    palabra, no sólo a la última: de lo contrario 'actos fallidos' no casa y el
    recuento sale MÁS BAJO que el de una búsqueda literal — que es la peor
    manera de fallar, porque el instrumento parece prudente cuando está ciego.
    La búsqueda tolerante debe recuperar al menos las coincidencias literales.
    """
    termino = _RE_LIGADURAS.sub(lambda m: _LIGADURAS[m.group()], termino)
    palabras = termino.split()
    cuerpos = [_letras(p) + _ANTES_DEL_PLURAL + plural for p in palabras]
    cuerpo = r"[ \t]*\n?[ \t]*".join(cuerpos)
    return re.compile(rf"\b{cuerpo}\b", re.IGNORECASE)


def hallazgos(texto: str, termino: str, plural: str = r"(?:e?s)?") -> Iterator[Hallazgo]:
    """Cada aparición del término, entera o partida, con su grado de certeza."""
    original = texto
    texto, indices = _expandir_ligaduras(texto)
    termino = _RE_LIGADURAS.sub(lambda m: _LIGADURAS[m.group()], termino)

    def hallazgo(m: re.Match, seguro: bool, motivo: str) -> Hallazgo:
        inicio = indices[m.start()] if indices is not None else m.start()
        fin = indices[m.end() - 1] + 1 if indices is not None else m.end()
        return Hallazgo(inicio, fin, original[inicio:fin], seguro, motivo)

    rx = patron(termino, plural)
    entero = re.compile(rf"^{re.escape(termino.split()[0])}", re.IGNORECASE)
    huecos_propios = termino.count(" ")   # los espacios que el término ya lleva
    for m in rx.finditer(texto):
        bruto = m.group(0)
        # Un guion blando pegado al término por fuera dice que la palabra sigue:
        # «information\xad\nally» es «informationally». El \b no lo ve, porque
        # U+00AD no es letra. No se cuenta, ni como seguro ni como duda.
        # Por detrás hay dos formas: el guion queda fuera del hallazgo, o el
        # patrón se lo tragó esperando un plural que no vino y acabó en el salto.
        detras = (_LETRA_TRAS_GUION_BLANDO.match(texto, m.end())
                  or (_GUION_BLANDO_AL_FINAL.search(bruto)
                      and re.match(r"\w", texto[m.end():m.end() + 1])))
        delante = _pegado_delante(texto, m.start())
        if detras or delante:
            continue
        # El guion blando lo pone el maquetador para decir «una sola palabra».
        # El blanco o el salto que van detrás no convierten el hallazgo en duda,
        # ni aunque el primer trozo esté en FRECUENTES.
        if _GUION_BLANDO in bruto:
            limpio = _GUION_BLANDO_Y_DESPUES.sub("", bruto)
            if len(re.findall(r"\s+", limpio)) <= huecos_propios:
                yield hallazgo(m, True, "guion blando")
                continue
        if len(re.findall(r"\s+", bruto)) <= huecos_propios:
            # sólo los espacios del propio sintagma: nada que auditar
            yield hallazgo(m, True, "entera")
            continue
        compacto = re.sub(r"\s+", "", bruto)
        antes = bruto.split()[0]
        # 'sugestión es': la palabra ya estaba completa antes del espacio
        if entero.match(antes) and len(antes) >= len(termino):
            continue
        # 'su gestión': el primer trozo es una palabra funcional -> a auditar
        if antes.lower() in FRECUENTES:
            yield hallazgo(m, False,
                           f"el primer trozo ('{antes}') es palabra por sí sola")
            continue
        yield hallazgo(m, True, "corte de OCR")


def contar(texto: str, termino: str, plural: str = r"(?:e?s)?") -> tuple[int, int]:
    """(seguros, a auditar). El total honesto está entre seguros y seguros+dudosos."""
    seguros = dudosos = 0
    for h in hallazgos(texto, termino, plural):
        if h.seguro:
            seguros += 1
        else:
            dudosos += 1
    return seguros, dudosos


def auditar(texto: str, termino: str, ancho: int = 95,
            plural: str = r"(?:e?s)?") -> list[tuple[str, str]]:
    """Los dudosos con su contexto, para mirarlos uno a uno."""
    salida = []
    for h in hallazgos(texto, termino, plural):
        if h.seguro:
            continue
        a, b = max(0, h.inicio - ancho), min(len(texto), h.fin + ancho)
        salida.append((h.motivo, re.sub(r"\s+", " ", texto[a:b])))
    return salida


# --------------------------------------------------------------------------
# El canario
# --------------------------------------------------------------------------
# Por qué está aquí, y no en una suite: el 2026-08-26 escribí tres verificadores
# en una sesión y los tres mintieron. El auditor de citas borraba corchetes en
# un texto con muchas notas editoriales, se comía párrafos y daba por
# INVENTADAS citas que estaban en el fichero. La sonda descartaba la palabra
# buena junto al falso positivo: siete apariciones. Y la de sintagmas devolvía
# 54 donde una búsqueda tonta devolvía 162.
#
# Había calibrado los DATOS antes de usarlos —reproduje cifras conocidas del
# corpus y no seguí hasta que salieron— pero al INSTRUMENTO no le pedí nada. Un
# verificador que miente es peor que no tenerlo, porque su «todo correcto»
# cierra la pregunta.
#
# Los tres fallos los caza una sola invariante, y es barata: **un instrumento
# que dice ser más sensible que otro nunca puede devolver menos**. No hace falta
# saber la respuesta correcta —eso es lo caro—: basta con saber la dirección.
# Corre solo, antes de medir nada, y aborta si falla.

_CASOS = [
    # (texto, término, seguros esperados, a auditar esperados, qué prueba)
    ("la sugestión hipnótica y la sugestión de vigilia", "sugestión", 2, 0,
     "la palabra entera, dos veces"),
    ("efectos de la suges tión y de la sugestio nes", "sugestión", 2, 0,
     "el corte de OCR dentro de la palabra"),
    ("el problema de la sugestión es menor de lo que parece", "sugestión", 1, 0,
     "'sugestión es' NO se descarta: la palabra entera cuenta"),
    ("durante su gestión ministerial fue el más inepto", "sugestión", 0, 1,
     "'su gestión' no se cuenta a ciegas: se manda a auditar"),
    ("los actos fallidos y el acto fallido de ayer", "acto fallido", 2, 0,
     "el sintagma flexiona en TODAS sus palabras, no sólo en la última"),
    ("una acción obsesiva y dos acciones obsesivas", "acción obsesiva", 2, 0,
     "sintagma con plural interno y tilde"),
    # Guion blando entre letras, en un salto de línea y antes de un término.
    ("terms of metaphysical presence and repre\xadsen\xadta\xadtion as “information.” But \n",
     "representation", 1, 0,
     "guion blando dentro de la línea",
     "guion blando"),
    ("I argue for a “Copernican revolution” in under\xad\nstanding the relation of documents",
     "understanding", 1, 0,
     "guion blando al final de línea, con el salto detrás",
     "guion blando"),
    ("are represented in minds, such repre\xadsen\xadta\xadtions are",
     "representation", 1, 0,
     "la palabra en plural partida por guiones blandos",
     "guion blando"),
    ("su\xad\ngestión", "sugestión", 1, 0,
     "guion blando aunque el primer trozo sea palabra frecuente",
     "guion blando"),
    # Lo que no hay que juntar, medido en el mismo raw.
    ("an exemplary figure in neo-\xad\ndocumentation texts",
     "neodocumentation", 0, 0,
     "el guion normal no se junta: neo- más guion blando sigue siendo un compuesto"),
    ("Gorichanaz, who \xadorganized and hosted", "organized", 1, 0,
     "el guion blando pegado al principio no parte la palabra",
     "entera"),
    # El borde: un guion blando pegado al término por fuera dice que la palabra
    # sigue. «information\xad\nally» es
    # «informationally», no «information». Lo cazó el control de Claude
    # (2026-11-04): 733 frente a 732.
    ("the notion of information as an information\xad\nally pro\xadcessed thing",
     "information", 1, 0,
     "guion blando detrás del término: la palabra sigue y no se cuenta",
     "entera"),
    # La misma regla por delante.
    ("against mis\xadinformation and dis\xad\ninformation alike", "information", 0, 0,
     "guion blando delante, pegado a una letra: la palabra empezó antes"),
    # Lectura en frío de Sol (2026-11-04, S09 y S10).
    ("mis\xad\n" + " " * 45 + "information", "information", 0, 0,
     "guion blando delante con salto y sangría larga: el borde no depende de una ventana fija"),
    # Guion blando seguido de espacio, SIN salto: en la biblioteca no es una palabra partida sino una palabra
    # entera o truncada.
    ("variants accounted\xad for the shift", "accounted", 1, 0,
     "guion blando y espacio sin salto: la palabra terminó, no se descarta"),
    ("I argue in under\xad\r\nstanding the relation", "understanding", 1, 0,
     "guion blando a final de línea con CRLF", "guion blando"),
]


def canario(verboso: bool = False) -> list[str]:
    """Comprueba el instrumento contra casos de respuesta conocida.

    Devuelve la lista de fallos (vacía si todo bien). Dos comprobaciones:
    los casos de arriba, y la invariante tolerante >= literal.
    """
    fallos = []

    for caso in _CASOS:
        texto, term, e_seg, e_dud, que_prueba = caso[:5]
        motivo = caso[5] if len(caso) > 5 else None
        s, d = contar(texto, term)
        if (s, d) != (e_seg, e_dud):
            fallos.append(f"{que_prueba}: esperaba ({e_seg},{e_dud}) y da ({s},{d})"
                          f"  [{texto!r}]")
        elif motivo is not None:
            motivos = [h.motivo for h in hallazgos(texto, term)]
            if motivos != [motivo] * e_seg:
                fallos.append(f"{que_prueba}: motivo esperaba {motivo!r} "
                              f"y da {motivos}  [{texto!r}]")
            elif verboso:
                print(f"  ok  {que_prueba}")
        elif verboso:
            print(f"  ok  {que_prueba}")

    # La invariante: tolerar cortes nunca puede encontrar MENOS que no tolerarlos.
    # La búsqueda literal lee el texto como está impreso: con los guiones blandos
    # unidos. Si no, contaría «information» dentro de «mis\u00adinformation».
    for texto, term, *_ in _CASOS:
        literal = len(re.findall(
            r"\b" + r"[ \t]*".join(re.escape(p) + r"(?:e?s)?" for p in term.split()) + r"\b",
            _GUION_BLANDO_Y_DESPUES.sub("", texto), re.IGNORECASE))
        s, d = contar(texto, term)
        if s + d < literal:
            fallos.append(f"tolerante ({s + d}) < literal ({literal}) para "
                          f"{term!r} en {texto!r}")
    if verboso and not fallos:
        print("  ok  invariante: tolerante >= literal en los %d casos" % len(_CASOS))
    return fallos



def expandir(rutas: list[Path]) -> list[Path]:
    """Expande comodines que la shell no expandió (PowerShell y cmd no lo hacen).

    Sin esto, `01_raw/books/*.md` llegaba literal, `is_file()` daba False y el
    recuento salía cero sin avisar: el cero falso que esta herramienta combate.
    """
    import glob
    salida: list[Path] = []
    for ruta in rutas:
        texto = str(ruta)
        if ruta.is_file():
            salida.append(ruta)
        elif any(c in texto for c in "*?["):
            encontrados = sorted(glob.glob(texto, recursive=True))
            if not encontrados:
                print(f"aviso: ningún fichero coincide con {texto}", file=sys.stderr)
            salida.extend(Path(p) for p in encontrados)
        else:
            print(f"aviso: no existe {texto}", file=sys.stderr)
    return salida


def main() -> None:
    import argparse
    import sys

    fallos = canario()
    if fallos:
        print("EL INSTRUMENTO NO PASA SU PROPIO CANARIO — no se mide nada:",
              file=sys.stderr)
        for f in fallos:
            print("  · " + f, file=sys.stderr)
        raise SystemExit(2)

    parser = argparse.ArgumentParser(
        description=("Cuenta un término admitiendo los cortes del OCR, y separa "
                     "lo seguro de lo que hay que mirar con ojos.")
    )
    parser.add_argument("--canario", action="store_true",
                        help="correr sólo el canario, en voz alta, y salir")
    parser.add_argument("termino", nargs="?")
    parser.add_argument("ficheros", nargs="*", type=Path)
    parser.add_argument("--auditar", action="store_true",
                        help="imprimir los dudosos con contexto, para revisarlos")
    parser.add_argument("--kwic", action="store_true",
                        help="imprimir todas las apariciones con contexto")
    parser.add_argument("--sin-plural", action="store_true")
    args = parser.parse_args()

    if args.canario:
        print("canario de buscar_tolerante.py (%s):" % VERSION)
        canario(verboso=True)
        print("pasa.")
        return
    if not args.termino or not args.ficheros:
        parser.error("hacen falta un término y al menos un fichero")

    plural = "" if args.sin_plural else r"(?:e?s)?"
    tot_s = tot_d = 0
    for path in expandir(args.ficheros):
        texto = path.read_text(encoding="utf-8", errors="replace")
        s, d = contar(texto, args.termino, plural)
        tot_s += s
        tot_d += d
        if s or d:
            aviso = f"  (+{d} por auditar)" if d else ""
            print(f"{path.name:<58} {s:5d}{aviso}")
        if args.auditar and d:
            for motivo, ctx in auditar(texto, args.termino, plural=plural):
                print(f"      [{motivo}] ...{ctx}...")
        if args.kwic:
            for h in hallazgos(texto, args.termino, plural):
                a, b = max(0, h.inicio - 95), min(len(texto), h.fin + 95)
                marca = " " if h.seguro else "?"
                print(f"    {marca} ...{re.sub(r'  +', ' ', texto[a:b])}...")

    print("-" * 72)
    print(f"seguros: {tot_s}   ·   por auditar: {tot_d}   ·   "
          f"el total está entre {tot_s} y {tot_s + tot_d}")


if __name__ == "__main__":
    main()
