# Ateneo-KB

[Español](README.md)

*No catalogue has ever read a book.*

AI agents hardly ever invent books any more. They now invent something harder to see: the sentence in quotation marks, attributed to a book that exists, in a chapter that exists, saying exactly what the researcher hoped to read. The catalogue confirms the author, the title and the ISBN. Only opening the page can refute the sentence.

Ateneo-KB is the system I use to work a research library together with several agents (Codex, Claude, Gemini) without losing the way back to the page. It ships no books and no models: it ships the rules, the tools and the locks.

The case behind it, told as an essay: [The End of Hallucinations?](https://pablomartinezsamper.com/pensamiento/the-end-of-hallucinations.html)

## What it does (and the failure behind each lock)

- **Keeps the source as it arrived.** Every book, paper or web page enters `01_raw/` with its original file, its Markdown conversion and a `.meta.json` recording where the text comes from. Nobody edits that folder afterwards.
- **Builds local reading rooms from your own books.** Assemble a thematic desk by topic, lists or syllabus: the machine can suggest books by vocabulary, you ratify who enters, and the offline browser reader searches the whole room at once, shows the page beside its research note and opens the original file on that sheet.
- **Measures how far to trust the text layer.** A corrupted witness does not lie: it stays silent. A broken scan turns `también` into `tambi6n`, strips diacritics or splits a word in two, so a literal search returns zero where the sentence was actually sitting. Ingestion measures the layer and warns before anyone copies a quotation.
- **Searches without fooling itself.** `buscar_tolerante.py` catches words broken by OCR, soft hyphens and typographic ligatures, and separates safe hits from those a person must inspect. A zero from a search is not a zero.
- **Stops the agent at the door.** In the private library this repository grew out of, eleven files inside `01_raw/` turned out to be summaries written by an agent — title, authors, *Abstract*, *Key Findings* — with no primary source behind them, backing ten notes marked as firm. A `pre-commit` hook aborts the commit if a summary dressed up as a source, an unattributed note or an unindexed file tries to slip in.
- **Tells the agent how to work.** `AGENTS.md` is the rulebook: what it may touch, what it may not, and how to keep *firm evidence*, *open reading* and *working hypothesis* apart.

The scripts and their comments are in Spanish; the commands work the same in any language.

## Getting started

You need Python 3.11 or later. Scanned PDFs without a text layer also need [Tesseract](https://github.com/tesseract-ocr/tesseract) with the languages of your books; everything else works without it.

```bash
git clone https://github.com/pablomartinezmake-code/ateneo-kb.git my-library
cd my-library
pip install -r requirements.txt
git config core.hooksPath hooks
```

Try the search right away on the Kant example already inside the repo:

```bash
python buscar_tolerante.py Verstandes 01_raw/books/*.md
```

Ingest a new source:

```bash
python ingest_to_raw.py "path/to/book.epub" --out 01_raw/books --write-meta
python ingest_to_raw.py "path/to/paper.pdf" --out 01_raw/papers --write-meta
```

Then ask your agent to read `AGENTS.md` and draft the source note.

## The example

The repository ships one source already ingested and catalogued: Kant's *What is Enlightenment?* (1784), in the German Wikisource transcription, with the printed page numbers of the journal. Start from the note, [`02_wiki/fuentes/kant-1784-was-ist-aufklaerung.md`](02_wiki/fuentes/kant-1784-was-ist-aufklaerung.md), and follow the trail to page 482, where Kant writes how comfortable it is to have a book that understands for you.

The note also shows how to read a false alarm: ingestion flags the text as «SUCIO» (`3.595/thousand`), yet opening the `.meta.json` sidecar reveals that all ten marks are Wikisource `cite_note` and `cite_ref` anchors. **The number warns; it does not decide.**

The example files (`01_raw/books/kant-1784-*`, `01_raw/papers/kant-1784-*`) come from Wikisource under CC BY-SA 3.0; everything else is MIT. Delete them once you start your own library.

## Reading rooms

Copy `04_ops/salas/ilustracion.json` to `04_ops/salas/<name>.json`. The `etiqueta` selects cards by their `dominio`. The `vocabulario` regular expression suggests other readings. To include a suggested card, add its filename without the extension to `ratificados`. Keep only sources that use `[**N**]` as folio markers in `folios_en_corchetes`.

```sh
python 04_ops/scripts/sala.py ilustracion
python 04_ops/scripts/consulta_sala.py ilustracion
```

Replace `ilustracion` with your room name. The first command generates `02_wiki/indices/sala-<name>.md`. Link it from `02_wiki/index.md`. The second builds `tmp/consulta-sala-<name>/index.html`. Open it in your browser to search all texts, read a quotation in context, view its card and open the linked original. The reader works offline without a server or API key. Git ignores `tmp/`, which contains the reading copies.

### Semantic search (optional)

The reader searches for words. To search for what a passage is *about* (the same idea in other words, or in another language), you can index the room locally with the BGE-M3 model:

```sh
pip install -r requirements-semantic.txt
python 04_ops/scripts/indexar_sentido.py ilustracion
python 04_ops/scripts/buscar_sentido.py ilustracion "thinking for oneself without a guardian"
```

The index lives in `tmp/sala-sentido/<name>/`. Re-indexing only recomputes texts that changed. Each hit gives the text, the line, the sheet (`PDF p. N`, `p. N` or `sin marca de página`, meaning no page marker) and the passage. A text that changed after indexing is skipped with a warning, because its positions would be wrong. `--por-texto 2` caps how many passages one book can contribute. `--solo <regex>` restricts the search to some texts. `--json` returns output an agent can read. `--reranker` adds a second model, bge-reranker-v2-m3, which rereads the top 20 candidates and reorders them. It loads only after the first model has been released from memory.

Requirements: the first run downloads BGE-M3 (about 2.2 GB), plus another 2.2 GB if you use `--reranker`. After that it runs offline. It needs 8–16 GB of RAM. A CPU is workable for a small room of 5–20 texts; for 50 books or more a CUDA GPU is recommended (`--device cuda`).

Limits: it finds topical neighbours, across languages too, but it does not verify that a quotation exists or fix a printed page number. `PDF p. N` is the sheet of the file. Read the passage and the printed page in the original PDF or EPUB before quoting.

## What it is not

- **Not a library.** Copyrighted books stay on your disk; this repository neither ships nor shares them.
- **Not an AI product.** It calls no API. The agent comes in as a guest, on whatever subscription or tool you already use.
- **Not a decision-maker.** The agent proposes; a person reviews and signs.

## Tests

```bash
python -m pytest tests
python 04_ops/scripts/wiki_graph.py --strict
python 04_ops/scripts/censo_resumenes_en_raw.py --self-test
python 04_ops/scripts/censo_fuentes.py --self-test
```

## Where this comes from

This is the open skeleton of the research library behind my forthcoming book *Toda inteligencia es artificial* (Xoroi Edicions). It builds on the open *LLM Wiki* pattern (`raw → wiki → outputs`) popularised by Andrej Karpathy and adds what that pattern leaves open for the humanities: verification back to the page and editorial locks. The tools were written with Claude Code and Codex; every rule grew out of a real failure that had to be caught, and each script records in its header the case that produced it.

Pablo Martínez Samper · [pablomartinezsamper.com](https://pablomartinezsamper.com/index-en.html) · MIT licence
