"""Exercise both room commands in a standalone copy of the public example."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest


ROOT = Path(__file__).resolve().parents[1]
KANT = "kant-1784-was-ist-aufklaerung"


@pytest.fixture
def library(tmp_path):
    raw = f"{KANT}-wikisource"
    files = [
        f"01_raw/books/{raw}.md", f"01_raw/books/{raw}.meta.json", f"01_raw/papers/{raw}.epub",
        f"02_wiki/fuentes/{KANT}.md", "04_ops/salas/ilustracion.json",
        "04_ops/scripts/sala.py", "04_ops/scripts/consulta_sala.py",
        "04_ops/scripts/consulta_sala.html", "04_ops/scripts/consulta_sala.js",
        "palabras_partidas.py", "fidelidad_capa.py",
    ]
    for name in files:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (tmp_path / "02_wiki/indices").mkdir()
    return tmp_path


def build(library, name, no_rg=False):
    env = dict(os.environ, PYTHONUTF8="1")
    if no_rg:
        env["PATH"] = ""
    for script in ("sala", "consulta_sala"):
        subprocess.run([sys.executable, library / "04_ops/scripts" / f"{script}.py", name],
                       cwd=library.parent, env=env, check=True)
    out = library / "tmp" / f"consulta-sala-{name}"
    html = (out / "index.html").read_text(encoding="utf-8")
    for src in re.findall(r'<script src="([^"]+)">', html):
        assert (out / src).is_file()
    assert "@font-face" not in html
    data = (out / "datos.js").read_text(encoding="utf-8")
    assert data.startswith("window.SALA = ") and data.endswith(";\n")
    return out, json.loads(data[len("window.SALA = "):-2])


def test_kant(library):
    out, data = build(library, "ilustracion")
    book, = data["libros"]
    assert book["id"] == KANT
    assert book["clase"] == "epub-con-folios"
    assert "Atlas de citas" in book["ficha_cuerpo"]
    assert any(u["e"] == "p. 481" and "Sapere aude!" in u["t"] for u in book["u"])
    for field, ext in (("raw_uri", ".md"), ("original", ".epub")):
        path = (out / unquote(book[field])).resolve()
        assert path.is_relative_to(library / "01_raw") and path.is_file()
        assert path.suffix == ext and not Path(book[field]).is_absolute()


def test_search_and_quote_in_generated_reader(library):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is optional and only used to exercise the reader JavaScript")
    out, _ = build(library, "ilustracion")
    runner = out / "check.cjs"
    runner.write_text("""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
global.window = global;
for (const file of ["datos.js", "consulta_sala.js"])
  vm.runInThisContext(fs.readFileSync(file, "utf8"));
const html = fs.readFileSync("index.html", "utf8");
const elements = new Map();
const element = () => ({closest() {return this}, focus() {}, addEventListener() {},
  querySelector() {return null}, classList: {toggle() {}}});
global.document = {getElementById(id) {
  if (!elements.has(id)) elements.set(id, element());
  return elements.get(id);
}};
vm.runInThisContext(html.match(/<script>([\\s\\S]*?)<\\/script>/)[1]);
assert.match(document.getElementById("estante").innerHTML, /Kant/);
const hit = buscar("aufklarung").porLibro[0];
assert.ok(hit.hits.some(h => L[hit.li].u[h.ui].e === "p. 481"));
const quotes = citasFicha(L[0].ficha_cuerpo);
assert.deepEqual(quotes.map(q => q.pagina), [481, 481, 482]);
for (const q of quotes) {
  const dest = destinoCita(L[0], q);
  assert.equal(L[0].u[dest.ui].e, "p. " + q.pagina);
  abrir(0, dest.ui, {cita: q, literal: dest.literal});
  assert.match(document.getElementById("lector").innerHTML, /\\.epub/);
}
assert.equal(destinoCita(L[0], quotes[0]).literal, true);
assert.equal(norm("ﬁlo\\u00ad\\nsofía"), "filosofia");
""", encoding="utf-8")
    subprocess.run([node, runner], cwd=out, check=True)


@pytest.mark.parametrize("no_rg", [False, True])
def test_normalized_vocabulary(library, no_rg):
    defs = library / "04_ops/salas"
    definition = json.loads((defs / "ilustracion.json").read_text(encoding="utf-8"))
    definition.update(titulo="Ideas", etiqueta="ideas", vocabulario="filosofía",
                      umbral_ficha=1, folios_en_corchetes=[])
    (defs / "ideas.json").write_text(json.dumps(definition), encoding="utf-8")
    raw = "01_raw/books/ideas.md"
    text = "ﬁlo\u00ad\nsofía"
    (library / raw).write_text("<!-- pdf_page: 007 -->\n" + text, encoding="utf-8")
    (library / "01_raw/books/otro.md").write_text((" " + text) * 12, encoding="utf-8")
    cards = library / "02_wiki/fuentes"
    (cards / "ideas.md").write_text(
        f"---\ndominio: ideas\nforma: libro\nfuentes:\n  - {raw}\n---\n# Ideas\n", encoding="utf-8")
    (cards / "propuesta.md").write_text(f"# Lectura\n{text}", encoding="utf-8")
    _, data = build(library, "ideas", no_rg)
    book, = data["libros"]
    assert book["u"] == [{"e": "PDF p. 7", "t": "filosofía"}]
    index = (library / "02_wiki/indices/sala-ideas.md").read_text(encoding="utf-8")
    assert "[[propuesta|" in index.split("## Propuestas:")[1]
    assert "01_raw/books/otro.md" in index.split("## Propuestos:")[1]


@pytest.mark.parametrize("course", [False, True])
def test_curated_room(library, course):
    raw = next((library / "01_raw/books").glob("*.md")).relative_to(library).as_posix()
    if course:
        definition = {"programa": {"nombre": "Curso", "fuente": "programa.md"},
                      "clases": [{"n": 1, "fecha": "", "titulo": "Leer", "pregunta": "¿Por qué?",
                                  "lecturas": [["obra", "Inicio"]]}],
                      "obras": {"obra": {"ficha": KANT, "titulo": "Kant", "testigo": raw}}}
    else:
        definition = {"listas": {"a": {"etiqueta": "Curso", "nombre": "Curso", "fuente": "lista.md"}},
                      "miembros": {KANT: ["a"]}, "subtemas": []}
    definition["titulo"] = "Seminario"
    (library / "04_ops/salas/seminario.json").write_text(json.dumps(definition), encoding="utf-8")
    _, data = build(library, "seminario")
    book, = data["libros"]
    assert book["raw"] == raw and book["original"].endswith(".epub")
    if course:
        assert data["estante"][0]["items"] == [{"li": 0, "leer": "Inicio"}]
    else:
        assert book["lista"] == "Curso"

