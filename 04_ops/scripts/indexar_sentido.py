"""Indexa por sentido los textos de una sala con BGE-M3, en local: deja un mapa por raw para `buscar_sentido.py`.

Uso:
  python 04_ops/scripts/indexar_sentido.py <sala> [--model BAAI/bge-m3] [--device auto|cpu|cuda]
      [--batch-size 16] [--force] [--out <dir>]

Opcional: necesita `pip install -r requirements-semantic.txt`. La primera vez descarga el modelo
(unos 2,2 GB); después funciona sin conexión.

Los textos de la sala son los mismos que abre el lector de `consulta_sala.py`: el testigo que
declara la definición o, si no lo declara, el primer raw de la ficha con texto. Las salas por
materia o por listas necesitan antes su índice (`python 04_ops/scripts/sala.py <sala>`).

Cada mapa (`tmp/sala-sentido/<sala>/<raw>-<huella>.npz`) guarda los vectores de los trozos, sus
posiciones exactas en el markdown (`ini`, `fin`), la hoja que los precede (`pag` → `etiquetas`), la
huella SHA-256 del raw, la versión del troceador y el modelo. Un mapa cuya huella, troceador y modelo
siguen valiendo no se recalcula (salvo `--force`): volver a indexar una sala sin cambios no carga el
modelo. `indice.json` deja constancia de qué se indexó.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
import zipfile
from datetime import date
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.insert(0, str(_AQUI))
import consulta_sala as lector  # noqa: E402
import sala as derivador  # noqa: E402
import sentido_trozos  # noqa: E402

KB = Path(__file__).resolve().parents[2]
MODELO = "BAAI/bge-m3"
LARGO_MAXIMO = 512  # tokens por trozo para BGE-M3; los trozos de `sentido_trozos` caben de sobra
INSTALAR = "pip install -r requirements-semantic.txt"


class SentidoError(Exception):
    """Un fallo que se explica en una frase y sale con código 2."""


def comprobar_dependencias(*modulos: str) -> None:
    faltan = []
    for nombre in modulos:
        try:
            importlib.import_module(nombre)
        except ImportError:
            faltan.append(nombre)
    if faltan:
        raise SentidoError(f"La búsqueda por sentido es opcional y le falta {', '.join(faltan)}. "
                           f"Instálala con: {INSTALAR}")


def resolver_dispositivo(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


class EmbedderBGE:
    """BGE-M3 con sentence-transformers: vectores normalizados, de modo que el producto es el coseno."""

    def __init__(self, model: str, device: str):
        from sentence_transformers import SentenceTransformer
        self.modelo = SentenceTransformer(model, device=resolver_dispositivo(device))
        self.modelo.max_seq_length = LARGO_MAXIMO

    def encode(self, textos: list[str], batch_size: int = 16):
        return self.modelo.encode(textos, batch_size=batch_size, normalize_embeddings=True,
                                  convert_to_numpy=True, show_progress_bar=False)


def textos_de_la_sala(sala: str, kb: Path = KB) -> list[dict]:
    """Los raws de la sala, resueltos como los resuelve `consulta_sala.py` para su lector.

    Devuelve una entrada por raw: `raw` (ruta relativa a la biblioteca), `titulo`, `ficha` (vacía si
    no la hay) y `folios` (si la definición declara sus marcas `[**N**]`).
    """
    definicion = kb / "04_ops" / "salas" / f"{sala}.json"
    if not definicion.is_file():
        raise SentidoError(f"No existe la definición de la sala: 04_ops/salas/{sala}.json")
    defin = json.loads(definicion.read_text(encoding="utf-8"))
    folios = set(defin.get("folios_en_corchetes", []))
    salida, vistos = [], set()

    def poner(raw: str, titulo: str, ficha: str) -> None:
        ruta = (kb / raw).resolve()
        if not ruta.is_relative_to((kb / "01_raw").resolve()):
            raise SentidoError(f"Texto fuera de 01_raw en la sala: {raw}")
        if not ruta.is_file():
            raise SentidoError(f"Texto de la sala ausente del disco: {raw}")
        if raw not in vistos:
            vistos.add(raw)
            salida.append({"raw": raw, "titulo": titulo, "ficha": ficha, "folios": raw in folios})

    if "clases" in defin:  # sala por programa: las lecturas de cada clase
        obras = defin["obras"]
        for clase in defin["clases"]:
            for clave, _ in clase["lecturas"]:
                obra = obras[clave]
                poner(obra["testigo"], obra["titulo"], obra.get("ficha", ""))
        return salida

    indice = kb / "02_wiki" / "indices" / f"sala-{sala}.md"
    if not indice.is_file():
        raise SentidoError(f"Genera primero el índice: python 04_ops/scripts/sala.py {sala}")
    pagina = indice.read_text(encoding="utf-8")
    if "## En la sala" not in pagina:
        raise SentidoError(f"{indice.relative_to(kb).as_posix()} no tiene sección «## En la sala»")
    dentro = pagina.split("## En la sala", 1)[1].split("\n## ", 1)[0]
    testigos = defin.get("testigos", {})
    for linea in dentro.splitlines():
        m = lector.FICHA.match(linea)
        if not m:
            continue
        ficha, titulo = m.group(1), m.group(2)
        texto_ficha = (kb / "02_wiki" / "fuentes" / f"{ficha}.md").read_text(encoding="utf-8")
        raws = [r for r in derivador.raws_de(derivador.frontmatter(texto_ficha)) if (kb / r).exists()]
        if ficha in testigos:
            raw = testigos[ficha]
        else:  # el mismo criterio que el lector: el primer raw con texto, o el primero
            raw = next((r for r in raws if (kb / r).stat().st_size > 5000), raws[0] if raws else None)
        if raw:
            poner(raw, titulo, ficha)
    if defin.get("incluir_sin_ficha"):
        for item in defin.get("sin_ficha", []):
            if item.get("raw"):
                poner(item["raw"], item.get("entrada") or Path(item["raw"]).stem, "")
    return salida


def nombre_mapa(raw: str) -> str:
    """Un nombre de fichero estable por raw: su nombre y una huella corta de la ruta (dos libros pueden llamarse igual)."""
    return f"{Path(raw).stem}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:8]}.npz"


def leer_raw(raw: str, kb: Path = KB) -> tuple[bytes, str]:
    """Bytes y texto del raw. Se decodifica de los bytes, sin traducir saltos de línea: las posiciones son de este texto."""
    crudo = (kb / raw).read_bytes()
    return crudo, crudo.decode("utf-8", errors="replace")


def mapa_vigente(npz: Path, sha256: str, model: str) -> bool:
    import numpy as np
    try:
        with np.load(npz, allow_pickle=False) as z:
            return (str(z["sha256"]) == sha256 and str(z["troceador"]) == sentido_trozos.CHUNKER_VERSION
                    and str(z["model"]) == model)
    except (OSError, KeyError, ValueError, TypeError, AttributeError, zipfile.BadZipFile):
        return False


def guardar_mapa(npz: Path, raw: str, sha256: str, model: str, trozos: list[dict], vectores) -> None:
    """Escribe a un temporal y lo pone en su sitio al final: un corte a medias no deja un mapa válido."""
    import numpy as np
    etiquetas = sorted({t["pagina"] for t in trozos})
    temporal = npz.with_name(npz.stem + ".tmp.npz")
    with open(temporal, "wb") as f:
        np.savez(f, vec=np.asarray(vectores, dtype=np.float16),
                 pag=np.array([etiquetas.index(t["pagina"]) for t in trozos], dtype=np.int32),
                 ini=np.array([t["ini"] for t in trozos], dtype=np.int64),
                 fin=np.array([t["fin"] for t in trozos], dtype=np.int64),
                 etiquetas=np.array(etiquetas, dtype=str), raw=np.array(raw), sha256=np.array(sha256),
                 troceador=np.array(sentido_trozos.CHUNKER_VERSION), model=np.array(model))
    os.replace(temporal, npz)


def indexar(sala: str, model: str = MODELO, device: str = "auto", batch_size: int = 16, force: bool = False,
            out: Path | None = None, kb: Path = KB, embedder_factory=None, informar=print) -> dict:
    """Indexa la sala y devuelve lo que se escribió en `indice.json`.

    `embedder_factory(model, device)` devuelve un objeto con `encode(textos, batch_size)`; por defecto,
    BGE-M3 con sentence-transformers. Se llama solo si algún raw necesita vectores.
    """
    textos = textos_de_la_sala(sala, kb)
    if not textos:
        raise SentidoError(f"La sala {sala} no tiene textos que indexar.")
    destino = Path(out) if out else kb / "tmp" / "sala-sentido" / sala
    destino.mkdir(parents=True, exist_ok=True)
    fabrica = embedder_factory or EmbedderBGE
    embedder = None
    registro = []
    for t in textos:
        crudo, texto = leer_raw(t["raw"], kb)
        sha256 = hashlib.sha256(crudo).hexdigest()
        npz = destino / nombre_mapa(t["raw"])
        fila = {**t, "sha256": sha256, "mapa": npz.name}
        if not force and npz.exists() and mapa_vigente(npz, sha256, model):
            informar(f"sin cambios   {t['raw']}")
            registro.append({**fila, "estado": "sin cambios"})
            continue
        trozos = sentido_trozos.trocear(texto, folios=t["folios"])
        if not trozos:
            informar(f"sin texto     {t['raw']}")
            registro.append({**fila, "estado": "sin texto", "trozos": 0})
            continue
        if embedder is None:
            embedder = fabrica(model, device)
        vectores = embedder.encode([c["texto"] for c in trozos], batch_size=batch_size)
        if len(vectores) != len(trozos):
            raise SentidoError(f"El modelo devolvió {len(vectores)} vectores para {len(trozos)} trozos: {t['raw']}")
        guardar_mapa(npz, t["raw"], sha256, model, trozos, vectores)
        hojas = len({c["pagina"] for c in trozos if c["pagina"]})
        informar(f"indexado      {t['raw']} · {len(trozos)} trozos · {hojas} hojas marcadas")
        registro.append({**fila, "estado": "indexado", "trozos": len(trozos), "hojas": hojas})
    indice = {"sala": sala, "fecha": date.today().isoformat(), "model": model,
              "troceador": sentido_trozos.CHUNKER_VERSION, "textos": registro}
    (destino / "indice.json").write_text(json.dumps(indice, ensure_ascii=False, indent=1), encoding="utf-8")
    return indice


def main(argv: list[str] | None = None, embedder_factory=None, kb: Path = KB) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sala", type=derivador.nombre_sala)
    ap.add_argument("--model", default=MODELO)
    ap.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--force", action="store_true", help="recalcula también los mapas vigentes")
    ap.add_argument("--out", type=Path, help="directorio de los mapas (por defecto tmp/sala-sentido/<sala>/)")
    args = ap.parse_args(argv)
    try:
        comprobar_dependencias("numpy", *(() if embedder_factory else ("sentence_transformers",)))
        indice = indexar(args.sala, args.model, args.device, args.batch_size, args.force, args.out,
                         kb=kb, embedder_factory=embedder_factory)
    except SentidoError as e:
        print(e, file=sys.stderr)
        return 2
    cuenta = {}
    for t in indice["textos"]:
        cuenta[t["estado"]] = cuenta.get(t["estado"], 0) + 1
    print(f"\n{len(indice['textos'])} textos · " + " · ".join(f"{n} {e}" for e, n in cuenta.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
