# Ateneo-KB

[English](README.en.md)

*Ningún catálogo ha leído un libro.*

https://github.com/user-attachments/assets/6023bd31-1caf-43ec-819a-844a193d8e6e

Un agente de IA ya casi no se inventa libros. Ahora inventa algo más difícil de ver: la frase entre comillas, atribuida a un libro que existe, en un capítulo que existe, que dice justo lo que el investigador quería leer. El catálogo confirma el autor, la obra y el ISBN. Solo abrir la página desmiente la frase.

Ateneo-KB es el sistema con el que trabajo una biblioteca de investigación junto a varios agentes (Codex, Claude, Gemini) sin perder el camino de vuelta a la página. No trae libros ni modelos dentro: trae las reglas, las herramientas y los candados.

El caso que lo originó, contado en un ensayo: [¿El fin de las alucinaciones?](https://pablomartinezsamper.com/pensamiento/el-fin-de-las-alucinaciones.html)

## Qué hace (y de qué tropiezo salió cada pieza)

- **Guarda la fuente tal como llegó.** Cada libro, artículo o página entra en `01_raw/` con su original, su conversión a Markdown y un `.meta.json` que registra de dónde salió el texto. Nadie edita esa carpeta después.
- **Monta salas de lectura con tus propios libros.** Reúne una mesa temática en local (por materia, listas o programa de curso): la máquina puede proponer libros por vocabulario, tú ratificas quién entra, y el lector local permite buscar en toda la sala a la vez, leer la página junto a su ficha y abrir el original en esa hoja.
- **Mide cuánto fiarse de la capa de texto.** Un testigo sucio no miente: calla. Un escaneo roto convierte `también` en `tambi6n`, borra tildes o parte `sugestión` en `su gestión`, y una búsqueda literal devuelve cero donde la frase sí estaba. La ingesta mide la capa y avisa antes de que nadie copie una cita.
- **Busca sin engañarse.** `buscar_tolerante.py` encuentra las palabras que partió el reconocimiento óptico, los guiones blandos y las ligaduras, y separa los aciertos seguros de los que debe mirar una persona. Un cero de búsqueda no es un cero.
- **Para al agente en la puerta.** En la biblioteca de la que sale este repositorio, once ficheros de `01_raw/` resultaron ser resúmenes escritos por un agente —título, autores, *Abstract*, *Key Findings*— sin la fuente detrás, y sostenían diez fichas dadas por firmes. Un candado de `pre-commit` aborta el commit si intenta entrar un resumen disfrazado de fuente, una ficha sin procedencia o una nota fuera del índice.
- **Le dice al agente cómo trabajar.** `AGENTS.md` es el reglamento: qué puede tocar, qué no, y cómo separar siempre *evidencia firme*, *lectura abierta* e *hipótesis abierta*.

## Empezar

Hace falta Python 3.11 o posterior. Para los PDF escaneados sin capa de texto hace falta además [Tesseract](https://github.com/tesseract-ocr/tesseract) con los idiomas de tus libros; sin él, todo lo demás funciona.

```bash
git clone https://github.com/pablomartinezmake-code/ateneo-kb.git mi-biblioteca
cd mi-biblioteca
pip install -r requirements.txt
git config core.hooksPath hooks
```

Prueba el buscador ahora mismo sobre el texto de ejemplo que ya viene dentro (Kant, 1784):

```bash
python buscar_tolerante.py Verstandes 01_raw/books/*.md
```

Ingerir una fuente nueva:

```bash
python ingest_to_raw.py "ruta/al/libro.epub" --out 01_raw/books --write-meta
python ingest_to_raw.py "ruta/al/articulo.pdf" --out 01_raw/papers --write-meta
```

Después, pídele a tu agente que lea `AGENTS.md` y redacte la ficha de esa fuente.

## El ejemplo

El repositorio trae una fuente ya ingerida y fichada: «¿Qué es la Ilustración?», de Kant (1784), en la transcripción de Wikisource, con los folios del impreso. Empieza por la ficha, [`02_wiki/fuentes/kant-1784-was-ist-aufklaerung.md`](02_wiki/fuentes/kant-1784-was-ist-aufklaerung.md), y sigue el rastro hasta la página 482, donde Kant escribe lo cómodo que es tener un libro que piense por uno.

La ficha enseña además a leer una falsa alarma: la ingesta marca el texto como «SUCIO» (`3,595/mil`), pero al mirar el sidecar `.meta.json` se ve que las diez marcas son enlaces `cite_note` y `cite_ref` de Wikisource. **El número avisa; no decide.**

Los ficheros del ejemplo (`01_raw/books/kant-1784-*`, `01_raw/papers/kant-1784-*`) vienen de Wikisource y se distribuyen con CC BY-SA 3.0; el resto del repositorio, con licencia MIT. Bórralos cuando empieces tu biblioteca.

## Salas de lectura

Copia `04_ops/salas/ilustracion.json` en `04_ops/salas/<nombre>.json`. La `etiqueta` selecciona las fichas por su `dominio`. El `vocabulario` es una expresión regular que propone otras lecturas. Para admitir una ficha propuesta, añade su nombre de fichero sin extensión a `ratificados`. Conserva en `folios_en_corchetes` solo las rutas que usan marcas `[**N**]` para indicar folios.

```sh
python 04_ops/scripts/sala.py ilustracion
python 04_ops/scripts/consulta_sala.py ilustracion
```

Sustituye `ilustracion` por tu nombre de sala. El primer comando genera `02_wiki/indices/sala-<nombre>.md`. Enlaza ese índice desde `02_wiki/index.md`. El segundo construye `tmp/consulta-sala-<nombre>/index.html`. Ábrelo en tu navegador para buscar en los textos, leer el contexto de una cita y consultar la ficha junto al original enlazado. Funciona sin servidor, claves ni conexión. Git ignora `tmp/`, que contiene las copias de lectura.

### Búsqueda por sentido (opcional)

El lector busca palabras. Para buscar *de qué habla* un pasaje —la misma idea dicha con otras palabras, o en otra lengua— la sala se puede indexar en local con el modelo BGE-M3:

```sh
pip install -r requirements-semantic.txt
python 04_ops/scripts/indexar_sentido.py ilustracion
python 04_ops/scripts/buscar_sentido.py ilustracion "pensar por cuenta propia sin tutela"
```

El índice queda en `tmp/sala-sentido/<nombre>/`. Volver a indexar solo recalcula los textos que cambiaron. Cada hallazgo da el texto, la línea, la hoja (`PDF p. N`, `p. N` o `sin marca de página`) y el pasaje. Un texto que cambió después de indexarse se salta con un aviso, porque sus posiciones serían falsas. `--por-texto 2` limita cuántos pasajes da un mismo libro. `--solo <expresión>` restringe la búsqueda a unos textos. `--json` sirve para pasarle el resultado a un agente. `--reranker` añade un segundo modelo, bge-reranker-v2-m3, que relee los 20 primeros candidatos y los reordena. Se carga cuando el primero ya ha salido de la memoria.

Qué pide: la primera vez descarga BGE-M3 (unos 2,2 GB) y otros 2,2 GB si usas `--reranker`. Después funciona sin conexión. Hacen falta entre 8 y 16 GB de RAM. En CPU es manejable para una sala pequeña, de 5 a 20 textos; para 50 libros o más conviene una GPU con CUDA (`--device cuda`).

Qué no hace: encuentra vecinos de tema, también entre lenguas, pero no verifica que una cita exista ni fija un folio. `PDF p. N` es la hoja del fichero. El pasaje y el folio impreso se leen en el PDF o el EPUB original antes de citar.

## Lo que no es

- **No es una biblioteca.** Los libros con derechos se quedan en tu disco; este repositorio no los trae ni los reparte.
- **No es un producto de IA.** No llama a ninguna API. El agente entra como invitado, con la suscripción o herramienta que ya uses.
- **No decide por ti.** El agente propone; una persona revisa y firma.

## Pruebas

```bash
python -m pytest tests
python 04_ops/scripts/wiki_graph.py --strict
python 04_ops/scripts/censo_resumenes_en_raw.py --self-test
python 04_ops/scripts/censo_fuentes.py --self-test
```

## De dónde sale

Es el esqueleto abierto de la biblioteca con la que escribo *Toda inteligencia es artificial* (Xoroi Edicions, de próxima aparición). Parte del patrón en abierto *LLM Wiki* (`raw → wiki → outputs`) popularizado por Andrej Karpathy y le añade lo que ese esquema dejaba sin cerrar para las humanidades: la verificación contra la página y los candados editoriales. Las herramientas se escribieron con Claude Code y Codex; cada regla salió de un fallo real que hubo que atrapar, y cada guion cuenta en su cabecera el caso que lo produjo.

Pablo Martínez Samper · [pablomartinezsamper.com](https://pablomartinezsamper.com) · Licencia MIT
