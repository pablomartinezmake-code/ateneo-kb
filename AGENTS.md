# Ateneo-KB — reglas para los agentes

Este fichero lo leen los agentes de programación (Codex lo carga solo; Claude Code, si se le pide) antes de
tocar la biblioteca. Dice qué pueden hacer, qué no, y por qué.

## Qué es esto

Una biblioteca de investigación en Markdown y Git que trabajan juntos una persona y sus agentes. La
persona decide; los agentes leen, convierten, buscan y proponen. Se abre con Obsidian o con cualquier
editor: son ficheros de texto.

La idea que la sostiene: **un agente puede inventar una cita que suena verdadera, y ningún catálogo lo
detecta**. Comprobar que la obra existe no basta; hay que volver a la página. Todo lo que sigue sirve para
que siempre se pueda volver a la página.

## Estructura

- `00_inbox/` — captura rápida, sin procesar.
- `01_raw/` — las fuentes tal como llegaron: el original y su conversión a Markdown. **No se modifica nunca.**
- `01_raw/reference-links/` — rastros de búsqueda cuando la fuente todavía no se ha conseguido.
- `02_wiki/` — conocimiento curado: `fuentes/` (una ficha por fuente), `conceptos/`, `indices/`.
- `03_outputs/` — lo que se escribe a partir de la wiki: borradores, ensayos, informes.
- `04_ops/` — gestión: `log.md`, plantillas, guiones de control.

## No negociables

1. **`01_raw/` no se toca.** Se lee, no se edita. Si la conversión salió mal, se vuelve a convertir desde
   el original con `ingest_to_raw.py`; nunca se arregla el texto a mano.
2. **No inventar citas ni referencias.** Si la fuente no está, no se cita.
3. **Separar siempre tres niveles**: *evidencia firme* (respaldada por una fuente que se puede abrir),
   *lectura abierta* (interpretación razonable, no cerrada) e *hipótesis abierta* (especulación declarada).
4. **Toda ficha curada lleva su fuente y su nivel de confianza** en el frontmatter.
5. **El agente propone; el humano aprueba.** Nada pasa a `curado` sin una persona que lo haya leído.
6. **Toda operación deja una línea en `04_ops/log.md`**: `## [AAAA-MM-DD] ingest | qué se hizo`.

## Ingerir una fuente

1. **El original entra con la herramienta, nunca a mano**:

   ```
   python ingest_to_raw.py "ruta/al/fichero.pdf" --out 01_raw/papers --write-meta
   ```

   (`--out 01_raw/books` para libros, `--out 01_raw/papers` para artículos; por defecto, el original PDF se copia en `01_raw/pdf/` y el EPUB en `01_raw/papers/`. La opción `--original-out` permite elegir otro directorio).

   Copia el original a `01_raw/`, lo convierte a Markdown y escribe al lado un `.meta.json` que dice de
   qué fichero sale el texto y cuánto fiarse de él. Los PDF llevan una marca por hoja
   (`<!-- pdf_page: NNN -->`): es la hoja del PDF, **no** el folio impreso.

   *Por qué:* en la biblioteca de la que sale este sistema, once ficheros de `01_raw` resultaron ser
   resúmenes escritos por un agente —título, autores, «Abstract», «Key Findings»— sin la fuente detrás.
   Sostenían diez fichas marcadas como firmes. `04_ops/scripts/censo_resumenes_en_raw.py` los detecta y
   el candado de `pre-commit` impide que vuelvan a entrar.

2. **Si el original no se consigue** (muro de pago, bloqueo), no se escribe nada en `01_raw/`: se abre un
   rastro en `01_raw/reference-links/` con la plantilla `04_ops/templates/search-trail.md`.

3. **Mira la línea `Capa:` que imprime la ingesta.** Mide cuánto se equivocó el reconocimiento óptico. Un
   número bajo no certifica nada: el detector solo ve ciertas averías (ver `fidelidad_capa.py`). Antes de
   copiar una cita literal de un escaneo, se coteja con la imagen de la página.

4. **Escribe la ficha** en `02_wiki/fuentes/<autor-año-titulo>.md` con la plantilla
   `04_ops/templates/fuente.md`. La ficha declara `forma:` (libro, capitulo, articulo, prensa, web,
   audiovisual, informe, tesis, inedito, otro) y lleva en `fuentes:` la ruta al `.md` de `01_raw/`.

