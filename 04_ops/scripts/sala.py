"""Genera el índice Markdown de una sala temática, por listas o por programa."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from itertools import accumulate
from pathlib import Path

KB = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KB))
import palabras_partidas  # noqa: E402

FUENTES = KB / "02_wiki" / "fuentes"
RAW = KB / "01_raw"
CACHE = KB / "tmp" / "cache-salas.json"  # Cache of raw text measurements.
MEMORIA = {}
LIGADURAS = str.maketrans(dict(zip("ﬀﬁﬂﬃﬄﬅﬆ", ("ff", "fi", "fl", "ffi", "ffl", "st", "st"))))


def nombre_sala(valor: str) -> str:
    if not re.fullmatch(r"[\w-]+", valor):
        raise argparse.ArgumentTypeError("Usa letras, números, guiones o guiones bajos.")
    return valor


def frontmatter(texto: str) -> dict:
    m = re.match(r"---\n(.*?)\n---", texto, re.S)
    campos, clave = {}, None
    for linea in (m.group(1).splitlines() if m else []):
        if re.match(r"^\w[\w_]*:", linea):
            clave, _, valor = linea.partition(":")
            campos[clave] = valor.strip()
        elif clave and linea.strip().startswith("- "):
            campos[clave] = (campos[clave] + "\n" + linea.strip()[2:]).strip()
    return campos


def titulo(texto: str, reserva: str) -> str:
    m = re.search(r"^# (.+)$", texto, re.M)
    return m.group(1).strip() if m else reserva


def raws_de(fm: dict) -> list[str]:
    """Rutas `01_raw/….md` escritas en `fuentes:`.

    El lector del frontmatter las junta en un solo texto: una por línea, o una
    lista en línea `[a, b]`. La ficha a veces las pone entre comillas simples o
    dobles. Aquí se quitan y solo se queda el markdown de `01_raw`.
    """
    bruto = fm.get("fuentes", "")
    if not isinstance(bruto, str):
        bruto = ""
    bruto = bruto.strip()
    if len(bruto) >= 2 and bruto[0] == "[" and bruto[-1] == "]":
        trozos = bruto[1:-1].split(",")
    else:
        trozos = bruto.splitlines()
    salida = []
    for trozo in trozos:
        r = trozo.strip().strip("\"'")
        if r.startswith("01_raw/") and r.endswith(".md"):
            salida.append(r)
    return salida


_CORTE_BLANDO = re.compile(r"(?<=\w)\xad(?:[ \t]*\r?\n[ \t]*)?(?=\w)")


def unir_guion_blando(texto: str) -> str:
    """El texto como se lee impreso: las palabras que cortó el maquetador, unidas; el guion blando suelto, fuera."""
    return _CORTE_BLANDO.sub("", texto).replace("\xad", "")


def contar_raws(patron: str) -> dict[str, int]:
    """Cuenta sobre texto normalizado. Usa el mismo motor de expresiones con y sin rg."""
    voc = re.compile(patron, re.I)
    rg = shutil.which("rg")
    rutas = RAW.rglob("*.md")
    if rg and all(term.isalpha() for term in patron.split("|")):
        # Literal alternatives are safe to preselect. Include typesetting
        # artifacts and Python's additional case-insensitive letter matches.
        resultado = subprocess.run(
            [rg, "--no-config", "--hidden", "--no-ignore", "-ali0", "-g", "*.md",
             "-e", patron, "-e", "[\u00adﬀ-ﬆİıſK]", str(RAW)],
            capture_output=True,
        )
        if resultado.returncode in (0, 1):
            rutas = (Path(os.fsdecode(p)) for p in resultado.stdout.split(b"\0") if p)
    cuentas = {}
    for ruta in rutas:
        texto = normalizar(ruta.read_text(encoding="utf-8", errors="replace"))
        n = sum(1 for _ in voc.finditer(texto))
        if n:
            cuentas[ruta.relative_to(KB).as_posix()] = n
    return cuentas


def normalizar(texto: str) -> str:
    return unir_guion_blando(texto).translate(LIGADURAS)


def veces_del_ancla(texto_raw: str, ancla: str) -> int:
    """Cuenta el ancla en el texto normalizado."""
    plano = lambda s: re.sub(r"\s+", " ", normalizar(s))
    return plano(texto_raw).count(plano(ancla))


def memorizada(clave: str, rel: str, calcular):
    """Medidas caras de un raw, guardadas mientras el fichero no cambie (tamaño y fecha)."""
    st = (KB / rel).stat()
    huella = f"{clave}|{rel}|{st.st_size}|{st.st_mtime_ns}"
    if huella not in MEMORIA:
        MEMORIA[huella] = calcular()
    return MEMORIA[huella]


def guardar_memoria() -> None:
    """Guarda las medidas cuyo fichero sigue igual; las de ficheros cambiados o borrados se tiran."""
    vigentes = {}
    for huella, valor in MEMORIA.items():
        _, rel, tam, fecha = huella.rsplit("|", 3)
        p = KB / rel
        if p.exists() and f"{p.stat().st_size}|{p.stat().st_mtime_ns}" == f"{tam}|{fecha}":
            vigentes[huella] = valor
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(vigentes, ensure_ascii=False), encoding="utf-8")


def ficha_raw(rel: str) -> dict:
    """Lo que hace falta saber de un raw antes de usarlo."""
    return memorizada(f"ficha|{palabras_partidas.MARCA_VERSION}", rel, lambda: _ficha_raw(rel))


def _ficha_raw(rel: str) -> dict:
    md = KB / rel
    meta_path = md.with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    texto = md.read_text(encoding="utf-8", errors="replace")
    capa = meta.get("text_layer_fidelity", {}).get("per_thousand_words")
    partidas = palabras_partidas.medir(texto)["por_mil"]
    con_pagina = bool(re.search(r"<!-- (pdf_)?page: \d+ -->", texto))
    return {"capa": capa, "partidas": partidas, "pagina": con_pagina,
            "palabras": len(texto.split())}


def coma(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def miles(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def mejor_tramo(rel: str, voc: re.Pattern, ventana: int) -> tuple[float, str]:
    """Busca la ventana de mayor densidad en el texto."""
    tokens = normalizar((KB / rel).read_text(encoding="utf-8", errors="replace")).split()
    if not tokens:
        return 0.0, ""
    ventana = min(len(tokens), max(1, ventana))
    vistas = {t: 1 if voc.search(t) else 0 for t in set(tokens)}
    acumulado = list(accumulate((vistas[t] for t in tokens), initial=0))
    mejor, donde = 0, 0
    for i in range(len(tokens) - ventana + 1):
        acum = acumulado[i + ventana] - acumulado[i]
        if acum > mejor:
            mejor, donde = acum, i
    pagina = ""
    for j in range(donde, -1, -1):
        if tokens[j] in ("pdf_page:", "page:") and j + 1 < len(tokens):
            pagina = ("PDF p. " if tokens[j] == "pdf_page:" else "p. ") + tokens[j + 1].lstrip("0")
            break
    return 1000.0 * mejor / ventana, pagina


def avisos(r: dict) -> str:
    trozos = []
    if r["capa"] is None:
        trozos.append("capa sin medir")
    elif r["capa"] > 2.0:
        trozos.append(f"capa {coma(r['capa'])}‰: citar contra el original")
    if r["partidas"] >= 1.0:
        trozos.append(f"parte palabras ({coma(r['partidas'])}‰): `buscar_tolerante.py`")
    if not r["pagina"]:
        trozos.append("sin marca de hoja PDF")
    return "; ".join(trozos) if trozos else "con marca de hoja PDF"


def subtema(texto: str, subtemas: list) -> str:
    for nombre, patron in subtemas:
        if re.search(patron, texto, re.I):
            return nombre
    return "Otros"


def describir_raws(fila: dict, cache: dict) -> str:
    partes = []
    for rel in fila["raws"]:
        if not (KB / rel).exists():
            partes.append(f"`{rel}` (no está en disco)")
            continue
        cache.setdefault(rel, ficha_raw(rel))
        partes.append(avisos(cache[rel]) if len(fila["raws"]) == 1
                      else f"`{Path(rel).stem}`: {avisos(cache[rel])}")
    return " · ".join(partes) if partes else "sin raw enlazado"


def por_subtema(out: list, filas: list, clave, linea, sub: list) -> None:
    grupos = {}
    for fila in filas:
        grupos.setdefault(subtema(clave(fila), sub), []).append(fila)
    for nombre in [s[0] for s in sub] + ["Otros"]:
        if nombre in grupos:
            out.append(f"### {nombre} ({len(grupos[nombre])})")
            out.append("")
            out.extend(linea(f) for f in sorted(grupos[nombre], key=lambda f: f["id"]))
            out.append("")


def sala_por_listas(sala: str, definicion: dict) -> None:
    """Agrupa fichas y testigos por las listas declaradas."""
    miembros, testigos = definicion["miembros"], definicion.get("testigos", {})
    listas = definicion["listas"]
    faltan = [m for m in miembros if not (FUENTES / f"{m}.md").exists()]
    if faltan:
        sys.exit(f"miembros sin ficha en 02_wiki/fuentes/: {', '.join(faltan)}")
    dentro, propuestas = [], []
    for f in sorted(FUENTES.glob("*.md")):
        texto = f.read_text(encoding="utf-8")
        fm = frontmatter(texto)
        dominios = [d.strip() for d in fm.get("dominio", "").split("|")]
        if f.stem not in miembros and definicion.get("etiqueta_propuesta") not in dominios:
            continue
        fila = {"id": f.stem, "titulo": titulo(texto, f.stem), "estado": fm.get("estado", "?"),
                "forma": fm.get("forma", "?"), "clave": f"{f.stem} {titulo(texto, f.stem)}",
                "raws": [testigos[f.stem]] if f.stem in testigos else raws_de(fm)}
        if f.stem in miembros:
            fila["listas"] = " y ".join(listas[l]["etiqueta"] for l in miembros[f.stem])
            dentro.append(fila)
        else:
            propuestas.append(fila)

    cache = {}
    sub = definicion["subtemas"]
    sin_ficha = definicion.get("sin_ficha", [])
    con_texto = [s for s in sin_ficha if s.get("raw")]
    cuenta = {k: sum(1 for m in miembros.values() if m == [k]) for k in listas}
    out = [
        f"# Sala de {definicion['titulo']}",
        "",
        f"> Generada por `python 04_ops/scripts/sala.py {sala}` el {date.today().isoformat()} "
        f"a partir de `04_ops/salas/{sala}.json`.",
        ">",
        f"> {len(dentro)} fichas en la sala, "
        + ("de las listas del encargo: " if definicion.get("encargo") else
           "de las listas que eligió quien investiga: " if definicion.get("ratificada")
           else "**sin ratificar todavía**, de las listas propuestas: ")
        + "; ".join(f"{l['nombre']} (`{l['fuente']}`)" for l in listas.values()) + ". "
        + " · ".join(f"{cuenta[k]} solo de {l['etiqueta']}" for k, l in listas.items())
        + f" · {sum(1 for m in miembros.values() if len(m) > 1)} de más de una. "
        f"{len(propuestas)} fichas propuestas · {len(sin_ficha)} entradas de las listas sin ficha. "
        + definicion.get("criterio", "Las listas determinan la pertenencia.") + " "
        "El subtema lo pone el guion, solo por el título.",
        "",
        "Cada línea dice de qué lista viene y lo que hay que saber antes de citar: capa de texto sucia (citar "
        "contra el original), palabras partidas (buscar con `buscar_tolerante.py`, un cero literal no es un cero) "
        "y si se ha detectado una marca de hoja PDF. El texto es el testigo que fija la definición.",
        "",
        "## En la sala",
        "",
    ]
    if definicion.get("encargo"):
        out += [definicion["encargo"], ""]
    tramos = definicion.get("tramos", {})
    for ficha, lista in tramos.items():
        if ficha not in testigos:
            sys.exit(f"tramos de {ficha}: hace falta su testigo en `testigos`")
        texto = (KB / testigos[ficha]).read_text(encoding="utf-8")
        for t in lista:
            n = veces_del_ancla(texto, t["ancla"])
            if n != 1:
                sys.exit(f"tramo «{t['titulo']}» de {ficha}: el ancla sale {n} veces en {testigos[ficha]} (tiene que ser 1)")
    linea_tramos = lambda f: "".join(f"\n  - Leer: «{t['titulo']}» — {t['motivo']}. Empieza en «{t['ancla']}…»"
                                     for t in tramos.get(f["id"], []))
    por_subtema(out, dentro, lambda f: f["clave"],
                lambda f: f"- [[{f['id']}|{f['titulo']}]] — {f['forma']} · `{f['estado']}` · {f['listas']} · "
                          f"{describir_raws(f, cache)}{linea_tramos(f)}", sub)
    out += ["## Propuestas: fichas que ninguna lista nombra", "",
            f"Llevan `{definicion.get('etiqueta_propuesta')}` en `dominio:` y no están en ninguna lista. "
            "Entran en la sala cuando quien investiga las ratifica (se añaden a `miembros` en la definición).", ""]
    por_subtema(out, propuestas, lambda f: f["clave"],
                lambda f: f"- [[{f['id']}|{f['titulo']}]] — {f['forma']} · `{f['estado']}`", sub)
    out += ["## En las listas, sin ficha", "",
            ("Las entradas con texto en la casa se pueden leer y buscar en la consulta, rotuladas como sin ficha. "
             "Su inclusión no las convierte en fuentes curadas ni acredita sus citas. Las fichas se hacen por encargo."
             if definicion.get("incluir_sin_ficha") else
             "No entran en la sala ni en la consulta hasta que tengan ficha, y las fichas se hacen por encargo. "
             "Las que tienen texto en la casa están listas para fichar; las otras habría que conseguirlas."), ""]
    for rotulo, filas in (("Con texto en la casa", con_texto),
                          ("Sin texto en la casa", [s for s in sin_ficha if not s.get("raw")])):
        if filas:
            out += [f"### {rotulo} ({len(filas)})", ""]
            for s in filas:
                linea = f"- {s['entrada']} — {listas[s['lista']]['etiqueta']}"
                if s.get("raw"):
                    linea += f" · `{s['raw']}` · " + describir_raws({"raws": [s["raw"]]}, cache)
                out.append(linea + (f" · {s['nota']}" if s.get("nota") else ""))
            out.append("")

    destino = KB / "02_wiki" / "indices" / f"sala-{sala}.md"
    destino.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    guardar_memoria()
    print(f"{destino.relative_to(KB)}: {len(dentro)} dentro, {len(propuestas)} fichas propuestas, "
          f"{len(sin_ficha)} entradas sin ficha ({len(con_texto)} con texto)")


def sala_por_programa(sala: str, definicion: dict) -> None:
    """Organiza las obras por lecturas y clases del programa."""
    obras, clases = definicion["obras"], definicion["clases"]
    faltan = [f"{k}: {o['testigo']}" for k, o in obras.items() if not (KB / o["testigo"]).exists()]
    faltan += [f"{k}: ficha {o['ficha']}" for k, o in obras.items()
               if o.get("ficha") and not (FUENTES / f"{o['ficha']}.md").exists()]
    citadas = {k for c in clases for k, _ in c["lecturas"] + c.get("propuestas", [])}
    faltan += [f"{k}: no está en `obras`" for k in citadas - set(obras)]
    if faltan:
        sys.exit("definición rota:\n  " + "\n  ".join(faltan))
    cache = {}

    def linea(clave: str, leer: str) -> str:
        o = obras[clave]
        cabeza = f"[[{o['ficha']}|{o['titulo']}]]" if o.get("ficha") else f"{o['titulo']} (sin ficha)"
        return (f"- {cabeza} — leer: {leer} · `{o['testigo']}` · {describir_raws({'raws': [o['testigo']]}, cache)}"
                + (f" · {o['nota']}" if o.get("nota") else ""))

    leidas = {k for c in clases for k, _ in c["lecturas"]}
    prog = definicion["programa"]
    out = [
        f"# Sala de {definicion['titulo']}",
        "",
        f"> Generada por `python 04_ops/scripts/sala.py {sala}` el {date.today().isoformat()} "
        f"a partir de `04_ops/salas/{sala}.json`.",
        ">",
        f"> La pertenencia sale del {prog['nombre']} (`{prog['fuente']}`): {len(clases)} clases, "
        f"{len(leidas)} obras con texto en casa, {sum(1 for k in leidas if obras[k].get('ficha'))} de ellas con ficha. "
        + (f"Ratificada: {definicion['ratificada']}." if definicion.get("ratificada")
           else "**Sin ratificar todavía**: el casamiento de cada lectura con su texto está pendiente de revisión."),
        "",
        "Cada línea dice qué leer según el programa, con qué texto se lee y lo que hay que saber antes de citar: capa "
        "de texto sucia (citar contra el original), palabras partidas (buscar con `buscar_tolerante.py`, un cero "
        "literal no es un cero) y si se ha detectado una marca de hoja PDF. Una obra sin ficha está en la sala igual: "
        "la puso el programa, no la curación.",
        "",
    ]
    if definicion.get("materiales"):
        out += ["Materiales del curso que ya existen: " + " · ".join(f"`{m}`" for m in definicion["materiales"]), ""]
    out += ["## En la sala", ""]
    for c in clases:
        out += [f"### Clase {c['n']} · {c['fecha']} — {c['titulo']}", "", f"*{c['pregunta']}*", ""]
        out += [linea(k, leer) for k, leer in c["lecturas"]]
        out += [f"- {entrada} — **sin texto en casa**: {nota}" for entrada, nota in c.get("sin_texto", [])]
        out.append("")
    propuestas = [(c["n"], k, motivo) for c in clases for k, motivo in c.get("propuestas", [])]
    if propuestas:
        out += ["## Propuestas: obras que el programa no pone en las lecturas de la clase", "",
                "Entran en la sala cuando quien investiga las ratifica (se pasan a `lecturas` en la definición).", ""]
        out += [linea(k, f"clase {n}; {motivo}") for n, k, motivo in propuestas]
        out.append("")

    destino = KB / "02_wiki" / "indices" / f"sala-{sala}.md"
    destino.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    guardar_memoria()
    print(f"{destino.relative_to(KB)}: {len(clases)} clases, {len(leidas)} obras con texto, "
          f"{len(propuestas)} propuestas, {sum(len(c.get('sin_texto', [])) for c in clases)} sin texto")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sala", type=nombre_sala)
    sala = parser.parse_args().sala
    if CACHE.exists():
        MEMORIA.update(json.loads(CACHE.read_text(encoding="utf-8")))
    definicion = json.loads((KB / "04_ops" / "salas" / f"{sala}.json").read_text(encoding="utf-8"))
    if "clases" in definicion:
        sala_por_programa(sala, definicion)
        return
    if "miembros" in definicion:
        sala_por_listas(sala, definicion)
        return
    voc = re.compile(definicion["vocabulario"], re.I)
    ratificados, descartados = set(definicion["ratificados"]), set(definicion["descartados"])

    dentro, propuestas, citados = [], [], set()
    fuera = {}  # raw -> ficha que lo cita y no está ni dentro ni propuesta
    for f in sorted(FUENTES.glob("*.md")):
        texto = f.read_text(encoding="utf-8")
        fm = frontmatter(texto)
        citados.update(raws_de(fm))
        if f.stem in descartados:
            continue
        etiquetada = definicion["etiqueta"] in [d.strip() for d in fm.get("dominio", "").split("|")]
        veces = len(voc.findall(normalizar(texto)))
        fila = {"id": f.stem, "titulo": titulo(texto, f.stem), "estado": fm.get("estado", "?"),
                "forma": fm.get("forma", "?"), "raws": raws_de(fm), "veces": veces,
                "clave": f"{f.stem} {titulo(texto, f.stem)}"}
        if etiquetada or f.stem in ratificados:
            dentro.append(fila)
        elif veces >= definicion["umbral_ficha"]:
            propuestas.append(fila)
        else:
            for rel in fila["raws"]:
                fuera.setdefault(rel, fila)

    sueltos = []
    excluir = tuple(definicion.get("excluir_carpetas", []))
    por_titulo = re.compile(definicion["titulo_patron"], re.I) if definicion.get("titulo_patron") else None
    for rel, n in contar_raws(definicion["vocabulario"]).items():
        nombrado = bool(por_titulo and por_titulo.search(Path(rel).stem))
        ficha_fuera = fuera.get(rel)
        if rel in citados and not ficha_fuera:
            continue  # su ficha ya está en la sala o propuesta
        if ficha_fuera and any(ficha_fuera is p for p in propuestas):
            continue  # otro de sus textos ya la propuso
        if rel in descartados or rel.startswith(excluir) or (n < 10 and not nombrado):
            continue
        palabras = len((KB / rel).read_text(encoding="utf-8", errors="replace").split())
        por_mil = 1000.0 * n / max(palabras, 1)
        tramo = ""
        if not (por_mil >= definicion["umbral_raw_por_mil"] or nombrado or rel in ratificados):
            if not definicion.get("umbral_tramo_por_mil"):
                continue
            ventana = definicion.get("tramo_palabras", 5000)
            densidad, pagina = memorizada(f"tramo2|{voc.pattern}|{ventana}", rel,
                                          lambda: mejor_tramo(rel, voc, ventana))
            if densidad < definicion["umbral_tramo_por_mil"]:
                continue
            tramo = f"por un tramo ({coma(densidad)}‰" + (f", desde {pagina})" if pagina else ")")
        if ficha_fuera:
            ficha_fuera["motivo"] = f"por su texto `{Path(rel).stem}`" + (
                f", {tramo}" if tramo else f", {coma(por_mil)}‰")
            propuestas.append(ficha_fuera)
            continue
        sueltos.append({"id": rel, "por_mil": por_mil, "veces": n, "por_titulo": nombrado,
                        "tramo": tramo, **ficha_raw(rel)})

    cache = {}
    sub = definicion["subtemas"]
    out = [
        f"# Sala de {definicion['titulo']}",
        "",
        f"> Generada por `python 04_ops/scripts/sala.py {sala}` el {date.today().isoformat()} "
        f"a partir de `04_ops/salas/{sala}.json`.",
        ">",
        f"> Fichas en la sala: {len(dentro)} · Fichas propuestas: {len(propuestas)} · "
        f"{len(sueltos)} textos sin ficha propuestos. Una propuesta "
        "entra en la sala cuando quien investiga la ratifica. El subtema lo pone el guion, solo por el título. "
        f"Carpetas excluidas: {', '.join(f'`{c}`' for c in excluir) or 'ninguna'}.",
        "",
        "Cada línea dice lo que hay que saber antes de citar: capa de texto sucia (citar contra el "
        "original), palabras partidas (buscar con `buscar_tolerante.py`, un cero literal no es un cero) "
        "y si se ha detectado una marca de hoja PDF.",
        "",
        "## En la sala",
        "",
    ]

    por_subtema(out, dentro, lambda f: f["clave"],
                lambda f: f"- [[{f['id']}|{f['titulo']}]] — {f['forma']} · `{f['estado']}` · {describir_raws(f, cache)}",
                sub)
    out += ["## Propuestas: fichas sin la etiqueta", "",
            f"No llevan `{definicion['etiqueta']}` en `dominio:`. Entran porque la ficha nombra el vocabulario "
            f"de la sala {definicion['umbral_ficha']} veces o más, o porque su texto pasa el mismo examen que "
            "los textos sin ficha («por su texto»).", ""]
    por_subtema(out, propuestas, lambda f: f["clave"],
                lambda f: f"- [[{f['id']}|{f['titulo']}]] — "
                          + (f.get("motivo") or f"{f['veces']} veces") + f" · {f['forma']} · `{f['estado']}`", sub)
    out += ["## Propuestos: textos sin ficha", "",
            f"Ninguna ficha los nombra; el vocabulario pasa de {coma(definicion['umbral_raw_por_mil'])} por mil "
            "palabras" + (" o el nombre del fichero lo dice («por el título»)" if por_titulo else "")
            + (f", o un tramo de {miles(definicion.get('tramo_palabras', 5000))} palabras pasa de "
               f"{coma(definicion['umbral_tramo_por_mil'])} («por un tramo»: un capítulo dentro de un libro sobre otra cosa)"
               if definicion.get("umbral_tramo_por_mil") else "")
            + ". Ordenados por densidad dentro de cada subtema.", ""]
    grupos = {}
    for s in sueltos:
        grupos.setdefault(subtema(Path(s["id"]).stem, sub), []).append(s)
    for nombre in [s[0] for s in sub] + ["Otros"]:
        if nombre in grupos:
            out += [f"### {nombre} ({len(grupos[nombre])})", ""]
            for s in sorted(grupos[nombre], key=lambda s: -s["por_mil"]):
                entra = " · por el título" if s["por_titulo"] and s["por_mil"] < definicion["umbral_raw_por_mil"] else ""
                entra += f" · {s['tramo']}" if s["tramo"] else ""
                out.append(f"- `{s['id']}` — {coma(s['por_mil'])}‰ ({miles(s['palabras'])} palabras){entra} · {avisos(s)}")
            out.append("")

    destino = KB / "02_wiki" / "indices" / f"sala-{sala}.md"
    destino.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    guardar_memoria()
    print(f"{destino.relative_to(KB)}: {len(dentro)} dentro, {len(propuestas)} fichas propuestas, "
          f"{len(sueltos)} sin ficha")


if __name__ == "__main__":
    main()
