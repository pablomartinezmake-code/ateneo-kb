"use strict";
// Búsqueda de la consulta de sala. La página la carga antes de su propio
// script; estas funciones quedan globales. Node las recibe por module.exports.

// Las siete ligaduras que algunos PDF guardan como un carácter («signiﬁcado»), las mismas que consulta_sala.py.
// Los datos ya llegan sin ellas; aquí, para la pregunta y para la cita de la ficha copiada del raw.
const LIG = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"};
const sinLig = s => s.replace(/(?<=\p{L})\u00ad(?:[ \t]*\r?\n[ \t]*)?(?=\p{L})/gu, "").replace(/\u00ad/g, "").replace(/[ﬀ-ﬆ]/g, c => LIG[c]);
const norm = s => sinLig(s).replace(/(\p{L})-[ \t]*\n[ \t]*(\p{L})/gu, "$1$2").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ");
const VAR = {a:"aáàâäã", e:"eéèêë", i:"iíìîï", o:"oóòôöõ", u:"uúùûü", n:"nñ", c:"cç"};
const escRx = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// Palabras de PARTIR letras o más se buscan también partidas por un espacio dentro, como deja el OCR o el
// EPUB al quitar un guion de fin de verso («trans lated»). Las más cortas, no: «de» partida
// casaría en cualquier parte. Lo que solo casa partido se marca para mirarlo («su gestión» no es «sugestión»).
const PARTIR = 5;
// Sobre el texto original: tildes optativas, y entre letra y letra el guion de fin de línea (y el espacio, si se parte).
const tolerante = (w, partir) => [...w].map(ch => (VAR[ch] ? "[" + VAR[ch] + VAR[ch].toUpperCase() + "]" : escRx(ch))).join(partir ? "(?:-[ \\t]*\\n[ \\t]*|\\s)?" : "(?:-[ \\t]*\\n[ \\t]*)?");

// Una palabra suelta casa por el principio («traduc» → traducción); una frase entre comillas,
// entera: «"inteligencia artificial"» no casa con «artificializada».
function terminos(q) {
  const out = [];
  q.replace(/"([^"]+)"|«([^»]+)»|(\S+)/g, (_, a, b, c) => { const t = norm(a || b || c).trim(); if (t.length > 1) out.push({t, exacta: !c}); });
  return out;
}
function buscar(q) {
  const ts = terminos(q);
  if (!ts.length) return null;
  ts.forEach(x => { x.partir = x.t.replace(/ /g, "").length >= PARTIR; });
  const cierre = exacta => (exacta ? "(?![\\p{L}\\p{N}])" : "");
  // La partida tiene que acabar donde acaba la palabra: si no, «to translate defining» casa como
  // «translate d|efining» (medido: 3 de las 4 «translated» partidas de la sala eran eso).
  const FIN = "(?![\\p{L}\\p{N}])";
  const huecos = t => (t.match(/ /g) || []).length;
  const rx = ts.map(({t, exacta, partir}) => ({
    literal: new RegExp("\\b" + escRx(t) + cierre(exacta), "gu"),
    partida: partir ? new RegExp("\\b" + t.split(" ").map(p => [...p].map(escRx).join(" ?")).join(" ") + FIN, "gu") : null,
    huecos: huecos(t),
  }));
  const porLibro = [];
  L.forEach((l, li) => {
    const hits = [];
    l.u.forEach((u, ui) => {
      let total = 0, partidas = 0;
      for (const r of rx) {
        const lit = (u.n.match(r.literal) || []).length;
        const par = r.partida ? (u.n.match(r.partida) || []).filter(m => huecos(m) > r.huecos).length : 0;
        if (!lit && !par) return;
        total += lit + par; partidas += par;
      }
      hits.push({ui, total, partidas});
    });
    if (hits.length) porLibro.push({li, hits, total: hits.reduce((a, h) => a + h.total, 0)});
  });
  porLibro.sort((a, b) => b.total - a.total);
  const marcas = new RegExp(ts.map(({t, exacta, partir}) =>
    "(?:" + t.split(" ").map(p => tolerante(p, false)).join("\\s+") + ")" + cierre(exacta) +
    (partir ? "|(?:" + t.split(" ").map(p => tolerante(p, true)).join("\\s+") + ")" + FIN : "")).join("|"), "giu");
  return {q, ts, porLibro, marcas};
}
function marcar(texto, rx) {
  if (!rx) return esc(texto);
  let out = "", i = 0;
  texto.replace(rx, (m, ...a) => { const p = a[a.length - 2]; out += esc(texto.slice(i, p)) + "<mark>" + esc(m) + "</mark>"; i = p + m.length; return m; });
  return out + esc(texto.slice(i));
}

