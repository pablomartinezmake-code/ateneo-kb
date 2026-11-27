"""Construye un lector local en tmp/consulta-sala-<nombre>/."""
import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import quote

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.insert(0, str(_AQUI))
import sala as derivador

KB = Path(__file__).resolve().parents[2]
PAGINA = re.compile(r"<!-- (?:pdf_)?page: (\d+)(?: \| printed_page: [^>]*?)? -->"
                    r"|^## Página PDF (\d+)(?: · (izquierda|derecha))?[ \t]*$", re.M)
PDF_PAGE = re.compile(r"^## PDF page (\d+)(?: / printed \d+)?[ \t]*$", re.M)
ENCABEZADO = re.compile(r"^(#{1,6})[ \t]+(.*)$", re.M)
FICHA = re.compile(r"^- \[\[([^|\]]+)\|(.+?)\]\](.*)$")  # admite corchetes en el título
TRAMO = 2400  # caracteres por tramo cuando una sección de EPUB no trae páginas
PARTE = 3000  # en un EPUB sin encabezados, un párrafo de este largo abre parte (un capítulo entero en una línea)


def limpio(s: str) -> str:
    """Texto para leer: sin comentarios, imágenes ni el ruido de enlaces del EPUB."""
    s = derivador.normalizar(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"\[\[(\d+)\]\]\(#[^)]*\)", r"⁽\1⁾", s)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"</?span[^>]*>", "", s)
    s = re.sub(r"\[([^\]]{1,6})\]\(#[^)]*\)", r"⁽\1⁾", s)      # llamada de nota: «nozze[4](#fn4)»
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)               # enlace: su texto
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)           # restos de maqueta del PDF
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def unidad_del_ancla(partes: list[dict], ancla: str) -> int | None:
    """Localiza el comienzo de un tramo tras normalizar su ancla."""
    a = re.sub(r"\s+", " ", limpio(ancla))
    return next((i for i, u in enumerate(partes) if a in re.sub(r"\s+", " ", u["t"])), None)


def etiqueta(s: str) -> str:
    return re.sub(r"\s+", " ", limpio(s).replace("*", "")).strip()


def unidades(texto: str, folios: bool = False) -> tuple[str, list[dict]]:
    """Devuelve el formato y las unidades de lectura en el orden de la fuente."""
    if folios:
        marcas = list(re.finditer(r"\[\*\*(\d+)\*\*\]", texto))
        if not marcas:
            raise ValueError("Faltan las marcas de folio [**N**] declaradas en la sala")
        partes = []
        inicio = limpio(texto[:marcas[0].start()])
        if inicio:
            partes.append({"e": "inicio", "t": inicio})
        for i, m in enumerate(marcas):
            fin = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
            partes.append({"e": f"p. {m[1]}", "t": limpio(texto[m.end():fin])})
        return "epub-con-folios", partes
    marcas = list(PAGINA.finditer(texto))
    if not marcas:
        hojas = list(PDF_PAGE.finditer(texto))
        if hojas and 2 * len(hojas) >= len(ENCABEZADO.findall(texto)):
            marcas = hojas
    if marcas:
        salida = []
        for i, m in enumerate(marcas):
            fin = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
            t = limpio(texto[m.end():fin])
            if t:
                lado = f" {m.group(3)}" if m.re is PAGINA and m.group(3) else ""
                salida.append({"e": f"PDF p. {int(m.group(1) or m.group(2))}{lado}", "t": t})
        return "pdf", salida
    cabezas = list(ENCABEZADO.finditer(texto))
    trozos = []
    if not cabezas:
        for p in texto.split("\n\n"):
            if len(p) >= PARTE or not trozos:
                trozos.append((incipit(p), p))
            else:
                trozos[-1] = (trozos[-1][0], trozos[-1][1] + "\n\n" + p)
        return "epub-sin-encabezados", _tramos(trozos)
    if cabezas[0].start() > 0:
        trozos.append(("inicio", texto[:cabezas[0].start()]))
    for i, m in enumerate(cabezas):
        fin = cabezas[i + 1].start() if i + 1 < len(cabezas) else len(texto)
        trozos.append((etiqueta(m.group(2)) or "sin título", texto[m.end():fin]))
    return "epub", _tramos(trozos)


