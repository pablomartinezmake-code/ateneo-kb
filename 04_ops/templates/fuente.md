---
tipo: fuente
forma:
dominio:
estado: review
origen:
fuentes:
  - 
confianza: abierta
actualizado: {{date:YYYY-MM-DD}}
vigencia: provisional
ultima_verificacion: {{date:YYYY-MM-DD}}
superado_por:
search_trail:
identificador_estable:
---
> [!note] Completar antes de considerar esta nota como curada
> - `forma` (libro | capitulo | articulo | prensa | web | audiovisual | informe | tesis | inedito | otro), `dominio` y `origen`
> - `confianza` y `vigencia`
> - `search_trail` si la referencia se fijo por lookup
> - `identificador_estable` con DOI, URL canonica o ISBN si aplica

## Intent de ingesta

- **Línea declarada por el humano**:
- **Pregunta orientadora del agente** (completar solo si no hubo intent declarado):
- **Respuesta del humano** (si hubo pregunta):

## Cita Chicago

*Completar cita en formato Chicago Author-Date o Notes and Bibliography.*

## Proveniencia

- **Raw / path**:
- **Search trail**:
- **Identificador estable**:

## Huella literal breve

*Terminos o frases-puntero que aparecen **literalmente** en el raw, sin desarrollarlos, sin paginar. Funcion: indice rapido para que un humano encuentre el pasaje en el raw o PDF cuando llegue el momento de citar. **No es banco de citas** — es puntero al raw. Si la fuente va a producir cita literal en cuerpo de libro o paper, la cita firmada vive en `03_outputs/`, junto a la pieza que la usa, no aqui. Tipico: 3-8 entradas.*

Terminos a recuperar en lectura humana: `...`, `...`.

## Atlas de citas

*1-3 frases literales con paginacion firme, seleccionadas **por densidad**, no por funcion argumental. Funcion: que la nota sostenga una clase, una presentacion o una relectura en voz alta sin tener que abrir el PDF. Cotejadas contra raw/PDF; fechar el cotejo. Sin parafrasis dentro, sin comentario dentro (el comentario vive en `Resumen` o en `Tension con la wiki`). Si una cita migra al cuerpo del libro o del paper, se duplica en la pieza de `03_outputs/` con funcion argumental marcada — la cita del Atlas queda en la nota como objeto pedagogico. Maximo 3, regla dura: la cita que ya no defiende la fuente sale.*

> *«Cita literal exacta.»* — Autor, p. NN.

> *«Segunda cita.»* — Autor, p. NN.

> *«Tercera cita.»* — Autor, p. NN.

*Glosa breve opcional (1-2 lineas): criterio de seleccion, fecha de cotejo, orientacion de cada cita. No es interpretacion de las frases.*

## Resumen



## Takeaways

### Evidencia firme


### Lectura abierta


### Hipotesis abierta


## Relacion con el proyecto



## Conceptos vinculados

- 

## Claims estructurados

```claims
# Duplica y completa un item por cada claim importante extraido de la fuente.
# `tipo`: evidencia | lectura | hipotesis
# `confianza`: firme | media | abierta
# `vigencia`: vigente | provisional | superado | stale
# `soporte`: lista de notas enlazadas (doble corchete) o identificadores estables
[]
```

## Tensión con la wiki

*El agente completa esta sección solo si detecta tensión con nodos existentes en `02_wiki/`. Si no hay tensión, dejar la sección vacía o eliminarla en la nota final. Mientras haya tensión sin resolver, la nota permanece en estado `review`, no `curado`.*

- **Confirma**: (enlace a la nota) — nota:
- **Matiza**: (enlace a la nota) — nota:
- **Contradice**: (enlace a la nota) — nota:
- **Supera**: (enlace a la nota) — nota:

### Decisión humana pendiente

- Propuesta por defecto del agente:
- Alternativa(s):
