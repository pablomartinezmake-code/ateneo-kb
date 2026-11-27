"""Busca por sentido en los mapas de una sala: encuentra vecinos de tema, no cita.

Uso:
  python 04_ops/scripts/buscar_sentido.py <sala> "<pregunta>" [--limite 12] [--por-texto 2]
      [--reranker] [--reranker-model BAAI/bge-reranker-v2-m3] [--candidatos-reranker 20]
      [--solo <regex>] [--device auto|cpu|cuda] [--mapas <dir>] [--json]

Antes: `python 04_ops/scripts/indexar_sentido.py <sala>`.

Sube los pasajes que hablan de lo mismo que la pregunta, aunque no compartan palabras y aunque
estén en otra lengua. Es lo que se quiere para encontrar conexiones; no sirve para comprobar que
una cita existe ni para fijar un folio. Cada hallazgo da el raw, la línea y la hoja que precede al
pasaje («PDF p. N»): la dirección de vuelta a la imagen, donde se lee el folio impreso.

Solo cuenta un mapa si su raw está hoy en la sala, si lo hizo el troceador actual y si su huella
coincide con el markdown en disco. Un raw que cambió después de indexarse se salta y se avisa:
sus posiciones serían falsas. De cada hoja (o tramo, en los textos sin marcas) sale su mejor
pasaje, y `--por-texto` limita cuántos da un mismo texto, para que un libro entero sobre el
asunto no se coma la lista.

`--reranker` pide un segundo lector (bge-reranker-v2-m3, otros 2,2 GB) que relee los primeros
candidatos junto a la pregunta y los reordena. Se carga después de soltar BGE-M3: los dos juntos
no caben en la memoria de un portátil.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.insert(0, str(_AQUI))
import indexar_sentido as indexador  # noqa: E402
import sala as derivador  # noqa: E402
import sentido_trozos  # noqa: E402
from indexar_sentido import SentidoError  # noqa: E402

KB = indexador.KB
RERANKER = "BAAI/bge-reranker-v2-m3"
TRAMO = 3000  # caracteres: más o menos una página, para agrupar los textos sin marca de hoja
EXTRACTO = 500
NOTA = ("La búsqueda por sentido localiza pasajes candidatos: vecinos de tema, también en otras lenguas. "
        "No verifica citas ni folios. «PDF p. N» es la hoja del fichero, no el folio impreso: "
        "antes de citar, lee el pasaje y el folio en el PDF o el EPUB original.")


class RerankerBGE:
    def __init__(self, model: str, device: str):
        from sentence_transformers import CrossEncoder
        self.modelo = CrossEncoder(model, device=indexador.resolver_dispositivo(device), max_length=1024)

    def predict(self, pares: list[tuple[str, str]]):
        return self.modelo.predict(pares, batch_size=4, show_progress_bar=False)


def soltar_memoria() -> None:
    """Recoge lo que ya no tiene dueño; en GPU, devuelve además a la tarjeta la memoria que PyTorch guardaba."""
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()


def cargar_mapas(directorio: Path, sala: set[str], kb: Path = KB) -> tuple[list[dict], dict]:
    """Los mapas que valen hoy para la sala; los demás, apartados con su motivo."""
    import numpy as np

    mapas = []
    apartados = {"temporal": [], "ilegible": [], "fuera_de_la_sala": [], "troceador": [], "huella": []}
    for npz in sorted(directorio.glob("*.npz")):
        if ".tmp" in npz.name:
            apartados["temporal"].append(npz.name)
            continue
        try:
            with np.load(npz, allow_pickle=False) as z:
                a = {k: z[k] for k in ("vec", "pag", "ini", "fin", "etiquetas", "raw", "sha256", "troceador", "model")}
        except (OSError, KeyError, ValueError, TypeError, AttributeError, zipfile.BadZipFile):
            apartados["ilegible"].append(npz.name)
            continue
        raw = str(a["raw"])
        if raw not in sala:
            apartados["fuera_de_la_sala"].append(raw)
            continue
        if str(a["troceador"]) != sentido_trozos.CHUNKER_VERSION:
            apartados["troceador"].append(raw)
            continue
        crudo, texto = indexador.leer_raw(raw, kb)
        if hashlib.sha256(crudo).hexdigest() != str(a["sha256"]):
            apartados["huella"].append(raw)
            continue
        mapas.append({"raw": raw, "texto": texto, "model": str(a["model"]),
                      "vec": a["vec"].astype(np.float32), "pag": a["pag"], "ini": a["ini"], "fin": a["fin"],
                      "etiquetas": [str(e) for e in a["etiquetas"]]})
    return mapas, apartados


def mejores_por_hoja(mapa: dict, q, cuantos: int) -> list[tuple[float, dict, int]]:
    """El mejor trozo de cada hoja (o tramo de TRAMO caracteres sin hoja), hasta `cuantos` por texto."""
    import numpy as np

    s = mapa["vec"] @ q
    mejor = {}
    for k in np.argsort(-s, kind="stable"):
        etiqueta = mapa["etiquetas"][int(mapa["pag"][k])]
        clave = etiqueta or ("tramo", int(mapa["ini"][k]) // TRAMO)
        if clave not in mejor:
            mejor[clave] = int(k)
            if len(mejor) >= cuantos:
                break
    return [(float(s[k]), mapa, k) for k in mejor.values()]


def buscar(sala: str, pregunta: str, *, limite: int = 12, por_texto: int = 2, reranker: bool = False,
           reranker_model: str = RERANKER, candidatos_reranker: int = 20, solo: str | None = None,
           device: str = "auto", mapas: Path | None = None, kb: Path = KB,
           embedder_factory=None, reranker_factory=None, avisar=None) -> dict:
    """Busca y devuelve el resultado como diccionario (el mismo que imprime `--json`).

    `embedder_factory(model, device)` da un objeto con `encode(textos, batch_size)`; `reranker_factory(model,
    device)`, uno con `predict(pares)`. Por defecto, los de sentence-transformers.
    """
    import numpy as np

    if limite < 1 or por_texto < 1 or candidatos_reranker < 1:
        raise SentidoError("--limite, --por-texto y --candidatos-reranker tienen que ser 1 o más.")
    textos = {t["raw"]: t for t in indexador.textos_de_la_sala(sala, kb)}
    if solo:
        try:
            patron = re.compile(solo, re.I)
        except re.error as e:
            raise SentidoError(f"--solo no es una expresión regular válida: {e}")
        textos = {r: t for r, t in textos.items() if patron.search(r) or patron.search(t["titulo"])}
        if not textos:
            raise SentidoError(f"--solo {solo!r} no casa con ningún texto de la sala.")
    directorio = Path(mapas) if mapas else kb / "tmp" / "sala-sentido" / sala
    orden = f"python 04_ops/scripts/indexar_sentido.py {sala}"
    if not directorio.is_dir():
        raise SentidoError(f"La sala {sala} no está indexada todavía ({directorio}). Indexa primero: {orden}")
    vigentes, apartados = cargar_mapas(directorio, set(textos), kb)
    modelos = {}
    for m in vigentes:
        modelos[m["model"]] = modelos.get(m["model"], 0) + 1
    model = max(modelos, key=lambda k: (modelos[k], k)) if modelos else indexador.MODELO
    apartados["modelo"] = [m["raw"] for m in vigentes if m["model"] != model]
    vigentes = [m for m in vigentes if m["model"] == model]
    sin_mapa = sorted(set(textos) - {m["raw"] for m in vigentes} - set(apartados["huella"]))
    if avisar:
        if apartados["huella"]:
            avisar(f"Aviso: {len(apartados['huella'])} texto(s) cambiaron después de indexarse y se saltan "
                   f"(sus posiciones serían falsas): {', '.join(apartados['huella'])}. Vuelve a correr: {orden}")
        if sin_mapa and vigentes:
            avisar(f"Aviso: {len(sin_mapa)} texto(s) de la sala sin mapa vigente: {', '.join(sin_mapa)}. "
                   f"Indexa con: {orden}")
        for motivo in ("troceador", "modelo"):
            if apartados[motivo]:
                avisar(f"Aviso: mapas de otro {motivo} apartados: {', '.join(apartados[motivo])}")
    if not vigentes:
        raise SentidoError(f"No hay mapas vigentes de la sala {sala} en {directorio}. Indexa primero: {orden}")

    embedder = (embedder_factory or indexador.EmbedderBGE)(model, device)
    q = np.asarray(embedder.encode([pregunta], batch_size=1)[0], dtype=np.float32)
    q = q / (np.linalg.norm(q) or 1.0)
    del embedder
    soltar_memoria()  # BGE-M3 fuera de la memoria antes de que entre el segundo lector

    # Cada texto aporta candidatos para llenar la lista o la tanda del segundo lector; el tope
    # --por-texto se aplica al final, después de que el lector elija, para no tirar su mejor pasaje.
    cuantos = max(limite, candidatos_reranker if reranker else 0)
    candidatos = [c for m in vigentes for c in mejores_por_hoja(m, q, cuantos)]
    candidatos.sort(key=lambda c: -c[0])

    def trozo(c) -> str:
        _, m, k = c
        return m["texto"][int(m["ini"][k]):int(m["fin"][k])]

    notas = {}
    if reranker:
        tanda = candidatos[:candidatos_reranker]
        lector = (reranker_factory or RerankerBGE)(reranker_model, device)
        puntos = [float(p) for p in lector.predict([(pregunta, trozo(c)) for c in tanda])]
        del lector
        soltar_memoria()
        orden_lector = sorted(range(len(tanda)), key=lambda i: -puntos[i])
        notas = {id(tanda[i]): puntos[i] for i in orden_lector}
        candidatos = [tanda[i] for i in orden_lector] + candidatos[len(tanda):]

    hallazgos, por_libro = [], {}
    for c in candidatos:
        sim, m, k = c
        if por_libro.get(m["raw"], 0) >= por_texto:
            continue
        por_libro[m["raw"]] = por_libro.get(m["raw"], 0) + 1
        ini, fin = int(m["ini"][k]), int(m["fin"][k])
        t = textos[m["raw"]]
        h = {"rango": len(hallazgos) + 1, "raw": m["raw"], "titulo": t["titulo"], "ficha": t["ficha"] or None,
             "linea": m["texto"].count("\n", 0, ini) + 1, "pagina": m["etiquetas"][int(m["pag"][k])] or None,
             "ini": ini, "fin": fin, "similitud": round(sim, 4)}
        if id(c) in notas:
            h["nota_reranker"] = round(notas[id(c)], 4)
        h["fragmento"] = trozo(c)
        hallazgos.append(h)
        if len(hallazgos) >= limite:
            break
    return {"sala": sala, "pregunta": pregunta, "modelo": model, "reranker": reranker_model if reranker else None,
            "solo": solo, "textos_de_la_sala": len(textos), "textos_buscados": len(vigentes),
            "sin_mapa": sin_mapa, "apartados": apartados, "hallazgos": hallazgos, "nota": NOTA}


def numero(x: float) -> str:
    return f"{x:.3f}".replace(".", ",")


def como_texto(r: dict) -> str:
    out = [f"Sala {r['sala']} · «{r['pregunta']}»",
           f"{r['textos_buscados']} de {r['textos_de_la_sala']} textos buscados · modelo {r['modelo']}"
           + (f" · segundo lector {r['reranker']}" if r["reranker"] else ""), ""]
    if not r["hallazgos"]:
        out.append("Sin hallazgos.")
    for h in r["hallazgos"]:
        nota = f" · reranker {numero(h['nota_reranker'])}" if "nota_reranker" in h else ""
        extracto = re.sub(r"\s+", " ", h["fragmento"]).strip()
        if len(extracto) > EXTRACTO:
            extracto = extracto[:EXTRACTO].rsplit(" ", 1)[0] + " …"
        out += [f"{h['rango']:2d}. {h['titulo']}",
                f"    {h['raw']} · línea {h['linea']} · {h['pagina'] or 'sin marca de página'}"
                f" · similitud {numero(h['similitud'])}{nota}",
                f"    {extracto}", ""]
    out.append(r["nota"])
    return "\n".join(out)


def main(argv: list[str] | None = None, embedder_factory=None, reranker_factory=None, kb: Path = KB) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sala", type=derivador.nombre_sala)
    ap.add_argument("pregunta")
    ap.add_argument("--limite", type=int, default=12)
    ap.add_argument("--por-texto", type=int, default=2, help="pasajes como mucho de un mismo texto")
    ap.add_argument("--reranker", action="store_true", help="reordena con un segundo lector (otros 2,2 GB)")
    ap.add_argument("--reranker-model", default=RERANKER)
    ap.add_argument("--candidatos-reranker", type=int, default=20)
    ap.add_argument("--solo", help="solo los textos cuyo raw o título casen con esta expresión regular")
    ap.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    ap.add_argument("--mapas", type=Path, help="directorio de los mapas (por defecto tmp/sala-sentido/<sala>/)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    avisar = lambda s: print(s, file=sys.stderr)  # noqa: E731
    try:
        necesita = ("sentence_transformers",) if not embedder_factory or (args.reranker and not reranker_factory) else ()
        indexador.comprobar_dependencias("numpy", *necesita)
        r = buscar(args.sala, args.pregunta, limite=args.limite, por_texto=args.por_texto, reranker=args.reranker,
                   reranker_model=args.reranker_model, candidatos_reranker=args.candidatos_reranker, solo=args.solo,
                   device=args.device, mapas=args.mapas, kb=kb, embedder_factory=embedder_factory,
                   reranker_factory=reranker_factory, avisar=avisar)
    except SentidoError as e:
        print(e, file=sys.stderr)
        return 2
    print(json.dumps(r, ensure_ascii=False, indent=1) if args.json else como_texto(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