def unidades_de(raw: str, defin: dict) -> tuple[str, list[dict]]:
    ruta = (KB / raw).resolve()
    if not ruta.is_relative_to(KB / "01_raw"):
        raise ValueError(f"Testigo fuera de 01_raw: {raw}")
    return unidades(ruta.read_text(encoding="utf-8", errors="replace"),
                    raw in defin.get("folios_en_corchetes", []))


def incipit(p: str) -> str:
    """Obtiene un rótulo de las primeras palabras."""
    p = limpio(p)[:200]
    m = re.match(r"(.{2,120}?)\s(?:¿\s)?[A-ZÁÉÍÓÚÑ]\s[a-záéíóúñ]", p) or re.match(r"(.{2,120}?)\s\[\d+\]", p)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else " ".join(p.split()[:3]) + "…"


def _tramos(trozos: list[tuple[str, str]]) -> list[dict]:
    salida = []
    for nombre, cuerpo in trozos:
        t = limpio(cuerpo)
        if not t:
            continue
        if len(t) <= TRAMO * 1.5:
            salida.append({"e": nombre, "t": t})
            continue
        parrafos, actual, k = [], "", 1
        for p in t.split("\n\n"):  # un capítulo en un solo párrafo se corta por frases
            parrafos += re.split(r"(?<=[.!?»])\s+(?=[«¿¡A-ZÁÉÍÓÚÑ])", p) if len(p) > TRAMO else [p]
        for p in parrafos:
            if actual and len(actual) + len(p) > TRAMO:
                salida.append({"e": f"{nombre} · tramo {k}", "t": actual.strip()})
                actual, k = "", k + 1
            actual += p + ("\n\n" if p.endswith("\n") or len(p) > 400 else " ")
        if actual.strip():
            salida.append({"e": f"{nombre} · tramo {k}" if k > 1 else nombre, "t": actual.strip()})
    return salida


CATEGORIAS_COTEJO = [
    "Citas cotejadas a ciegas por otro lector",
    "Alguna cita cotejada en la imagen por quien escribió la ficha",
    "Se miró en la imagen otra cosa, no las citas",
    "Citas sin cotejar en la imagen",
    "La ficha no lo dice",
]


def cuerpo_ficha(texto: str) -> str:
    texto = re.sub(r"\A\ufeff?---\n.*?\n---\s*\n", "", texto, count=1, flags=re.S)
    return re.sub(r"^# [^\n]*\n?", "", texto, count=1, flags=re.M).strip()


def _clausulas(frase: str) -> list[str]:
    """No cortar una prueba por el punto o punto y coma que lleva dentro la cita."""
    salida, inicio, comillas = [], 0, 0
    for i, c in enumerate(frase):
        comillas += (c == "«") - (c == "»")
        if comillas <= 0 and (c == ";" or c == "." and re.match(r"\s+[A-ZÁÉÍÓÚÑ]", frase[i + 1:])):
            salida.append(frase[inicio:i + 1].strip())
            inicio = i + 1
    salida.append(frase[inicio:].strip())
    return salida


