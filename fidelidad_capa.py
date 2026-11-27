#!/usr/bin/env python3
"""Fidelidad de la capa de texto de un raw de Ateneo-KB.

Por qué existe
--------------
`ingest_to_raw.py` declaraba que el texto SALIÓ —páginas, huecos sin capa,
conversor, fecha— y nunca si lo que sale se puede leer. Son cosas distintas: un
raw con la capa podrida se ingiere en verde, porque sus caracteres están todos
ahí y son todos basura. El daño no es que una cita salga con erratas (eso se
ve): es que **produce falsos negativos al localizar**. Buscar «the tiny thread»
en un testigo que dice `die tiny thre~tcl` devuelve cero, y la conclusión que
sale es «no está en esa página» cuando sí está. Un testigo sucio no miente:
calla.

La ingesta mide la capa antes de que un lector empiece a citarla.

Procedencia del detector — NO es original de este repo
------------------------------------------------------
Las cuatro marcas, sus umbrales y su calibración vienen tal cual de
corpus/biblioteca/fidelidad.py del repositorio hermano de la mesa de traducción (no publicado), versión `2026-08-03.2`,
calibrada allí sobre 59 testigos y 7.769.831 palabras. No se reescribió el
criterio: se copió, y `DETECTOR_VERSION` viaja con la medida para que un cambio
allí invalide estas declaraciones en vez de desplazarlas en silencio. Si las dos
versiones divergen, las cifras de los dos repos dejan de ser comparables — que
es exactamente lo que se quiere que se note.

Lo que aquí cambia respecto de la Mesa, y por qué
-------------------------------------------------
La Mesa mide la capa del PDF con `fitz`. Aquí se mide **el markdown derivado**,
que es el texto que la KB usa de verdad: nadie busca dentro del PDF, se busca en
`01_raw/`. Medir el PDF cuando lo que se lee es el markdown daría un número
correcto sobre el objeto equivocado.

Las cifras son SUELO, no total: la corrupción puramente alfabética
(`propósi-lo` por `propósito`) es invisible para este detector.

Quinta marca: ausencia de diacríticos (2026-09-28, versión 2026-09-28.1-kb)
--------------------------------------------------------------------------
Portada desde el repositorio hermano, commit `16e2c78be` (no publicado; 2026-08-25, «la quinta
marca — un testigo sin diacríticos no está limpio»): funciones
`alfabeto_diacritico` / `umbral_diacritico` y tablas `DIACRITICOS_POR_IDIOMA`,
`IDIOMAS_SIN_DIACRITICOS`, `UMBRAL_DIACRITICOS_POR_IDIOMA` copiadas tal cual,
con su calibración (es n=159 sano desde 11.38, roto hasta 7.67; fr, it, de, pt,
ca provisionales). Como en la Mesa, la medida va APARTE y no entra en
`per_thousand_words`: mover ese suelo invalidaría las 104 fichas ya selladas.

Lo que cambia respecto de la Mesa: allí el idioma lo declara el manifiesto
(`witness.language`); aquí las fichas no traen idioma, así que
`medir_markdown(markdown, idioma=None)` acepta uno explícito y, si falta, lo
DETECTA por palabras función (`detectar_idioma`) y lo declara en
`diacritics_language_detected`. Y la Mesa AVISA en `atencion`; aquí el aviso
vive en `banda`, que es lo que lee el humano.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

# Cambiar el detector cambia todas las cifras. Diverge de la Mesa desde
# 2026-09-16 y por eso la versión lo dice: ver SIGLA, más abajo. No tocar sin
# volver a medir la KB entera. 2026-09-28.1-kb: añade la quinta marca
# (diacríticos); las cuatro primeras y sus cifras no se mueven.
DETECTOR_VERSION = "2026-09-28.1-kb"
DETECTOR_ORIGIN = "mesa-de-traduccion/corpus/biblioteca/fidelidad.py"

_LETRA = r"a-zA-ZáéíóúüñÁÉÍÓÚÜÑ"
_VOCAL = r"aáeéiíoóuúüAÁEÉIÍOÓUÚUÜ"

# 1. Dígito INTERIOR, con letras a los dos lados: `imagi1111ndo`, `Paid6s`.
#    Exigir letra DETRÁS del dígito es lo único que separa la suciedad de una
#    llamada de nota pegada a la palabra (`Zambrano11`). No relajarlo.
MARCA_DIGITO = re.compile(rf"[{_LETRA}]+[0-9]+[{_LETRA}]+")

# 2. Signo tipográfico incrustado entre letras: `h11l·cmos`, `c|ue`, `Bewu~tsein`.
#    La barra `/` NO está, deliberadamente: en markdown venido de EPUB disparaba
#    el 99,8 % del ruido con rutas internas del libro electrónico.
MARCA_SIGNO = re.compile(rf"[{_LETRA}]+[·|•¡¿\\_~^<>«»][{_LETRA}]+")

# 3. Palabra larga sin una sola vocal: `llnc`, `ntrc`, `dndcs`.
MARCA_SIN_VOCAL = re.compile(rf"^[{_LETRA}]{{4,}}$")

# 3bis. La excepción de la regla 3: una SIGLA no es un OCR roto.
#    En un artículo con capa intacta las 63 marcas eran cuatro tokens:
#    `LLMs` (45), `Psych` (15), `ssrn` (2) y `PMLR` (1). En una KB que ingiere papers de IA en
#    inglés, `LLMs` es la palabra más repetida del corpus y no tiene vocales.
#
#    Esto NO es la hipótesis de la repetición, que el censo de los 905 ya tumbó
#    más arriba: no se mira cuántas veces aparece un token, sino su forma.
#    Es el mismo movimiento que `sin_andamio` —quitar de raíz lo que no es
#    prosa— y no un umbral estadístico.
#
#    El límite es estrecho a propósito, porque el coste es asimétrico y aquí se
#    yerra del lado caro: se silencian SOLO las mayúsculas de dos a cinco
#    letras, con una `s` final opcional (`LLMs`, `RLHF`, `PMLR`). Seis o más
#    siguen contando, porque un titular en versalitas destrozado por el OCR no
#    es una sigla. `Psych` y `ssrn` siguen contando: separarlas del ruido
#    exigiría un diccionario, y eso ya no es medir.
SIGLA = re.compile(r"^[A-ZÁÉÍÓÚÜÑ]{2,5}s?$")

# 3ter. La otra excepción de la regla 3: en inglés la `y` ES vocal.
#    `myth`, `rhythm`, `hymn`, `nymph`, `crypt`, `lynx` no llevan vocal para un
#    detector calibrado en castellano, donde la `y` es consonante o conjunción.
#    Censo del 2026-09-16 sobre los .md de `01_raw` (ripgrep, 12 s): 5.258
#    apariciones sin vocal llevan `y`, encabezadas por `myth` (957), `YHWH`
#    (525), `phys` (415), `rhythm` (244) y nombres propios —Lynch, Pylyshyn,
#    Flynn—. Ninguno es ruido.
#
#    Pero la cola SÍ lo es: `zlpwcfwkcyj`, `yyhhvvhh`, `ypvrrrtnm`, `yptxj`.
#    Por eso la excepción no es «la y es vocal» a secas, que cegaría el
#    detector ante esa basura, sino la forma de una palabra inglesa: de cuatro
#    a seis letras y UNA sola `y`. Deja fuera lo largo y lo repetido, que es
#    donde vive el ruido.
#
#    Lo que aun así se cuela: 288 tokens de una sola aparición que pasan el
#    filtro por ser cortos (`zwry`, `ztyw`). Medido: no mueven ningún veredicto
#    —los ficheros que los contienen están sucios por otras tres reglas a la
#    vez—, y se declaran en `y_as_vowel_skipped` para que se puedan revisar.
Y_VOCAL_INGLESA = re.compile(r"^(?=[^yY]*[yY][^yY]*$)[A-Za-z]{4,6}$")

# 4. Cinco consonantes seguidas. INFORMATIVA, fuera del cómputo: sus aciertos
#    son `Psych`, `tzsch`, `Bewusst` — legítimos, no basura.
MARCA_CONSONANTES = re.compile(rf"[^{_VOCAL}\W\d_]{{5,}}")

PALABRA = re.compile(rf"[{_LETRA}]{{2,}}")

# ---------------------------------------------------------------------------
# 5. La AUSENCIA de diacríticos (portada de la Mesa, commit 16e2c78be)
# ---------------------------------------------------------------------------
# Las cuatro marcas de arriba buscan basura visible. Un OCR que pierde TODAS las
# tildes y eñes no deja ninguna: `Ruisenor` por `Ruiseñor`, `dano` por `daño` son
# palabras bien formadas que no existen. Ver el docstring del módulo y, en la
# Mesa, el commit citado para la calibración completa.
DIACRITICOS_POR_IDIOMA = {
    "es": "áéíóúüñÁÉÍÓÚÜÑ¿¡",
    "it": "àèéìíòóùúÀÈÉÌÒÙ",
    "de": "äöüÄÖÜß",
    "fr": "àâçéèêëîïôûùüÿœÀÂÇÉÈÊÎÔÛ",
    "pt": "ãáâàçéêíóôõúÃÁÂÇÉÊÍÓÔÕÚ",
    "ca": "àèéíïòóúüçÀÈÉÍÒÓÚÇ·",
}

# Idiomas sin diacríticos propios: la marca NO APLICA (un 0.0/mil sería un
# número correcto sobre el objeto equivocado).
IDIOMAS_SIN_DIACRITICOS = ("en", "la", "grc", "he", "ar")

UMBRAL_DIACRITICOS_POR_IDIOMA = {
    "es": 9.0,    # calibrado en la Mesa: n=159, sano desde 11.38, roto hasta 7.67
    "fr": 15.0,   # calibrado: n=6, sano desde 26.75
    "it": 6.0,    # población de 3: provisional
    "de": 6.0,    # sin población; el roto conocido da 0.00
    "pt": 6.0,
    "ca": 6.0,
}
UMBRAL_DIACRITICOS_POR_DEFECTO = 6.0


def alfabeto_diacritico(idioma: Any) -> str | None:
    """Los diacríticos que ese idioma debe traer, o None si no aplica."""
    if not idioma:
        return None
    clave = str(idioma).strip().lower().replace("_", "-").split("-")[0][:3]
    if clave in IDIOMAS_SIN_DIACRITICOS:
        return None
    return DIACRITICOS_POR_IDIOMA.get(clave[:2])


def umbral_diacritico(idioma: Any) -> float:
    """El suelo que ese idioma debe superar. Por idioma: sus suelos difieren."""
    if not idioma:
        return UMBRAL_DIACRITICOS_POR_DEFECTO
    clave = str(idioma).strip().lower().replace("_", "-").split("-")[0][:2]
    return UMBRAL_DIACRITICOS_POR_IDIOMA.get(clave, UMBRAL_DIACRITICOS_POR_DEFECTO)


# Detección de idioma, PROPIA de esta KB (la Mesa lo lee del manifiesto). Palabras
# función; se usan solo las que NO llevan diacrítico, para que la señal no
# dependa de lo que se mide (un testigo sin tildes se detecta igual). Gana el
# idioma con más aciertos si supera al segundo por 1.5x y hay al menos 200
# aciertos; si no, None y la marca no se mide (mejor callar que medir contra el
# idioma equivocado).
_FUNCION = {
    "es": "el la los las de del que y en un una por con para se su al lo como pero sus le ya o este entre cuando muy sin sobre me hasta hay donde quien desde todo nos durante todos uno les ni contra otros ese eso ante ellos e esto antes algunos unos yo otro otras otra tanto esa estos mucho quienes nada muchos cual poco ella estar estas algunas algo nosotros".split(),
    "en": "the of and to in is that it for as with was on be by this are at from or his an which have not but had they you were their one all we can her has there been if more when will would who so no".split(),
    "fr": "le la les des du de un une et est en que qui dans pour pas sur au aux avec ce ses son sa il elle nous vous ils ne se plus par mais ou comme cette sont ont fait aussi".split(),
    "it": "il lo la i gli le di del della dei delle un uno una e che in per con su non si come piu ma anche sono era ha ho questo questa quello nel nella alla al dal dalla lui lei noi voi loro".split(),
    "de": "der die das und ist von zu den mit sich des auf nicht ein eine als auch es an werden aus er hat dass sie nach wird bei einer um am sind noch wie einem einen so zum war haben nur oder aber vor zur bis mehr durch man sein wurde sei".split(),
    "pt": "o a os as de do da dos das que e em um uma para com nao se por mais como mas foi ao ele ela seu sua ou ser quando muito nos ja eu tambem so pelo pela ate isso entre era depois sem mesmo aos ter seus quem nas me esse eles voce tinha foram essa num nem suas meu minha numa".split(),
}
_PALABRA_MINUSCULA = re.compile(r"[^\W\d_]+", re.UNICODE)


def detectar_idioma(texto: str) -> str | None:
    """Idioma dominante por palabras función, o None si no hay señal clara."""
    from collections import Counter
    cuenta = Counter(p.lower() for p in _PALABRA_MINUSCULA.findall(texto))
    # Una palabra función compartida entre lenguas (`la`, `e`, `de`, `a`) suma a
    # ambas; el margen 1.5x es lo que las separa.
    puntos = {
        idioma: sum(cuenta[w] for w in set(palabras))
        for idioma, palabras in _FUNCION.items()
    }
    orden = sorted(puntos.items(), key=lambda kv: -kv[1])
    (mejor, n1), (_, n2) = orden[0], orden[1]
    if n1 < 200 or n1 < 1.5 * n2:
        return None
    return mejor
_TOKEN = re.compile(r"\S+")
_NO_LETRA = re.compile(rf"[^{_LETRA}]")
_TIENE_VOCAL = re.compile(rf"[{_VOCAL}]")

# Residuo de conversión: rutas, nombres de fichero y restos de etiqueta que un
# EPUB pasado a markdown arrastra consigo. NO es prosa del libro: nunca va a
# aparecer dentro de una cita, así que contarlo mediría el andamio, no la obra.
_RESIDUO = re.compile(
    r"\S*(?:://|[\w-]+\.(?:x?html?|jpe?g|png|gif|css|ncx|opf|svg)\b)\S*",
    re.IGNORECASE,
)

# Residuo de conversión propio de ESTA casa, medido el 2026-08-24 sobre los 905
# raw. Los tres salieron del censo, no de imaginar qué podría fallar:
#   · entidades HTML sin desescapar — `&#x2019;s`, `NLP&#x2014;Conclusions`.
#     Dos textos sanos daban 22,62 y 19,85 marcas por mil.
#   · etiquetas HTML sueltas pegadas a la palabra siguiente.
#   · marcadores de cita de ChatGPT en el Área de Uso Privado —
#     no pertenecen al texto de la fuente.
# Ninguno es prosa del documento: no van a aparecer nunca dentro de una cita.
_RESIDUO_CASA = re.compile(
    r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]{2,10});"      # entidades HTML
    r"|</?[a-zA-Z][a-zA-Z0-9]*\s*/?>"                 # etiquetas HTML sueltas
    r"|[-][^-\s]*",           # marcadores en el Área de Uso Privado
)

# Andamio propio de la KB, no del libro. El ancla `<!-- pdf_page: 007 -->` es la
# trampa de esta adaptación: `pdf_page` lleva un guion bajo entre letras y
# MARCA_SIGNO lo cuenta. Las anclas pueden sumar una marca falsa por página.
#
# ⚠️ Por la ruta de partir por página el ancla ya no aparece —`split` se la
# come— y quitarla ahí no cambia una sola marca. Se comprobó: el efecto es cero
# por esa ruta y 181 por la del fichero entero. La limpieza hace falta para los
# raw SIN anclas de página, que se miden de una pieza y sí llevan comentarios
# dentro. Un guardián que solo sirve en una de las dos rutas sigue siendo
# necesario en esa.
_ANCLA_HTML = re.compile(r"<!--.*?-->", re.DOTALL)
_CABECERA_PAGINA = re.compile(r"^#{1,6}\s*PDF page\s+\d+.*$", re.IGNORECASE | re.MULTILINE)

# Frontmatter YAML de la casa. Sus claves son snake_case (`fuente_raw`,
# `source_file`, `source_role`) y MARCA_SIGNO las cuenta como capa podrida.
# Medido en el tomo VI de una obra completa (EPUB): sus 9 marcas —todas— eran
# el frontmatter. Poco en un fichero de 100.686 palabras; en un raw corto, toda
# la medida.
_FRONTMATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)

# Dos conversores, dos anclas. `pdf_pages` escribe el comentario HTML; los raw
# más viejos de la casa escriben una cabecera markdown `## PDF page 293 /
# printed 281` (318 de ellas en el PDF de ese mismo tomo). Si
# solo se reconociera la primera, medio corpus se mediría de una pieza y
# `units_affected` —el dato que dice si la suciedad está repartida o concentrada
# en cuatro páginas— saldría siempre 1/1, que es no decir nada.
MARCADOR_PAGINA = re.compile(
    r"^(?:<!--\s*pdf_page:\s*\d+\s*-->|#{1,6}\s*PDF page\s+\d+.*)\s*$",
    re.MULTILINE | re.IGNORECASE,
)

UNIDAD_PAGINAS = "pdf_page_markers"
UNIDAD_FICHERO = "whole_file"


def sin_andamio(texto: str) -> str:
    """Quita andamio de la casa, rutas y nombres de fichero: deja la prosa."""
    texto = _FRONTMATTER.sub(" ", texto)
    texto = _ANCLA_HTML.sub(" ", texto)
    texto = _CABECERA_PAGINA.sub(" ", texto)
    texto = _RESIDUO_CASA.sub(" ", texto)
    return _RESIDUO.sub(" ", texto)


def marcas_de_unidad(texto: str) -> dict[str, int]:
    """Cuenta las marcas de una unidad. Las tres primeras puntúan; la cuarta no."""
    sin_vocal = 0
    siglas = 0
    con_y = 0
    for token in _TOKEN.findall(texto):
        limpio = _NO_LETRA.sub("", token)
        if MARCA_SIN_VOCAL.match(limpio) and not _TIENE_VOCAL.search(limpio):
            if SIGLA.match(limpio):
                siglas += 1  # no se pierde: se declara en `acronyms_skipped`
                continue
            if Y_VOCAL_INGLESA.match(limpio):
                con_y += 1  # ídem, en `y_as_vowel_skipped`
                continue
            sin_vocal += 1
    return {
        "digit": len(MARCA_DIGITO.findall(texto)),
        "sign": len(MARCA_SIGNO.findall(texto)),
        "vowelless": sin_vocal,
        "consonants": len(MARCA_CONSONANTES.findall(texto)),
        "acronyms": siglas,
        "y_vowel": con_y,
    }


# ---------------------------------------------------------------------------
# El discriminador — por qué el número de la Mesa NO basta aquí
# ---------------------------------------------------------------------------
# La Mesa avisa en su propio docstring: «fuera de esa población hay que volver a
# medir; un corpus con notación matemática o química movería el suelo». Esta KB
# ES esa otra población: tiene papers de arXiv, libros de programación y
# preprints de IA mezclados con escaneos de humanidades. Medido el 2026-08-24,
# un manual de programación de PLN sale a 35,22/mil —peor que el peor
# escaneo— y su texto es impecable: sus 3.668 marcas de signo son `input_ids`
# (×109), `from_pretrained` (×64), `batch_size` (×55). Código, no capa podrida.
#
# La primera hipótesis fue la REPETICIÓN: el OCR se equivoca distinto cada vez
# (`d1e`, `i1t`, `a1nd`, todas únicas) y un identificador de programa es el
# mismo token cien veces. Sobre siete ficheros elegidos parecía perfecta —
# escaneo sucio 0,85-0,92; el manual 0,23— y el censo de los 905 la tumbó.
#
# ⚠️ **La repetición NO separa suciedad de notación.** Un OCR que lee la ó como
# 6 produce `tambi6n`, `s6lo`, `relaci6n`: el mismo token corrupto tantas veces
# como aparezca la palabra. Con el umbral en 0,50 el censo declaró «no es OCR»
# a tres ensayos escaneados (r0,354, r0,14 y r0,434), que son escaneos sucios
# de verdad.
#
# Por eso el ratio **NO decide**. Un falso «SUCIO» le cuesta a un humano una
# mirada; un falso «no es OCR» le cuesta una cita mal firmada, porque esconde el
# testigo en vez de enseñarlo. El ratio se declara y avisa; el veredicto lo
# sigue dando el por-mil de la Mesa. Lo que sí se arregló de raíz es la causa
# real de aquellos falsos positivos: el residuo de conversión, que ahora se
# quita en `sin_andamio` antes de contar.
RATIO_SOSPECHOSO = 0.50

# Por debajo de esto el por-mil es inestable: en `vaswani-2017` bastaron 53
# marcas sobre 2.233 palabras para dar 25/mil. No se oculta la cifra — se dice
# que la población es corta.
PALABRAS_MINIMAS_FIABLES = 2000


def medir_unidades(
    unidades: Iterable[str], unidad: str, idioma: str | None = None
) -> dict[str, Any]:
    """Mide una secuencia de textos ya limpios de andamio.

    Recorre TODAS las unidades a propósito: un muestreo daría un número plausible
    sobre una población que nadie declaró, que es peor que no tenerlo.

    `idioma` activa la quinta marca (diacríticos). Se cuenta en el mismo
    recorrido: `unidades` suele ser un generador y recorrerlo dos veces daría
    cero sin decir por qué. Si es None, `medir_markdown` ya lo habrá detectado.
    """
    letras_diacriticas = alfabeto_diacritico(idioma)
    caracteres = 0
    diacriticos = 0
    palabras = 0
    total = 0
    afectadas = 0
    acumulado = {
        "digit": 0, "sign": 0, "vowelless": 0,
        "consonants": 0, "acronyms": 0, "y_vowel": 0,
    }
    vistos: dict[str, int] = {}

    for texto in unidades:
        if not texto.strip():
            continue
        total += 1
        palabras += len(PALABRA.findall(texto))
        marcas = marcas_de_unidad(texto)
        if letras_diacriticas:
            # Se cuenta sobre el texto con los blancos COMPACTADOS: el markdown de
            # esta KB viene a veces relleno de espacios de maquetación (medido
            # 2026-09-29: un ensayo escaneado tiene 16 caracteres
            # por palabra y 0,53 de blancos, y salía a 6,7/mil con sus tildes
            # intactas). La Mesa cuenta texto de `fitz`, sin ese relleno; aquí
            # hay que quitarlo para que el umbral calibrado allí siga valiendo.
            compacto = " ".join(texto.split())
            caracteres += len(compacto)
            diacriticos += sum(compacto.count(c) for c in letras_diacriticas)
        if marcas["digit"] or marcas["sign"] or marcas["vowelless"]:
            afectadas += 1
        for clave, valor in marcas.items():
            acumulado[clave] += valor
        # Solo las dos marcas que devuelven el token que las disparó. La de
        # `sin_vocal` no entra: no cambia el reparto y contarla obligaría a
        # rehacer su bucle.
        for token in MARCA_SIGNO.findall(texto) + MARCA_DIGITO.findall(texto):
            vistos[token] = vistos.get(token, 0) + 1

    fuertes = acumulado["digit"] + acumulado["sign"] + acumulado["vowelless"]
    apariciones = sum(vistos.values())
    ratio = round(len(vistos) / apariciones, 3) if apariciones else 1.0
    por_mil = round(fuertes / palabras * 1000, 3) if palabras else 0.0

    medida = {
        "detector": DETECTOR_VERSION,
        "detector_origin": DETECTOR_ORIGIN,
        "unit": unidad,
        "words": palabras,
        "marks": {
            "digit": acumulado["digit"],
            "sign": acumulado["sign"],
            "vowelless": acumulado["vowelless"],
        },
        "consonants_informational": acumulado["consonants"],
        # Marcas de la regla 3 que eran siglas y NO se contaron (ver SIGLA).
        # Se declara para que un cero de este detector no esconda su motivo.
        "acronyms_skipped": acumulado["acronyms"],
        # Ídem para la `y` inglesa (ver Y_VOCAL_INGLESA).
        "y_as_vowel_skipped": acumulado["y_vowel"],
        "strong_marks": fuertes,
        "per_thousand_words": por_mil,
        "units_affected": afectadas,
        "units_total": total,
        # Cuántas de las marcas son tokens distintos. Alto = errores irrepetibles
        # = OCR. Bajo = el mismo token una y otra vez = notación legítima.
        "distinct_mark_ratio": ratio,
        "distinct_marks": len(vistos),
        "most_repeated_marks": [
            {"token": t, "count": n}
            for t, n in sorted(vistos.items(), key=lambda kv: -kv[1])[:5]
        ],
    }
    # Quinta marca: aparte, fuera de per_thousand_words (ver docstring del módulo).
    if letras_diacriticas and caracteres:
        medida["diacritics_language"] = str(idioma)
        medida["diacritics_per_thousand"] = round(diacriticos / caracteres * 1000, 2)
    return medida


def medir_markdown(markdown: str, idioma: str | None = None) -> dict[str, Any]:
    """Mide el markdown derivado. Si lleva anclas de página, la unidad es la página.

    `idioma` explícito manda; si falta se detecta (`detectar_idioma`) sobre el
    texto sin andamio y se declara en `diacritics_language_detected`.
    """
    detectado = False
    if idioma is None:
        idioma = detectar_idioma(sin_andamio(markdown))
        detectado = idioma is not None
    if MARCADOR_PAGINA.search(markdown):
        trozos = MARCADOR_PAGINA.split(markdown)
        medida = medir_unidades((sin_andamio(t) for t in trozos), UNIDAD_PAGINAS, idioma)
    else:
        medida = medir_unidades([sin_andamio(markdown)], UNIDAD_FICHERO, idioma)
    if detectado and "diacritics_language" in medida:
        medida["diacritics_language_detected"] = True
    # Independiente: no cambia marcas, umbrales ni DETECTOR_VERSION.
    from erratas_palabra import medir as medir_erratas
    medida["wordlike_errors"] = medir_erratas(markdown)
    medida["control_characters"] = medir_controles(markdown)
    return medida




# Medida independiente: controles C0/DEL salvo TAB, LF y CR. No entra en
# las marcas calibradas. Se mide el markdown íntegro, como el censo.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CONTROL_RUN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")


def medir_controles(markdown: str) -> dict[str, Any]:
    """Posición verificable; especie orientativa, nunca recuperación de letras.

    inside_word exige letra Unicode inmediatamente a ambos lados. Las rachas
    entre letras se cuentan aparte para no ocultar su subconteo en datos rotos.
    broken_encoding señala alternancia densa de NUL/letras en el entorno local
    (20 caracteres a cada lado, al menos 4 NUL y >= uno por dos letras).
    possible_ligature es un resto sospechoso: también puede ser guion perdido,
    signo matemático u otra avería. No certifica que falte fi/fl/ff.
    """
    species = dict(possible_ligature=0, page_break=0, broken_encoding=0)
    by_codepoint: dict[str, int] = {}
    samples = []
    inside = 0
    for match in _CONTROL.finditer(markdown):
        i = match.start()
        c = match.group()
        code = f"U+{ord(c):04X}"
        by_codepoint[code] = by_codepoint.get(code, 0) + 1
        if not (i and i + 1 < len(markdown)
                and markdown[i-1].isalpha() and markdown[i+1].isalpha()):
            continue
        inside += 1
        window = markdown[max(0, i-20):i+21]
        nulls = window.count('\x00')
        letters = sum(ch.isalpha() for ch in window)
        if c == '\x0c':
            kind = 'page_break'
        elif nulls >= 4 and nulls * 2 >= letters:
            kind = 'broken_encoding'
        else:
            kind = 'possible_ligature'
        species[kind] += 1
        # Dos muestras por especie; ASCII escapado hace visibles los controles.
        if sum(s['species'] == kind for s in samples) < 2:
            samples.append(dict(offset=i, codepoint=code, species=kind,
                                context=ascii(markdown[max(0,i-16):i+17])))
    between_runs = sum(len(m.group()) for m in _CONTROL_RUN.finditer(markdown)
        if m.start() and m.end() < len(markdown)
        and markdown[m.start()-1].isalpha() and markdown[m.end()].isalpha())
    return dict(detector='2026-10-13.1-controls', total=sum(by_codepoint.values()),
                inside_word=inside, between_letter_runs=between_runs,
                by_codepoint=by_codepoint, by_species=species, samples=samples,
                decision='review' if inside else 'no_interior_controls_not_proof_of_clean_text')


def aviso_controles(measure: dict[str, Any]) -> str:
    """Aviso separado del veredicto calibrado; vacío cuando no hay interiores."""
    if not measure['inside_word']:
        return ''
    kinds = measure['by_species']
    return (f"Controles: REVISAR {measure['inside_word']} entre letras "
            f"(posible ligadura/otro signo perdido: {kinds['possible_ligature']}; "
            f"salto de página: {kinds['page_break']}; "
            f"patrón de codificación rota: {kinds['broken_encoding']}). "
            "Pueden ocultar palabras al buscar; cotejar el original. "
            + '; '.join(s['context'] for s in measure['samples']))

def fidelidad_no_aplica(razon: str) -> dict[str, Any]:
    """«No aplica» hay que DECIRLO.

    Un campo ausente significa «nadie lo ha mirado», que es el estado que esto
    vino a terminar; un `not_applicable` con su razón significa «se miró y no
    había nada que medir».
    """
    return {
        "detector": DETECTOR_VERSION,
        "detector_origin": DETECTOR_ORIGIN,
        "not_applicable": razon,
    }


# ---------------------------------------------------------------------------
# Bandas de lectura — para el humano, NO para el JSON
# ---------------------------------------------------------------------------
# El sidecar guarda el número desnudo. Las bandas ordenan a quien lee la salida
# del script, no a quien lee la ficha, y por eso no se serializan. Vienen del
# reparto medido en la Mesa el 2026-08-03 sobre 59 testigos.
def banda(
    por_mil: float,
    ratio: float = 1.0,
    palabras: int = 10**9,
    diacriticos: float | None = None,
    idioma: str | None = None,
) -> str:
    """Traduce la medida a una frase. El por-mil manda; el ratio solo avisa.

    `diacriticos`/`idioma` (quinta marca): si la densidad no supera el umbral del
    idioma, el veredicto de las cuatro marcas NO puede absolver: se antepone
    SIN DIACRÍTICOS. Un testigo limpio por las cuatro marcas y sin tildes no está
    limpio, está mudo.
    """
    if por_mil >= 2.0:
        veredicto = "SUCIO — ninguna cita se copia de aquí; se reconstruye contra el original"
    elif por_mil >= 0.8:
        veredicto = "CON RUIDO — cotejar toda cita literal antes de firmarla"
    elif por_mil >= 0.2:
        veredicto = "aceptable — ruido de fondo normal en escaneo"
    else:
        veredicto = "limpio"

    if diacriticos is not None and diacriticos <= umbral_diacritico(idioma):
        veredicto = (
            f"SIN DIACRÍTICOS ({diacriticos}/mil en «{idioma}», umbral "
            f"{umbral_diacritico(idioma)}) — la capa perdió tildes/eñes y las "
            f"palabras que salen no son las del original; un «no localizado» "
            f"aquí no es un dato. Lectura de las cuatro marcas: {veredicto}"
        )

    avisos = []
    if palabras < PALABRAS_MINIMAS_FIABLES:
        avisos.append(f"población corta ({palabras} palabras): el por-mil es inestable")
    if por_mil >= 0.8 and ratio < RATIO_SOSPECHOSO:
        avisos.append(
            f"pocas marcas distintas (ratio {ratio}): puede ser notación legítima "
            "—código, fórmulas— o una sustitución sistematica de OCR (la o acentuada leida como 6). "
            "Mirar most_repeated_marks para saber cuál"
        )
    return veredicto + ("".join(f" [{a}]" for a in avisos))


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description=(
            "Mide la fidelidad de la capa de texto de uno o varios raw de la KB. "
            "Sin --write solo informa; no toca ningún fichero."
        )
    )
    parser.add_argument("paths", nargs="+", help="Ficheros .md de 01_raw a medir.")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Escribe la medida en el .meta.json contiguo (clave text_layer_fidelity).",
    )
    parser.add_argument("--json", action="store_true", help="Salida en JSON, una línea por fichero.")
    args = parser.parse_args()

    for bruto in args.paths:
        ruta = Path(bruto)
        if not ruta.exists():
            print(f"AUSENTE  {ruta}")
            continue
        medida = medir_markdown(ruta.read_text(encoding="utf-8", errors="replace"))

        if args.json:
            print(json.dumps({"file": str(ruta), **medida}, ensure_ascii=False))
        else:
            from erratas_palabra import lectura_integrada
            print(
                f"{medida['per_thousand_words']:8.3f}/mil  "
                f"ratio {medida['distinct_mark_ratio']:<5}  "
                f"{medida['units_affected']:>5}/{medida['units_total']:<5} unidades  "
                f"{medida['words']:>9} palabras  "
                f"{ruta.name}\n"
                f"          {lectura_integrada(banda(medida['per_thousand_words'], medida['distinct_mark_ratio'], medida['words'], medida.get('diacritics_per_thousand'), medida.get('diacritics_language')), medida['wordlike_errors'])}"
            )

        if args.write:
            meta = ruta.with_suffix(".meta.json")
            if not meta.exists():
                print(f"          sin sidecar: no se escribe ({meta.name})")
                continue
            payload = json.loads(meta.read_text(encoding="utf-8"))
            payload["text_layer_fidelity"] = medida
            meta.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"          escrito en {meta.name}")


if __name__ == "__main__":
    main()
