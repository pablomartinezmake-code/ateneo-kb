#!/usr/bin/env python3
"""La quinta marca: palabras que el OCR partió por dentro.

Por qué existe
--------------
`fidelidad_capa.py` declara en su propio docstring el hueco que este módulo
cubre: «Las cifras son SUELO, no total: la corrupción puramente alfabética
(`propósi-lo` por `propósito`) es invisible para este detector». Sus cuatro
marcas miran DENTRO del token —dígito entre letras, signo entre letras, token
sin vocal, cinco consonantes seguidas—. Ninguna mira el espacio ENTRE dos
tokens, y ahí vive una enfermedad distinta: el OCR que parte `aplicar` en
`apli car`, `bibliografía` en `bibliogra fía`, `sugestión` en `su gestión`.

El daño es el mismo que describe aquel módulo, y por la misma razón: **produce
falsos negativos al localizar, sin avisar**. Una búsqueda literal puede perder
muchas apariciones sin dar indicio alguno del corte.

Alcance: toda la biblioteca, y no por gusto
--------------------------------------------
Se barrieron los 456 raw de `01_raw/books` de más de 20 KB (2026-08-26, con este
detector ya con el canario puesto):

    origen      n   mediana    p75    p90    máx
    pdf       235      3.70  11.23  13.56  17.36
    epub      215      0.02   0.05   0.13  13.53

Ciento ochenta veces de diferencia entre la mediana de lo escaneado y la de lo
nativo. **157 ficheros —el 34 %— pasan de 1 corte por mil**, y 117 pasan de 5. No
es cosa de una editorial ni de un autor: los tres peores del censo son
escaneos de ensayo de tres editoriales distintas (17,36; 16,55 y 15,37).

Un EPUB puede contener texto nativo o texto obtenido mediante OCR. La extensión
por sí sola no determina cuánto se parten las palabras.

Por qué NO se añade a las cuatro marcas de `fidelidad_capa`
------------------------------------------------------------
Porque allí `DETECTOR_VERSION` viaja con cada medida, y el criterio está copiado
literal de corpus/biblioteca/fidelidad.py del repositorio hermano de la mesa de traducción (no publicado) para que las cifras de
los dos repos sean comparables. Tocar `marcas_de_unidad()` invalidaría el censo
de 2026-08-24 y rompería esa comparabilidad en silencio — justo lo que aquel
módulo se esfuerza en evitar. Esta marca vive aparte, con su propia versión, y se
suma al diagnóstico sin contaminar el existente.

El método: la biblioteca dice qué palabra es
---------------------------------------------
Dos tokens adyacentes `a` y `b` son un corte si su unión `ab` es palabra y cada
trozo por separado es más raro que la unión. «Palabra» se decide por dos vías:

1. **El léxico de la casa** (`04_ops/manifests/lexico-castellano.json`, 101.526
   formas de los 208 raw que el censo declaró limpios), que ya construye
   `construir_lexico.py`. Es la vía fuerte, y sólo vale para el castellano.
2. **El propio fichero**, cuando la unión aparece >= 5 veces entera en él. Es la
   vía que funciona en alemán, italiano o inglés, donde no hay léxico de la casa.

El filtro de frecuencia es lo que separa `enriqueci`+`miento` —los dos trozos
raros, la unión frecuente— de `su`+`gestión`, donde `su` es de las palabras más
frecuentes del castellano y por tanto la pareja no salta. Ese filtro tiene un
coste conocido y buscado: **un corte real cuyo primer trozo sea una palabra
común se pierde**. Se prefiere perderlo a inventarlo — la cifra es un suelo,
igual que en `fidelidad_capa`.

Lo que este detector NO hace
-----------------------------
No repara nada. `reparar_capa.py` deshace sustituciones de carácter, que son
regulares; un corte no lo es —unir `su gestión` sin más destroza «durante su
gestión ministerial»—. La reparación de cortes tiene que ser decidida en la
lectura, no en el fichero, y para eso está `buscar_tolerante.py`.

Uso:
    python palabras_partidas.py 01_raw/books/obras-completas-vol-01-pdf-pages.md
    python palabras_partidas.py --censo 01_raw/books/*.md
"""

from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import fidelidad_capa as F

MARCA_VERSION = "2026-11-04.1"   # cuenta el guion blando U+00AD (antes, 2026-08-26.1)

REPO_ROOT = Path(__file__).resolve().parent
LEXICO = REPO_ROOT / "04_ops" / "manifests" / "lexico-castellano.json"