def marca_cotejo(texto: str) -> dict:
    """Declaración de la ficha, no certificación nuestra. Reglas conservadoras, por cláusula.

    El párrafo completo conserva límites («las demás…») y la sección da contexto a
    «Solo lo cotejado». Un cotejo genérico bajo Atlas no declara por sí solo su objeto.
    """
    bloques, seccion_actual = [], ""
    for b in re.split(r"\n\s*\n", cuerpo_ficha(texto)):
        h = re.match(r"^## (.+)", b)
        if h:
            seccion_actual = h.group(1)
            b = re.sub(r"^## [^\n]*\n?", "", b)
        if b.strip():
            for linea in re.split(r"\n(?=- )", b):
                bloques.append((seccion_actual, re.sub(r"\s+", " ", linea).strip()))
        if h and re.search(r"sin cotejar|pendiente", seccion_actual, re.I):
            bloques.append((seccion_actual, "## " + seccion_actual))
    positivas, otras, pendientes = [], [], []
    for sec, frase in bloques:
        ciego = re.search(r"copió a ciegas|copiadas? a ciegas", frase, re.I)
        if ciego and not re.search(r"\b(?:no|nunca|sin)\b.{0,25}$", frase[:ciego.start()], re.I):
            return {"categoria": CATEGORIAS_COTEJO[0], "frase": frase}
        for clausula in _clausulas(frase):
            s = re.sub(r"[*`]", "", clausula).lower()
            negativo = re.search(r"sin cotej|no (?:se )?(?:cotej|comprob|verific|fijan)|no debe|no puede|no pasar|salvo que|ninguna .*cita|pendiente|propuesta|a verificar|exige|hace falta|requiere|antes de|tras cotejo|\bhasta\b|cuando se", s)
            accion = re.search(r"cotejad\w*|cotejaron|cotejó|cotejo (?:del|visual)|le[ií]d\w* en la imagen|leyó", s)
            imagen = re.search(r"imagen|visual|original|escaneo", s)
            sujeto = re.search(r"\bcitas?\b|microcita|pasajes usados en la ficha", s)
            if accion and imagen and sujeto and not negativo and not s.lstrip().startswith(">"):
                limite = re.search(r"Las demás citas[^.]*\.", frase)
                inicio = frase.find(clausula)
                positivas.append(frase[inicio:limite.end()] if limite and limite.end() > inicio else clausula)
            elif re.search(r"atlas de citas|^citas", sec, re.I) and re.match(r"\*?solo lo cotejado en la imagen", s) and not negativo:
                positivas.append(frase)
            if accion and imagen and not negativo and re.search(r"página legal|pagina legal|créditos|creditos|portada|folios?|texto base|editorial procedures", s):
                otras.append(clausula)
        declaracion_negativa = re.search(r"\bcitas?\b.*\b(?:no (?:se )?cotej|se cotejarán)", frase, re.I)
        if declaracion_negativa or re.search(r"sin cotejar en (?:la )?imagen|pendientes? de cotejo|cita a verificar tras cotejo|citas sin (?:comprobación|cotejo) visual|no se fijan citas literales hasta cotejo", frase, re.I):
            pendientes.append(frase)
    for categoria, candidatas in ((1, positivas), (2, otras), (3, pendientes)):
        if candidatas:
            return {"categoria": CATEGORIAS_COTEJO[categoria], "frase": candidatas[0]}
    return {"categoria": CATEGORIAS_COTEJO[4], "frase": ""}


def actos_registrados() -> dict:
    """Actos del registro de cotejos (04_ops/cotejos/citas.jsonl) por ficha; el visor los pone bajo cada cita."""
    ruta = KB / "04_ops" / "cotejos" / "citas.jsonl"
    por_ficha: dict = {}
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            if linea.strip():
                a = json.loads(linea)
                por_ficha.setdefault(a["ficha"], []).append({k: a[k] for k in (
                    "cita", "acto", "lector", "fecha", "resultado", "folio_visto", "capa")})
    return por_ficha


ACTOS = actos_registrados()


def datos_ficha(texto: str, slug: str) -> dict:
    return {"ficha_id": slug, "ficha_cuerpo": cuerpo_ficha(texto), "cotejo": marca_cotejo(texto),
            "actos": ACTOS.get(slug, [])}


def original_de(raw: str) -> tuple[str, str]:
    meta = (KB / raw).with_suffix(".meta.json")
    if meta.is_file():
        valor = json.loads(meta.read_text(encoding="utf-8")).get("original_copy")
        if valor:
            ruta = (KB / valor).resolve()
            if ruta.is_relative_to(KB) and ruta.is_file():
                return ruta.relative_to(KB).as_posix(), ruta.name
    return "", ""


