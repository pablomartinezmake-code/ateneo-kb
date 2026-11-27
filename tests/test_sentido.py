"""Búsqueda por sentido, sin red y sin modelos: un vectorizador falso por palabras ocupa el lugar de BGE-M3.

Lo que se prueba es la fontanería que hace volver a la página: rangos exactos, hojas, sala, huella,
troceador, topes y el orden en que entran y salen los modelos. La calidad de BGE-M3 no se mide aquí.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import weakref
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "04_ops" / "scripts"))
import buscar_sentido as bs  # noqa: E402
import indexar_sentido as ix  # noqa: E402
import sentido_trozos as st  # noqa: E402

necesita_numpy = pytest.mark.skipif(importlib.util.find_spec("numpy") is None,
                                    reason="numpy es opcional (requirements-semantic.txt)")
KANT_RAW = "01_raw/books/kant-1784-was-ist-aufklaerung-wikisource.md"
DIM = 512


def vector(texto: str):
    import numpy as np
    v = np.zeros(DIM, dtype=np.float32)
    for palabra in re.findall(r"\w+", texto.lower()):
        v[zlib.crc32(palabra.encode("utf-8")) % DIM] += 1.0
    return v / (np.linalg.norm(v) or 1.0)


class Registro(list):
    """Lo que hicieron los modelos falsos, en orden."""

    def embedder(self, model, device):
        return EmbedderFalso(self, model)

    def reranker(self, model, device):
        return RerankerFalso(self, model)


class EmbedderFalso:
    def __init__(self, registro, model):
        registro.append(("carga embedder", model))
        weakref.finalize(self, registro.append, ("suelta embedder", model))

    def encode(self, textos, batch_size=16):
        import numpy as np
        return np.stack([vector(t) for t in textos])


class RerankerFalso:
    """Premia el pasaje que nombra «Vormünder»: el orden de BGE no lo pondría el primero."""

    def __init__(self, registro, model):
        registro.append(("carga reranker", model))
        self.registro = registro

    def predict(self, pares):
        self.registro.append(("reordena", len(pares)))
        return [1.0 if "vormünder" in p.lower() else 0.0 for _, p in pares]


@pytest.fixture
def biblioteca(tmp_path):
    """Una copia de la biblioteca del ejemplo, para no escribir nada en el repositorio."""
    kb = tmp_path / "kb"
    nombres = [KANT_RAW, KANT_RAW.replace(".md", ".meta.json"),
               "02_wiki/fuentes/kant-1784-was-ist-aufklaerung.md", "04_ops/salas/ilustracion.json",
               "palabras_partidas.py", "fidelidad_capa.py"]
    nombres += [f"04_ops/scripts/{n}" for n in ("sala.py", "consulta_sala.py", "consulta_sala.html",
                                                 "consulta_sala.js", "sentido_trozos.py",
                                                 "indexar_sentido.py", "buscar_sentido.py")]
    for nombre in nombres:
        destino = kb / nombre
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / nombre, destino)
    (kb / "02_wiki/indices").mkdir()
    return kb


def correr(kb: Path, guion: str, *args: str) -> None:
    env = {**__import__("os").environ, "PYTHONUTF8": "1"}
    subprocess.run([sys.executable, kb / "04_ops/scripts" / guion, *args], cwd=kb, env=env, check=True,
                   capture_output=True)


def raws_del_lector(kb: Path, sala: str) -> set[str]:
    """Los raws que abre el lector de `consulta_sala.py`: la referencia con la que hay que coincidir."""
    correr(kb, "sala.py", sala)
    correr(kb, "consulta_sala.py", sala)
    datos = (kb / "tmp" / f"consulta-sala-{sala}" / "datos.js").read_text(encoding="utf-8")
    return {l["raw"] for l in json.loads(datos[len("window.SALA = "):-2])["libros"]}


def hojas(marca: str, numeros, tema: str, extra: str = "") -> str:
    partes = []
    for n in numeros:
        partes.append(f"<!-- {marca}: {n:03d} -->")
        partes.append(f"Hoja {n}. {tema} {extra} " * 6)
        partes.append(f"Otro párrafo de la hoja {n} sobre cosas distintas, jardines y relojes. " * 4)
    return "\n\n".join(partes) + "\n"


def ficha(kb: Path, nombre: str, titulo: str, raws: list[str]) -> None:
    fuentes = "".join(f"  - {r}\n" for r in raws)
    (kb / "02_wiki/fuentes" / f"{nombre}.md").write_text(
        f"---\ntipo: fuente\nforma: libro\ndominio: biblio\nfuentes:\n{fuentes}---\n# {titulo}\n",
        encoding="utf-8")


@pytest.fixture
def varios_libros(biblioteca):
    """Sala «biblio» con tres libros: hojas PDF, hojas de EPUB, y uno sin marcas cuya ficha trae primero un resumen corto."""
    kb = biblioteca
    defin = json.loads((kb / "04_ops/salas/ilustracion.json").read_text(encoding="utf-8"))
    defin.update(titulo="Biblio", etiqueta="biblio", vocabulario="qqqzzz", subtemas=[], folios_en_corchetes=[])
    (kb / "04_ops/salas/biblio.json").write_text(json.dumps(defin), encoding="utf-8")
    textos = {
        "01_raw/books/libro-a.md": "# Libro A\n\n" + hojas("pdf_page", range(1, 9), "La mayoría de edad y el uso público de la razón.")
                                   + "\n\n".join(["La hoja ocho sigue: el uso público de la razón tiene que ser libre. " * 8] * 4),
        "01_raw/papers/libro-b.md": hojas("page", range(1, 5), "Die Vormünder und die Unmündigkeit des Verstandes."),
        "01_raw/books/c-resumen.md": "Resumen breve de C.\n",
        "01_raw/books/c-completo.md": ("Un texto sin marcas de hoja sobre la razón pública y el uso de la razón. " * 20
                                       + "\n\n") * 6,
        "01_raw/books/huerfano.md": "El uso público de la razón, en un texto que no está en la sala.\n",
    }
    for rel, texto in textos.items():
        (kb / rel).parent.mkdir(parents=True, exist_ok=True)
        (kb / rel).write_text(texto, encoding="utf-8", newline="\n")
    ficha(kb, "libro-a", "Libro A", ["01_raw/books/libro-a.md"])
    ficha(kb, "libro-b", "Libro B", ["01_raw/papers/libro-b.md"])
    ficha(kb, "libro-c", "Libro C", ["01_raw/books/c-resumen.md", "01_raw/books/c-completo.md"])
    return kb


# --- El troceador -------------------------------------------------------------------------------

@pytest.mark.parametrize("salto", ["\n", "\r\n"])
def test_trozos_rango_exacto_y_hojas(salto):
    largo = " ".join(f"Frase número {i} del párrafo largo, que sigue y sigue." for i in range(80))
    texto = salto.join([
        "# Preámbulo sin hoja", "", "Antes de la primera marca.", "",
        "<!-- pdf_page: 007 -->", "", "Texto de la hoja siete.", "", largo, "",
        "<!-- page: 3 | printed_page: iii -->", "Texto de la página tres.", "",
    ])
    trozos = st.trocear(texto)
    assert trozos and all(texto[t["ini"]:t["fin"]] == t["texto"] for t in trozos)
    assert all(t["linea"] == texto.count("\n", 0, t["ini"]) + 1 for t in trozos)
    assert all(len(t["texto"]) <= st.MAXIMO and "<!--" not in t["texto"] for t in trozos)
    assert [t["pagina"] for t in trozos][0] == "" and trozos[0]["texto"].endswith("primera marca.")
    assert {t["pagina"] for t in trozos} == {"", "PDF p. 7", "p. 3"}
    siete = [t for t in trozos if t["pagina"] == "PDF p. 7"]
    assert len(siete) >= 3 and siete[0]["texto"].startswith("Texto de la hoja siete.")
    assert any(b["ini"] < a["fin"] for a, b in zip(siete, siete[1:])), "los trozos de una hoja se solapan"
    assert trozos[-1] == {**trozos[-1], "pagina": "p. 3", "texto": "Texto de la página tres."}


def test_trozos_sin_texto():
    assert st.trocear("") == []
    assert st.trocear("Una frase.\n\n---\n")[0]["texto"] == "Una frase.\n\n---"  # la regla viaja con su párrafo
    assert st.trocear("---\n\n<!-- pdf_page: 1 -->\n\n***\n") == []


def test_trozos_folios_en_corchetes():
    texto = (ROOT / KANT_RAW).read_bytes().decode("utf-8")
    trozos = st.trocear(texto, folios=True)
    assert all(texto[t["ini"]:t["fin"]] == t["texto"] for t in trozos)
    assert next(t for t in trozos if "Sapere aude!" in t["texto"])["pagina"] == "p. 481"
    assert next(t for t in trozos if "Es ist so bequem" in t["texto"])["pagina"] == "p. 482"
    assert {t["pagina"] for t in st.trocear(texto)} == {""}  # sin declararlo, [**N**] no es una marca


# --- La sala ------------------------------------------------------------------------------------

def test_sala_como_la_resuelve_el_lector(varios_libros):
    kb = varios_libros
    esperados = raws_del_lector(kb, "biblio")
    textos = ix.textos_de_la_sala("biblio", kb)
    assert {t["raw"] for t in textos} == esperados
    assert "01_raw/books/c-completo.md" in esperados  # el primer raw con texto, no el resumen
    assert "01_raw/books/huerfano.md" not in esperados


@necesita_numpy
def test_sala_sin_indice(biblioteca, capsys):
    assert ix.main(["ilustracion"], embedder_factory=Registro().embedder, kb=biblioteca) == 2
    assert "sala.py ilustracion" in capsys.readouterr().err


# --- Indexar y buscar ---------------------------------------------------------------------------

@necesita_numpy
def test_sala_de_ejemplo(biblioteca, tmp_path, capsys):
    import numpy as np
    kb, mapas, registro = biblioteca, tmp_path / "mapas", Registro()
    raws_del_lector(kb, "ilustracion")
    assert ix.main(["ilustracion", "--out", str(mapas)], embedder_factory=registro.embedder, kb=kb) == 0
    npz, = mapas.glob("*.npz")
    with np.load(npz) as z:
        assert set(z.files) == {"vec", "pag", "ini", "fin", "etiquetas", "raw", "sha256", "troceador", "model"}
        assert str(z["raw"]) == KANT_RAW and str(z["model"]) == "BAAI/bge-m3"
        assert str(z["troceador"]) == st.CHUNKER_VERSION == "exact-source-ranges-public-v1"
    indice = json.loads((mapas / "indice.json").read_text(encoding="utf-8"))
    assert [t["estado"] for t in indice["textos"]] == ["indexado"]
    capsys.readouterr()
    assert bs.main(["ilustracion", "Sapere aude Muth Verstandes", "--mapas", str(mapas)],
                   embedder_factory=registro.embedder, kb=kb) == 0
    salida = capsys.readouterr().out
    assert "kant-1784-was-ist-aufklaerung-wikisource.md · línea" in salida and "p. 481" in salida
    assert salida.rstrip().endswith(bs.NOTA)


@necesita_numpy
def test_json_y_varios_libros(varios_libros, tmp_path, capsys):
    kb, mapas, registro = varios_libros, tmp_path / "mapas", Registro()
    raws_del_lector(kb, "biblio")
    ix.indexar("biblio", out=mapas, kb=kb, embedder_factory=registro.embedder, informar=lambda s: None)
    assert bs.main(["biblio", "uso público de la razón", "--json", "--limite", "6", "--mapas", str(mapas)],
                   embedder_factory=registro.embedder, kb=kb) == 0
    r = json.loads(capsys.readouterr().out)
    assert set(r) == {"sala", "pregunta", "modelo", "reranker", "solo", "textos_de_la_sala", "textos_buscados",
                      "sin_mapa", "apartados", "hallazgos", "nota"}
    assert r["textos_de_la_sala"] == r["textos_buscados"] == 3 and r["reranker"] is None
    texto = {rel: (kb / rel).read_bytes().decode("utf-8") for rel in {h["raw"] for h in r["hallazgos"]}}
    for h in r["hallazgos"]:
        assert set(h) == {"rango", "raw", "titulo", "ficha", "linea", "pagina", "ini", "fin", "similitud", "fragmento"}
        assert texto[h["raw"]][h["ini"]:h["fin"]] == h["fragmento"]
        assert h["linea"] == texto[h["raw"]].count("\n", 0, h["ini"]) + 1
    paginas = {h["raw"]: h["pagina"] for h in r["hallazgos"]}
    assert paginas["01_raw/books/c-completo.md"] is None
    assert re.fullmatch(r"PDF p\. \d", paginas["01_raw/books/libro-a.md"])
    assert [h["rango"] for h in r["hallazgos"]] == list(range(1, len(r["hallazgos"]) + 1))


@necesita_numpy
def test_tope_por_texto_y_una_hoja_un_pasaje(varios_libros, tmp_path):
    kb, mapas, registro = varios_libros, tmp_path / "mapas", Registro()
    raws_del_lector(kb, "biblio")
    ix.indexar("biblio", out=mapas, kb=kb, embedder_factory=registro.embedder, informar=lambda s: None)
    pregunta = "mayoría de edad uso público razón"
    por_libro = lambda r: [h["raw"] for h in r["hallazgos"]].count("01_raw/books/libro-a.md")  # noqa: E731
    r = bs.buscar("biblio", pregunta, mapas=mapas, kb=kb, embedder_factory=registro.embedder)
    assert por_libro(r) == 2  # --por-texto por defecto
    r = bs.buscar("biblio", pregunta, por_texto=20, limite=50, mapas=mapas, kb=kb, embedder_factory=registro.embedder)
    a = [h["pagina"] for h in r["hallazgos"] if h["raw"] == "01_raw/books/libro-a.md" and h["pagina"]]
    assert len(a) == 8 and len(set(a)) == 8  # ocho hojas, un pasaje de cada una (la ocho tiene varios trozos)
    c = [h["ini"] // bs.TRAMO for h in r["hallazgos"] if h["raw"] == "01_raw/books/c-completo.md"]
    assert len(c) == len(set(c)) >= 2  # sin marcas: un pasaje por tramo
    r = bs.buscar("biblio", pregunta, solo=r"papers/", mapas=mapas, kb=kb, embedder_factory=registro.embedder)
    assert {h["raw"] for h in r["hallazgos"]} == {"01_raw/papers/libro-b.md"} and r["textos_de_la_sala"] == 1
    r = bs.buscar("biblio", pregunta, solo="libro c", mapas=mapas, kb=kb, embedder_factory=registro.embedder)
    assert {h["raw"] for h in r["hallazgos"]} == {"01_raw/books/c-completo.md"}  # también por el título
    with pytest.raises(ix.SentidoError, match="no casa"):
        bs.buscar("biblio", pregunta, solo="nadie", mapas=mapas, kb=kb, embedder_factory=registro.embedder)


@necesita_numpy
def test_incremental_y_huella(varios_libros, tmp_path):
    kb, mapas, registro = varios_libros, tmp_path / "mapas", Registro()
    raws_del_lector(kb, "biblio")
    callar = lambda s: None  # noqa: E731
    ix.indexar("biblio", out=mapas, kb=kb, embedder_factory=registro.embedder, informar=callar)
    assert registro.count(("carga embedder", "BAAI/bge-m3")) == 1
    indice = ix.indexar("biblio", out=mapas, kb=kb, embedder_factory=registro.embedder, informar=callar)
    assert {t["estado"] for t in indice["textos"]} == {"sin cambios"}
    assert registro.count(("carga embedder", "BAAI/bge-m3")) == 1, "una sala sin cambios no carga el modelo"

    cambiado = kb / "01_raw/papers/libro-b.md"
    cambiado.write_text("Línea nueva arriba.\n" + cambiado.read_text(encoding="utf-8"), encoding="utf-8")
    avisos = []
    r = bs.buscar("biblio", "Vormünder Unmündigkeit", mapas=mapas, kb=kb, embedder_factory=registro.embedder,
                  avisar=avisos.append)
    assert r["apartados"]["huella"] == ["01_raw/papers/libro-b.md"]
    assert "01_raw/papers/libro-b.md" not in {h["raw"] for h in r["hallazgos"]}
    assert any("cambiaron después de indexarse" in a and "libro-b.md" in a for a in avisos)

    indice = ix.indexar("biblio", out=mapas, kb=kb, embedder_factory=registro.embedder, informar=callar)
    estados = {t["raw"]: t["estado"] for t in indice["textos"]}
    assert estados.pop("01_raw/papers/libro-b.md") == "indexado" and set(estados.values()) == {"sin cambios"}
    indice = ix.indexar("biblio", out=mapas, kb=kb, embedder_factory=registro.embedder, informar=callar,
                        force=True)
    assert {t["estado"] for t in indice["textos"]} == {"indexado"}
    indice = ix.indexar("biblio", out=mapas, kb=kb, embedder_factory=registro.embedder, informar=callar,
                        model="otro/modelo")
    assert {t["estado"] for t in indice["textos"]} == {"indexado"}, "otro modelo invalida el mapa"


@necesita_numpy
def test_filtro_de_poblacion(varios_libros, tmp_path):
    import numpy as np
    kb, mapas, registro = varios_libros, tmp_path / "mapas", Registro()
    raws_del_lector(kb, "biblio")
    ix.indexar("biblio", out=mapas, kb=kb, embedder_factory=registro.embedder, informar=lambda s: None)
    huerfano = "01_raw/books/huerfano.md"
    crudo, texto = ix.leer_raw(huerfano, kb)
    trozos = st.trocear(texto)
    sha = __import__("hashlib").sha256(crudo).hexdigest()
    vectores = registro.embedder("m", "cpu").encode([t["texto"] for t in trozos])
    ix.guardar_mapa(mapas / ix.nombre_mapa(huerfano), huerfano, sha, "BAAI/bge-m3", trozos, vectores)
    un_mapa = next(mapas.glob("libro-a-*.npz"))
    shutil.copyfile(un_mapa, mapas / "libro-a-copia.tmp.npz")
    with np.load(un_mapa) as z:
        viejo = {k: z[k] for k in z.files}
    viejo["troceador"] = np.array("otro-troceador")
    viejo["raw"] = np.array("01_raw/books/c-completo.md")
    with open(mapas / "viejo.npz", "wb") as f:
        np.savez(f, **viejo)
    (mapas / "roto.npz").write_bytes(b"no es un npz")

    r = bs.buscar("biblio", "uso público de la razón", limite=50, por_texto=50, mapas=mapas, kb=kb,
                  embedder_factory=registro.embedder)
    assert r["apartados"]["fuera_de_la_sala"] == [huerfano]
    assert r["apartados"]["temporal"] == ["libro-a-copia.tmp.npz"]
    assert r["apartados"]["troceador"] == ["01_raw/books/c-completo.md"]
    assert r["apartados"]["ilegible"] == ["roto.npz"]
    assert huerfano not in {h["raw"] for h in r["hallazgos"]} and r["textos_buscados"] == 3


@necesita_numpy
def test_reranker_suelta_bge_antes_de_cargar(varios_libros, tmp_path, capsys):
    kb, mapas, registro = varios_libros, tmp_path / "mapas", Registro()
    raws_del_lector(kb, "biblio")
    ix.indexar("biblio", out=mapas, kb=kb, embedder_factory=registro.embedder, informar=lambda s: None)
    sin_lector = bs.buscar("biblio", "uso público de la razón", mapas=mapas, kb=kb, embedder_factory=registro.embedder)
    assert sin_lector["hallazgos"][0]["raw"] != "01_raw/papers/libro-b.md"
    registro.clear()
    assert bs.main(["biblio", "uso público de la razón", "--reranker", "--candidatos-reranker", "100", "--json",
                    "--mapas", str(mapas)], embedder_factory=registro.embedder,
                   reranker_factory=registro.reranker, kb=kb) == 0
    r = json.loads(capsys.readouterr().out)
    assert [e[0] for e in registro] == ["carga embedder", "suelta embedder", "carga reranker", "reordena"]
    assert 0 < registro[3][1] <= 100 and r["reranker"] == "BAAI/bge-reranker-v2-m3"
    assert r["hallazgos"][0]["raw"] == "01_raw/papers/libro-b.md"  # el segundo lector lo subió
    assert r["hallazgos"][0]["nota_reranker"] == 1.0


# --- Fallos que se explican ---------------------------------------------------------------------

def test_sin_sentence_transformers(biblioteca, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # import → ImportError
    assert ix.main(["ilustracion", "--out", str(biblioteca / "mapas")], kb=biblioteca) == 2
    assert "pip install -r requirements-semantic.txt" in capsys.readouterr().err
    assert not (biblioteca / "mapas").exists()
    assert bs.main(["ilustracion", "razón"], kb=biblioteca) == 2
    assert "requirements-semantic.txt" in capsys.readouterr().err


@necesita_numpy
def test_sala_sin_indexar(biblioteca, tmp_path, capsys):
    raws_del_lector(biblioteca, "ilustracion")
    registro = Registro()
    assert bs.main(["ilustracion", "razón", "--mapas", str(tmp_path / "nada")],
                   embedder_factory=registro.embedder, kb=biblioteca) == 2
    assert "indexar_sentido.py ilustracion" in capsys.readouterr().err
    (tmp_path / "vacio").mkdir()
    assert bs.main(["ilustracion", "razón", "--mapas", str(tmp_path / "vacio")],
                   embedder_factory=registro.embedder, kb=biblioteca) == 2
    assert "No hay mapas vigentes" in capsys.readouterr().err
    assert registro == [], "sin mapas no se carga ningún modelo"