5. **Enlaza la ficha desde `02_wiki/index.md`** (`- [[autor-año-titulo]] — de qué trata`). El lint
   rechaza el commit si una nota de la wiki no está en el índice.

6. **Añade la línea en `04_ops/log.md`.**

## Buscar sin engañarse

Un cero no prueba que algo no esté. El reconocimiento óptico parte palabras por dentro (`sugestión` →
`su gestión`) y la búsqueda literal las pierde en silencio:

```
python buscar_tolerante.py palabra 01_raw/books/*.md            # encuentra también las partidas
python buscar_tolerante.py --auditar palabra 01_raw/books/*.md  # enseña las dudosas para que decida una persona
python palabras_partidas.py --censo 01_raw/books/*.md           # cuánto parte cada fichero
```

## Citar

- Una cita literal se copia **de la fuente**, no de la ficha ni del resumen de otro agente.
- La página se lee **en la imagen de esa página**, no se calcula desde otra hoja.
- Una cita que va a salir fuera la coteja alguien que no la escribió: releerse repite el mismo error.

## Salas de lectura

Copia `04_ops/salas/ilustracion.json` en `04_ops/salas/<nombre>.json`. La `etiqueta` selecciona las fichas por su `dominio`. El `vocabulario` es una expresión regular que propone otras lecturas. Para admitir una ficha propuesta, añade su nombre de fichero sin extensión a `ratificados`. Conserva en `folios_en_corchetes` solo las rutas que usan marcas `[**N**]` para indicar folios.

```sh
python 04_ops/scripts/sala.py ilustracion
python 04_ops/scripts/consulta_sala.py ilustracion
```

Sustituye `ilustracion` por tu nombre de sala. El primer comando genera `02_wiki/indices/sala-<nombre>.md`. Enlaza ese índice desde `02_wiki/index.md`. El segundo construye `tmp/consulta-sala-<nombre>/index.html`. Ábrelo en tu navegador para buscar en los textos, leer el contexto de una cita y consultar la ficha junto al original enlazado. Funciona sin servidor, claves ni conexión. Git ignora `tmp/`, que contiene las copias de lectura.

### Búsqueda por sentido (opcional)

```sh
pip install -r requirements-semantic.txt      # BGE-M3: ~2,2 GB la primera vez; --reranker, otros ~2,2 GB
python 04_ops/scripts/indexar_sentido.py <sala>
python 04_ops/scripts/buscar_sentido.py <sala> "<pregunta>" [--por-texto 2] [--solo <regex>] [--reranker] [--json]
```

- **Un hallazgo es un candidato, no una cita.** Encuentra pasajes que hablan de lo mismo, también en otra
  lengua. Antes de citar, se abre el raw en la línea que da y se coteja con la imagen de esa página.
- **`PDF p. N` es la hoja del fichero, no el folio impreso.** El folio se lee en la imagen.
- **Un aviso de huella no se ignora.** Si el raw cambió después de indexarse, sus posiciones serían falsas:
  se vuelve a correr `indexar_sentido.py`.
- **Que no salga por sentido no prueba que no esté.** Antes de decirlo, «Buscar sin engañarse», arriba.

## Lint

Antes de cerrar una sesión que escribió en la wiki:

```
python 04_ops/scripts/wiki_graph.py --strict
```

Tiene que decir `0 links rotos`. Los enlaces `[[...]]` solo resuelven contra `02_wiki/`: un fichero de
`03_outputs/` se cita por su ruta, no con doble corchete.

## Frontmatter de una ficha

```yaml
---
tipo: fuente | concepto | reflexion
forma: libro | capitulo | articulo | prensa | web | audiovisual | informe | tesis | inedito | otro  # solo fuentes
dominio: libre (separa varios con " | ")
estado: raw | review | curado | publicado
origen: web | manual | consulta
fuentes:
  - 01_raw/...            # una ruta por línea; la forma [01_raw/...] no la leen los candados
confianza: firme | media | abierta
actualizado: AAAA-MM-DD
vigencia: vigente | provisional | superado
ultima_verificacion: AAAA-MM-DD
superado_por:
---
```

## Principio rector

El agente es el programador de la biblioteca; la persona es quien revisa y firma. Lo que está en la wiki
es lo que una persona ha aprobado.