def por_programa(defin: dict) -> tuple[list[dict], list[dict]]:
    """Construye una estantería por clase, con un texto por obra."""
    if derivador.CACHE.exists():
        derivador.MEMORIA.update(json.loads(derivador.CACHE.read_text(encoding="utf-8")))
    obras, libros, indice, estante = defin["obras"], [], {}, []
    for c in defin["clases"]:
        items = []
        for clave, leer in c["lecturas"]:
            if clave not in indice:
                o = obras[clave]
                ruta_ficha = KB / "02_wiki" / "fuentes" / f"{o['ficha']}.md" if o.get("ficha") else None
                texto_ficha = ruta_ficha.read_text(encoding="utf-8") if ruta_ficha else ""
                otros = [r for r in derivador.raws_de(derivador.frontmatter(texto_ficha))
                         if r != o["testigo"] and (KB / r).exists()]
                clase, partes = unidades_de(o["testigo"], defin)
                original, nombre_original = original_de(o["testigo"])
                indice[clave] = len(libros)
                libros.append({
                    "id": clave, "titulo": o["titulo"], "subtema": "", "forma": "libro",
                    "capa": derivador.avisos(derivador.ficha_raw(o["testigo"])) + (f" · {o['nota']}" if o.get("nota") else ""),
                    "ficha": o.get("ficha", ""), "raw": o["testigo"],
                    "raw_uri": o["testigo"], "original": original, "nombre_original": nombre_original,
                    "por": "elegido en la definición de la sala" + ("" if ruta_ficha else "; sin ficha"),
                    "otros": [f"{r} ({(KB / r).stat().st_size // 1024:,} KB)".replace(",", ".") for r in otros],
                    "clase": clase, "u": partes, "leer": [],
                    **datos_ficha(texto_ficha, o.get("ficha", "")),
                })
                print(f"{len(partes):5d} unidades ({clase})  {clave}")
            libro = libros[indice[clave]]
            libro["leer"].append({"clase": c["n"], "leer": leer})
            items.append({"li": indice[clave], "leer": leer})
        estante.append({"grupo": f"Clase {c['n']} · {c['fecha']}", "titulo": c["titulo"], "pregunta": c["pregunta"],
                        "items": items, "sin_texto": [e for e, _ in c.get("sin_texto", [])]})
    for l in libros:
        ns = [str(x["clase"]) for x in l["leer"]]
        l["subtema"] = ("Clase " if len(ns) == 1 else "Clases ") + (" y ".join(ns) if len(ns) < 3 else ", ".join(ns[:-1]) + " y " + ns[-1])
    return libros, estante