// La etiqueta manda: jamás buscamos una cita PDF en otra hoja para «corregir» la ficha.
function leerCita(linea) {
  const s = linea.replace(/^>\s*/gm, "").replace(/\r?\n/g, " ").trim();
  const m = /^\*?«([\s\S]+?)»\*?\s*—\s*(PDF p\.\s*(\d+)\b|p\.\s*(\d+)\b|EPUB(?:,\s*«([^»]+)»)?)/u.exec(s);
  if (!m) return null;
  return {texto: m[1].replace(/\*\*?|`/g, "").trim(), tipo: m[3] ? "pdf" : m[4] ? "folio" : "epub",
    pagina: Number(m[3] || m[4]) || null, seccion: m[5] || "", localizador: m[2]};
}
const textoLiteral = s => sinLig(s).replace(/\*\*/g, "").replace(/(\p{L})-[ \t]*\n[ \t]*(\p{L})/gu, "$1$2").replace(/\s+/g, " ").trim();
const contieneCita = (u, c) => textoLiteral(u.t).includes(textoLiteral(c.texto));
function destinoCita(libro, cita) {
  const c = typeof cita === "string" ? leerCita(cita) : cita;
  if (!c || !c.texto) return {ui: null, literal: false};
  const candidatas = libro.u.map((u, ui) => ({u, ui})).filter(({u}) => {
    if (c.tipo === "folio") return u.e === "p. " + c.pagina;
    if (c.tipo === "pdf") return new RegExp("^PDF p\\. " + c.pagina + "(?: (?:izquierda|derecha))?$").test(u.e);
    return !c.seccion || u.e === c.seccion || u.e.startsWith(c.seccion + " · tramo ");
  });
  const literal = candidatas.find(({u}) => contieneCita(u, c));
  // Sin sección, un EPUB solo tiene destino si el propio texto permite localizarlo.
  const destino = literal || (c.tipo !== "epub" || c.seccion ? candidatas[0] : null);
  return {ui: destino ? destino.ui : null, literal: !!literal};
}

// Un único escape para búsqueda, lector y Markdown. Ningún HTML de la ficha se interpreta.
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;", "'":"&#39;"}[c]));
function inlineFicha(texto, libros) {
  const rx = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]|\[([^\]]+)\]\(([^)]+)\)|`([^`]+)`|\*\*([^*]+)\*\*|\*([^*]+)\*/g;
  let out = "", pos = 0;
  for (const m of texto.matchAll(rx)) {
    out += esc(texto.slice(pos, m.index));
    if (m[1]) {
      const slug = m[1].replace(/^.*\//, "").replace(/\.md$/, "");
      const li = libros.findIndex(l => l.ficha_id === slug || l.id === slug);
      out += li >= 0 ? `<button class="mas" data-libro="${li}">${esc(m[2] || m[1])}</button>` : esc(m[2] || m[1]);
    } else if (m[3]) {
      out += /^https?:\/\/[^\s]+$/i.test(m[4]) ? `<a href="${esc(m[4])}" target="_blank" rel="noopener noreferrer">${esc(m[3])}</a>` : esc(m[3]);
    } else if (m[5]) out += `<code>${esc(m[5])}</code>`;
    else if (m[6]) out += `<strong>${esc(m[6])}</strong>`;
    else out += `<em>${esc(m[7])}</em>`;
    pos = m.index + m[0].length;
  }
  return out + esc(texto.slice(pos));
}
function bloquesFicha(texto) {
  const lineas = texto.replace(/\r/g, "").split("\n"), out = [];
  for (let i = 0; i < lineas.length;) {
    const l = lineas[i];
    if (!l.trim()) { i++; continue; }
    if (/^>/.test(l)) {
      let b = [];
      while (i < lineas.length && /^>/.test(lineas[i])) {
        const t = lineas[i++].replace(/^>\s?/, "");
        if (b.length && /^\*?«/.test(t) && /»\*?\s*—/.test(b.join(" "))) {
          out.push({tipo: "cita", texto: b.join("\n")}); b = [];
        }
        b.push(t);
      }
      out.push({tipo: "cita", texto: b.join("\n")});
    } else if (/^#{2,6}\s/.test(l)) { out.push({tipo: "titulo", texto: l.replace(/^#+\s+/, "")}); i++; }
    else if (/^\s*(?:[-*+] |\d+\. )/.test(l)) { out.push({tipo: "lista", texto: l.replace(/^\s*(?:[-*+] |\d+\. )/, ""), orden: /^\s*\d+\./.test(l)}); i++; }
    else if (l.includes("|") && i + 1 < lineas.length && /^\s*\|?\s*:?-{3,}/.test(lineas[i+1])) {
      const filas = [l]; i += 2;
      while (i < lineas.length && lineas[i].includes("|") && lineas[i].trim()) filas.push(lineas[i++]);
      out.push({tipo: "tabla", filas});
    } else {
      const b = [lineas[i++]];
      while (i < lineas.length && lineas[i].trim() && !/^(?:>|#{2,6}\s|\s*[-*+] |\s*\d+\. )/.test(lineas[i])) {
        if (i + 1 < lineas.length && lineas[i].includes("|") && /^\s*\|?\s*:?-{3,}/.test(lineas[i+1])) break;
        b.push(lineas[i++]);
      }
      out.push({tipo: "parrafo", texto: b.join(" ")});
    }
  }
  return out;
}
function citasFicha(texto) { return bloquesFicha(texto).filter(b => b.tipo === "cita").map(b => leerCita(b.texto)).filter(Boolean); }
// Bajo cada cita, sus actos en el registro de cotejos (04_ops/cotejos/citas.jsonl): quién miró la imagen y qué salió.
function actosDeCita(linea, actos) {
  const n = norm(linea);
  return actos.filter(a => n.includes(norm(a.cita)));
}
function renderFicha(texto, libros = [], actos = []) {
  const inl = s => inlineFicha(s, libros);
  const registro = linea => actosDeCita(linea, actos).map(a =>
    `<span class="e">registro: ${esc(a.acto)} · ${esc(a.lector)} · ${esc(a.fecha)} · ${esc(a.resultado.split(":").slice(0, 2).join(":"))}` +
    `${a.folio_visto ? ` · folio visto ${esc(a.folio_visto)}` : ""} · capa: ${esc(a.capa)}</span>`).join("");
  let cita = 0, lista = "", out = "";
  for (const b of bloquesFicha(texto)) {
    const tag = b.tipo === "lista" ? (b.orden ? "ol" : "ul") : "";
    if (lista !== tag) { if (lista) out += `</${lista}>`; if (tag) out += `<${tag}>`; lista = tag; }
    if (b.tipo === "titulo") out += `<h2>${inl(b.texto)}</h2>`;
    else if (b.tipo === "lista") out += `<li>${inl(b.texto)}</li>`;
    else if (b.tipo === "cita") out += leerCita(b.texto) ? `<button class="pasaje" data-cita="${cita++}">${inl(b.texto)}${registro(b.texto)}</button>` : `<blockquote>${inl(b.texto)}</blockquote>`;
    else if (b.tipo === "tabla") {
      const celdas = f => f.trim().replace(/^\||\|$/g, "").split("|");
      out += '<div class="tabla-ficha"><table>' + b.filas.map((f, i) => `<tr>${celdas(f).map(c => `<${i ? "td" : "th"}>${inl(c.trim())}</${i ? "td" : "th"}>`).join("")}</tr>`).join("") + "</table></div>";
    } else out += `<p>${inl(b.texto)}</p>`;
  }
  return out + (lista ? `</${lista}>` : "");
}
// Se parte del comienzo real de la cita. Nunca se sustituye por palabras de la búsqueda.
function marcaCita(cita, texto) {
  const palabras = norm(cita.texto).trim().split(/\s+/);
  for (let n = palabras.length; n > 0; n--) {
    const rx = new RegExp(palabras.slice(0, n).map(p => tolerante(p, false)).join("\\s+"), "iu");
    const m = rx.exec(texto);
    if (m) return {inicio: m.index, fin: m.index + m[0].length};
  }
  return null;
}
function marcarCita(texto, cita, literal) {
  const m = literal && marcaCita(cita, texto);
  return m ? esc(texto.slice(0, m.inicio)) + "<mark>" + esc(texto.slice(m.inicio, m.fin)) + "</mark>" + esc(texto.slice(m.fin)) : esc(texto);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { norm, terminos, buscar, marcar, tolerante, escRx, VAR, PARTIR,
    leerCita, destinoCita, renderFicha, actosDeCita, citasFicha, bloquesFicha, marcarCita, esc };
}