# El mismo alfabeto que fidelidad_capa, más las vocales alemanas e italianas:
# la biblioteca no es sólo castellana y un corte alemán es un corte igual.
_LETRA_MULTI = F._LETRA + "ÄÖÜäöüßÀÈÌÒÙàèìòùÂÊÎÔÛâêîôû"
PALABRA = re.compile(rf"[{_LETRA_MULTI}]{{2,}}")

# Cuántas veces ha de aparecer la unión entera para creerse que es palabra.
MIN_UNION = 5
# Frecuencia mínima en el léxico de la casa para aceptar la vía fuerte.
MIN_LEXICO = 5

_lexico_cache: dict[str, int] | None = None


def lexico() -> dict[str, int]:
    """El léxico castellano de la casa. Vacío si aún no se ha construido."""
    global _lexico_cache
    if _lexico_cache is None:
        try:
            _lexico_cache = json.loads(LEXICO.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _lexico_cache = {}
    return _lexico_cache


# El separador manda, y distingue tres cosas que NO son la misma:
#   'apli car'    espacio puro          -> corte de OCR
#   'apli- car'   guion y espacio/salto -> corte de maquetación (silabeo del original)
#   'tras-torno'  guion sin espacio     -> palabra con guion: AUTORÍA, no corte
# Un guion deliberado entre dos palabras debe conservarse.
# El segundo trozo va en LOOKAHEAD, y no es un detalle: sin él la regex consume
# las dos palabras y el siguiente match ya no puede empezar en la segunda, de
# modo que sólo se ven las parejas en posición par. Sobre «La bibliogra fía de
# la histe ria» se veía «La|bibliogra» y luego «fía|de» — nunca la pareja que
# importa. Lo cazó el canario de este módulo el 2026-08-26, DESPUÉS de haber
# corrido con él un censo de 456 ficheros que hubo que rehacer.
# Guion blando U+00AD entre dos letras, con el blanco o el salto que lo siga.
_GUION_BLANDO_EN_PALABRA = re.compile(
    rf"(?<=[{_LETRA_MULTI}])\xad(?:[ \t]*\r?\n[ \t]*)?(?=[{_LETRA_MULTI}])"
)

PAREJA = re.compile(
    rf"([{_LETRA_MULTI}]{{2,}})(-?[ \t]*\n[ \t]*|-[ \t]+|[ \t]+)(?=([{_LETRA_MULTI}]{{2,}}))"
)


def cortes_de_texto(
    texto: str, usar_lexico: bool = True, min_union: int = MIN_UNION
) -> tuple[int, collections.Counter, dict[str, tuple[str, str]], collections.Counter]:
    """(nº de palabras, cortes por forma, un ejemplo de cada, cortes por tipo)."""
    tokens = [t.lower() for t in PALABRA.findall(texto)]
    propia = collections.Counter(tokens)
    lex = lexico() if usar_lexico else {}

    cortes: collections.Counter = collections.Counter()
    ejemplos: dict[str, tuple[str, str]] = {}
    tipos: collections.Counter = collections.Counter()
    for m in PAREJA.finditer(texto):
        a, sep, b = m.group(1).lower(), m.group(2), m.group(3).lower()
        union = a + b
        # ¿es palabra? por el léxico de la casa, o por el propio fichero
        fuerza = max(propia.get(union, 0), min_union if lex.get(union, 0) >= MIN_LEXICO else 0)
        if fuerza < min_union:
            continue
        # y cada trozo ha de ser más raro que la unión (esto salva 'su gestión')
        if propia[a] >= fuerza or propia[b] >= fuerza:
            continue
        cortes[union] += 1
        ejemplos.setdefault(union, (a, b))
        tipos["silabeo" if "-" in sep else "ocr"] += 1
    return len(tokens), cortes, ejemplos, tipos


def medir(texto: str, usar_lexico: bool = True) -> dict[str, Any]:
    """Mide un markdown ya sin andamio. Devuelve el dato y su veredicto."""
    limpio = F.sin_andamio(texto)
    # El guion blando entre dos letras es un corte seguro para la búsqueda
    # literal: no hace falta que el léxico lo confirme, lo puso el maquetador.
    # Se cuenta aparte y se une antes de lo demás, para que «repre\u00adsen\u00adta\u00adtion»
    # no se cuente como cuatro palabras ni vuelva a salir como corte de OCR.
    blandos = len(_GUION_BLANDO_EN_PALABRA.findall(limpio))
    limpio = _GUION_BLANDO_EN_PALABRA.sub("", limpio).replace("\u00ad", "")
    palabras, cortes, ejemplos, tipos = cortes_de_texto(limpio, usar_lexico)
    total = sum(cortes.values()) + blandos
    por_mil = 1000.0 * total / palabras if palabras else 0.0
    return {
        "marca": "palabras_partidas",
        "version": MARCA_VERSION,
        "palabras": palabras,
        "cortes": total,
        "cortes_ocr": tipos.get("ocr", 0),
        "cortes_silabeo": tipos.get("silabeo", 0),
        "cortes_guion_blando": blandos,
        "formas_distintas": len(cortes),
        "por_mil": round(por_mil, 3),
        "veredicto": banda(por_mil),
        "top": [
            {"forma": u, "veces": c, "trozos": list(ejemplos[u])}
            for u, c in cortes.most_common(15)
        ],
    }


def banda(por_mil: float) -> str:
    """Traduce la medida a una frase. Calibrada sobre los 456 raw del 2026-08-26.

    Los cortes son el umbral entre las poblaciones que el censo separa: la
    mediana de lo nativo es 0,02 y la de lo escaneado 3,70, con el p75 en 11,23.
    Reparto resultante: 257 ficheros limpios, 42 con algún corte, 40 que parten
    palabras, 117 que las parten mucho.

    La asimetría es deliberada: ante la duda se avisa, nunca se absuelve. Un
    testigo marcado de más cuesta una comprobación; uno absuelto de más cuesta
    un recuento falso que nadie vuelve a mirar.
    """
    if por_mil >= 5.0:
        return ("PARTE MUCHO — ningún recuento literal vale aquí; "
                "buscar sólo con buscar_tolerante.py")
    if por_mil >= 1.0:
        return ("PARTE PALABRAS — todo recuento es un suelo; "
                "usar buscar_tolerante.py para cualquier término que importe")
    if por_mil >= 0.2:
        return "ALGÚN CORTE — comprobar con buscar_tolerante.py los términos clave"
    return "SIN CORTES APRECIABLES — la búsqueda literal es fiable"


def medir_fichero(path: Path, usar_lexico: bool | None = None) -> dict[str, Any]:
    texto = path.read_text(encoding="utf-8", errors="replace")
    if usar_lexico is None:
        usar_lexico = True
    salida = medir(texto, usar_lexico)
    salida["fichero"] = str(path)
    return salida


# --------------------------------------------------------------------------
# El canario
# --------------------------------------------------------------------------
# Mismo motivo que en `buscar_tolerante.py`: el 2026-08-26 este detector mintió
# dos veces antes de asentarse, y las dos mentiras eran plausibles. Corre solo,
# antes de medir nada, y aborta si falla. Los casos son los fallos que hubo, no
# ejemplos inventados: un canario que nunca ha visto fallar no prueba nada.

_CASOS = [
    # (texto, cortes esperados, qué prueba)
    ("La bibliogra fía de la histe ria y las repre sentaciones. "
     "La bibliografía, la histeria, las representaciones. "
     "Bibliografía, histeria, representaciones, bibliografía, histeria, representaciones. "
     "bibliografía histeria representaciones bibliografía histeria representaciones",
     3, "el corte con espacio puro SÍ se cuenta"),

    ("el lugar de un vuelco, de un tras-torno; esos tras-tornos de la neurosis; "
     "proyección y tras-torno. El trastorno hacia lo contrario. Trastorno, trastorno, "
     "trastorno, trastorno, trastorno del afecto, trastorno.",
     0, "el guion SIN espacio es autoría, no OCR: 'tras-torno' no cuenta"),

    ("durante su gestión ministerial. Su gestión, su gestión, su gestión, "
     "su gestión, su gestión de la cosa pública.",
     0, "'su gestión' no salta: 'su' es palabra frecuente por sí sola"),

    ("Un texto perfectamente limpio, sin ningún corte, escrito de corrido y sin "
     "nada que reparar en ninguna de sus palabras completas.",
     0, "un texto sano da cero"),
]

_CASOS_GUION_BLANDO = [
    # (texto, cortes de guion blando esperados, qué prueba)
    ("terms of metaphysical presence and repre\xadsen\xadta\xadtion as “information.” But \n"
     "further, and most importantly", 3, "guion blando dentro de la línea: tres cortes en una palabra"),
    ("I argue for a “Copernican revolution” in under\xad\nstanding the relation of documents",
     1, "guion blando a final de línea, con el salto detrás"),
    ("Gorichanaz, who \xadorganized and hosted the Document Academy conference",
     0, "guion blando suelto al principio: no parte nada y no se cuenta"),
    ("I argue for a revolution in under\xad\r\nstanding the relation of documents",
     1, "guion blando a final de línea con CRLF (Sol, S10)"),
]


def canario(verboso: bool = False) -> list[str]:
    """Comprueba el detector contra casos de respuesta conocida.

    Además de los casos, la invariante: un texto partido tiene que medir MÁS
    que el mismo texto sin partir. Sin ella, un detector que devuelve siempre
    cero pasaría por prudente.
    """
    fallos = []
    for texto, esperado, que_prueba in _CASOS:
        _, cortes, _, _ = cortes_de_texto(texto, usar_lexico=False, min_union=3)
        total = sum(cortes.values())
        if total != esperado:
            fallos.append(f"{que_prueba}: esperaba {esperado} y da {total} "
                          f"({dict(cortes)})")
        elif verboso:
            print(f"  ok  {que_prueba}")

    # La invariante: partir un texto no puede bajar la medida.
    # El texto sano ha de repetir la palabra bastante más veces de las que se
    # parten: el filtro exige que cada trozo sea ESTRICTAMENTE más raro que la
    # unión, así que partir la mitad de las apariciones no dispararía nada.
    # El guion blando (U+00AD). Lo pone el maquetador del PDF dentro de la palabra
    # y la búsqueda literal puede perderla. Estos casos cubren ambas posiciones.
    for texto, esperado, que_prueba in _CASOS_GUION_BLANDO:
        m = medir(texto, usar_lexico=False)
        absuelve = m["veredicto"].startswith("SIN CORTES")
        if m.get("cortes_guion_blando") != esperado or absuelve != (esperado == 0):
            fallos.append(f"{que_prueba}: esperaba {esperado} cortes de guion blando "
                          f"({'absolver' if esperado == 0 else 'no absolver'}); "
                          f"da {m.get('cortes_guion_blando')} · {m['veredicto']}")
        elif verboso:
            print(f"  ok  {que_prueba}")

    sano = "la representación " * 9
    roto = sano.replace("representación", "repre sentación", 2)
    _, c_sano, _, _ = cortes_de_texto(sano, usar_lexico=False, min_union=3)
    _, c_roto, _, _ = cortes_de_texto(roto, usar_lexico=False, min_union=3)
    if sum(c_roto.values()) <= sum(c_sano.values()):
        fallos.append(f"partir el texto no sube la medida: sano "
                      f"{sum(c_sano.values())}, roto {sum(c_roto.values())}")
    elif verboso:
        print("  ok  invariante: el texto partido mide más que el sano "
              f"({sum(c_roto.values())} > {sum(c_sano.values())})")
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
        print("EL DETECTOR NO PASA SU PROPIO CANARIO — no se mide nada:",
              file=sys.stderr)
        for f in fallos:
            print("  · " + f, file=sys.stderr)
        raise SystemExit(2)

    parser = argparse.ArgumentParser(
        description=(
            "Mide cuántas palabras partió el OCR en uno o varios raw. "
            "Es la marca que a fidelidad_capa.py le falta, y se declara aparte "
            "para no invalidar su censo."
        )
    )
    parser.add_argument("--canario", action="store_true",
                        help="correr sólo el canario, en voz alta, y salir")
    parser.add_argument("ficheros", nargs="*", type=Path)
    parser.add_argument("--censo", action="store_true",
                        help="salida JSON de todos los ficheros, para 04_ops/manifests/")
    parser.add_argument("--sin-lexico", action="store_true",
                        help="no usar el léxico castellano de la casa (corpus en otra lengua)")
    args = parser.parse_args()

    if args.canario:
        print("canario de palabras_partidas.py (%s):" % MARCA_VERSION)
        canario(verboso=True)
        print("pasa.")
        return
    if not args.ficheros:
        parser.error("hace falta al menos un fichero")

    filas = []
    for path in expandir(args.ficheros):
        try:
            filas.append(medir_fichero(path, usar_lexico=not args.sin_lexico))
        except OSError as exc:
            print(f"  !! {path}: {exc}", file=sys.stderr)

    if args.censo:
        print(json.dumps({"marca_version": MARCA_VERSION, "ficheros": filas},
                         ensure_ascii=False, indent=2))
        return

    for fila in filas:
        print("=" * 78)
        print(Path(fila["fichero"]).name)
        print(f"  {fila['palabras']} palabras · {fila['cortes']} cortes "
              f"({fila['cortes_ocr']} de OCR + {fila['cortes_silabeo']} de silabeo"
              + (f" + {fila['cortes_guion_blando']} de guion blando" if fila.get('cortes_guion_blando') else "")
              + ", "
              f"{fila['formas_distintas']} formas) · {fila['por_mil']} por mil")
        print(f"  {fila['veredicto']}")
        for t in fila["top"][:8]:
            a, b = t["trozos"]
            print(f"     {t['veces']:4d}  {t['forma']:<26}  <- '{a}' + '{b}'")


if __name__ == "__main__":
    main()