def libros_sin_ficha(defin: dict, libros: list[dict]) -> list[dict]:
    """Carga raws ratificados en `sin_ficha` para salas por listas, sin crear una ficha."""
    if not defin.get("incluir_sin_ficha", False):
        return []
    existentes = {str((KB / l["raw"]).resolve()) for l in libros if l.get("raw")}
    salida = []
    for item in defin.get("sin_ficha", []):
        raw = item.get("raw")
        if not raw:
            continue
        lista = item.get("lista")
        if lista not in defin.get("listas", {}):
            raise ValueError(f"sin_ficha {item.get('entrada', '<sin entrada>')!r}: lista inválida {lista!r}")
        ruta = (KB / raw).resolve()
        try:
            ruta.relative_to(KB.resolve())
        except ValueError as exc:
            raise ValueError(f"sin_ficha {item.get('entrada', '<sin entrada>')!r}: raw fuera de Ateneo-KB: {raw}") from exc
        if not ruta.is_file():
            raise ValueError(f"sin_ficha {item.get('entrada', '<sin entrada>')!r}: raw inexistente: {raw}")
        raw_key = str(ruta)
        if raw_key in existentes:
            raise ValueError(f"sin_ficha {item.get('entrada', '<sin entrada>')!r}: raw duplicado en la sala: {raw}")
        existentes.add(raw_key)
        clase, partes = unidades_de(raw, defin)
        original, nombre_original = original_de(raw)
        ficha_raw = derivador.ficha_raw(raw)
        capa = derivador.avisos(ficha_raw)
        if item.get("nota"):
            capa += (" · " if capa else "") + item["nota"]
        ident = item.get("id") or Path(raw).stem
        entrada = item.get("entrada") or ident
        fila = {
            "id": ident, "titulo": entrada,
            "subtema": derivador.subtema(entrada, defin.get("subtemas", [])),
            "forma": item.get("forma") or "libro", "capa": capa,
            "ficha": "", "raw": raw, "raw_uri": ruta.relative_to(KB).as_posix(),
            "original": original, "nombre_original": nombre_original,
            "por": "entrada sin ficha de la definición de la sala",
            "otros": [], "clase": clase, "u": partes,
            "lista": defin["listas"][lista]["etiqueta"],
            **datos_ficha("", ""),
        }
        salida.append(fila)
    return salida


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sala", type=derivador.nombre_sala)
    sala = parser.parse_args().sala
    indice = KB / "02_wiki" / "indices" / f"sala-{sala}.md"
    if not indice.is_file():
        sys.exit(f"Genera primero el índice: python 04_ops/scripts/sala.py {sala}")
    pagina = indice.read_text(encoding="utf-8")
    titulo_sala = re.search(r"^# (.+)$", pagina, re.M).group(1)
    dentro = pagina.split("## En la sala", 1)[1].split("\n## ", 1)[0]
    definicion = KB / "04_ops" / "salas" / f"{sala}.json"
    defin = json.loads(definicion.read_text(encoding="utf-8")) if definicion.exists() else {}
    testigos, miembros, listas = defin.get("testigos", {}), defin.get("miembros", {}), defin.get("listas", {})
    libros, subtema, estante = [], "Otros", None
    if "clases" in defin:
        libros, estante = por_programa(defin)
        dentro = ""
    for linea in dentro.splitlines():
        if linea.startswith("### "):
            subtema = re.sub(r"\s*\(\d+\)$", "", linea[4:]).strip()
            continue
        m = FICHA.match(linea)
        if not m:
            continue
        ficha, titulo, cola = m.group(1), m.group(2), m.group(3)
        ruta_ficha = KB / "02_wiki" / "fuentes" / f"{ficha}.md"
        texto_ficha = ruta_ficha.read_text(encoding="utf-8")
        fm = derivador.frontmatter(texto_ficha)
        raws = [r for r in derivador.raws_de(fm) if (KB / r).exists()]
        if ficha in testigos:
            if not (KB / testigos[ficha]).is_file():
                sys.exit(f"Testigo declarado ausente: {testigos[ficha]}")
            raw, por = testigos[ficha], "elegido en la definición de la sala"
        else:
            raw = next((r for r in raws if (KB / r).stat().st_size > 5000), raws[0] if raws else None)
            por = "primer raw de la ficha" if raw == (raws[0] if raws else None) else "primer raw de la ficha con texto"
        if not raw:
            print(f"sin raw legible, fuera del lector: {ficha}")
            continue
        if raws and raw != raws[0]:
            print(f"      ({por}: {raw}; antes en la ficha, {raws[0]})")
        otros = [f"{r} ({(KB / r).stat().st_size // 1024:,} KB)".replace(",", ".") for r in raws if r != raw]
        clase, partes = unidades_de(raw, defin)
        original, nombre_original = original_de(raw)
        partes_cola = [p.strip() for p in cola.split("·")]
        libros.append({
            "id": ficha, "titulo": titulo, "subtema": subtema, "forma": partes_cola[0].lstrip("— ").strip(),
            "capa": (partes_cola[-1] if len(partes_cola) > 2 else "").replace("`", ""),
            "ficha": ficha, "raw": raw, "raw_uri": raw,
            "original": original, "nombre_original": nombre_original, "por": por, "otros": otros,
            "clase": clase, "u": partes,
            **datos_ficha(texto_ficha, ficha),
        })
        if ficha in miembros:  # sala por listas: de qué lista viene
            libros[-1]["lista"] = " y ".join(listas[l]["etiqueta"] for l in miembros[ficha])
        tramos = []
        for t in defin.get("tramos", {}).get(ficha, []):
            ui = unidad_del_ancla(partes, t["ancla"])
            if ui is None:
                sys.exit(f"tramo «{t['titulo']}» de {ficha}: el ancla no cae en ninguna unidad del texto")
            tramos.append({"titulo": t["titulo"], "motivo": t["motivo"], "ancla": t["ancla"], "ui": ui})
        if tramos:
            libros[-1]["tramos"] = tramos
        print(f"{len(partes):5d} unidades ({clase})  {ficha}" + (f"  · {len(tramos)} tramos" if tramos else ""))
    libros.extend(libros_sin_ficha(defin, libros))
    for libro in libros:
        if not libro["u"]:
            sys.exit(f"Texto sin unidades legibles: {libro['raw']}")
    salida = KB / "tmp" / f"consulta-sala-{sala}"
    salida.mkdir(parents=True, exist_ok=True)
    for libro in libros:
        for campo in ("original", "raw_uri"):
            if libro[campo]:
                destino = (KB / libro[campo]).resolve()
                if not destino.is_relative_to(KB):
                    raise ValueError("El texto debe estar dentro de la biblioteca")
                libro[campo] = quote(Path(os.path.relpath(destino, salida)).as_posix())
    datos = {"sala": titulo_sala, "libros": libros}
    if estante:
        datos["estante"] = estante
    (salida / "datos.js").write_text("window.SALA = " + json.dumps(datos, ensure_ascii=False) + ";\n", encoding="utf-8")
    shutil.copyfile(_AQUI / "consulta_sala.html", salida / "index.html")
    shutil.copyfile(_AQUI / "consulta_sala.js", salida / "consulta_sala.js")
    total = sum(len(l["u"]) for l in libros)
    print(f"\nFuentes: {len(libros)} · Unidades: {total} → {(salida / 'index.html').as_uri()}")


if __name__ == "__main__":
    main()
