"""Evidencia estructural de escaneo, sobre todas las hojas; no certifica el OCR.

Una imagen individual debe cubrir >70% del CropBox. >=80% de hojas así
constituye escaneo; >=10% de hojas con imagen y texto se declara mixto. Una
lámina aislada sin texto no hace escaneado un libro digital. No se deduce
calidad, procedencia histórica ni lengua a partir del nombre del fichero.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from time import perf_counter

import pymupdf

LANGUAGES = {'en': 'eng', 'fr': 'fra', 'es': 'spa', 'de': 'deu', 'el': 'ell',
             'eng': 'eng', 'fra': 'fra', 'spa': 'spa', 'deu': 'deu', 'grc': 'grc'}
WORDS = {
    'eng': set('the and of to in is that with for this not are was from'.split()),
    'fra': set('le la les des une dans est que qui pour pas sur avec'.split()),
    'spa': set('el los las del una que por para con como pero se'.split()),
    'deu': set('der die das und ist nicht sich ein eine von mit den zu'.split()),
}

# Entre 1,1% (cubiertas digitales) y 35% (escaneo mixto) en los testigos:
# exigir 10% evita pedir un OCR completo por una cubierta con título.
MIN_FRACCION_MIXTO_CON_CAPA = .10


class _PaintCounts(pymupdf.mupdf.FzDevice2):
    """Count the native spans visited by get_texttrace without copying glyphs."""

    def __init__(self):
        super().__init__()
        self.spans = self.invisible = 0
        self.may_have_images = False
        self.use_virtual_fill_text()
        self.use_virtual_stroke_text()
        self.use_virtual_ignore_text()
        self.use_virtual_fill_image()
        self.use_virtual_fill_image_mask()
        self.use_virtual_fill_shade()

    def fill_text(self, ctx, text, *args):
        span = text.head
        while span:
            self.spans += 1
            span = span.next

    stroke_text = fill_text

    def ignore_text(self, ctx, text, *args):
        before = self.spans
        self.fill_text(ctx, text)
        self.invisible += self.spans - before

    def fill_image(self, *args):
        self.may_have_images = True

    fill_image_mask = fill_shade = fill_image


def _paint_counts(page):
    # Match the audited native trace and structured-text devices. On another
    # binding or a rotated page, use the original high-level APIs.
    if pymupdf.version[:2] == ('1.27.2.2', '1.27.2') and not page.rotation:
        device = _PaintCounts()
        mu = pymupdf.mupdf
        mu.fz_run_page(page.this, device, mu.FzMatrix(), mu.FzCookie())
        mu.fz_close_device(device)
        return device.spans, device.invisible, device.may_have_images
    spans = page.get_texttrace()
    return len(spans), sum(s['type'] == 3 for s in spans), True


def evaluar(path: str | Path, textos=None) -> dict:
    start = perf_counter()
    counts = Counter()
    words = Counter()
    details = []
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise ValueError('PDF cifrado: falta contraseña')
        producer = doc.metadata.get('producer') or ''
        creator = doc.metadata.get('creator') or ''
        _, lang = doc.xref_get_key(doc.pdf_catalog(), 'Lang')
        for n, page in enumerate(doc, 1):
            # Las coordenadas de imagen/texto son sin rotación.
            rect = page.rect * page.derotation_matrix
            area = rect.get_area()
            spans, invisible, may_have_images = _paint_counts(page)
            coverage = max((
                (pymupdf.Rect(i['bbox']) & rect).get_area() / area
                for i in page.get_image_info()
            ), default=0) if area and may_have_images else 0
            text = page.get_text() if textos is None else textos[n - 1]
            has_text = bool(text.strip())
            words.update(re.findall(r'[^\W\d_]+', text.lower()))
            full = coverage > .70
            counts['imagen_entera'] += full
            counts['imagen_entera_con_texto'] += full and has_text
            counts['imagen_entera_sin_texto'] += full and not has_text
            counts['paginas_con_texto'] += has_text
            counts['paginas_texto_sin_imagen_entera'] += has_text and not full
            counts['texto_invisible_mayoritario'] += invisible > spans / 2
            details.append({'pagina_pdf': n, 'cobertura_maxima': round(coverage, 6),
                            'caracteres': len(text.strip()), 'spans': spans,
                            'spans_invisibles': invisible})
        total = len(doc)
    scores = {code: sum(words[w] for w in vocabulary) for code, vocabulary in WORDS.items()}
    ranking = sorted(scores, key=scores.get, reverse=True)
    language = LANGUAGES.get(lang.lower().split('-')[0])
    evidence = {'metadato_Lang': lang, 'palabras_funcionales': scores}
    heuristic = (ranking[0] if scores[ranking[0]] >= 30
                 and scores[ranking[0]] >= 2 * max(1, scores[ranking[1]]) else None)
    declared = language or (lang if lang and lang != 'null' else None)
    if declared and heuristic and declared != heuristic:
        language = None
        evidence['candidatas'] = [declared, heuristic]
        evidence['criterio'] = 'conflicto entre /Lang y palabras funcionales; requiere elección humana'
    elif language:
        evidence['criterio'] = 'metadato PDF /Lang (declaración, no verificación lingüística)'
    elif heuristic:
        language = heuristic
        evidence['criterio'] = 'heurística: >=30 palabras funcionales y doble del segundo; revisar'
    else:
        evidence['criterio'] = 'lengua no determinada; requiere elección humana'
    scan = counts['imagen_entera'] / total if total else 0
    if scan >= .8:
        verdict = 'escaneo_con_capa' if counts['imagen_entera_con_texto'] else 'escaneo_sin_capa'
    elif total and counts['imagen_entera_con_texto'] / total >= MIN_FRACCION_MIXTO_CON_CAPA:
        verdict = 'mixto_con_capa'
    elif re.search(r'ocr|capture|clearscan|abbyy|tesseract|ricoh', producer + ' ' + creator, re.I):
        verdict = 'indeterminado'
    elif counts['paginas_con_texto']:
        verdict = 'nacido_digital'
    else:
        verdict = 'indeterminado'
    return {
        'detector': '2026-11-01.2', 'veredicto': verdict,
        'criterio': 'imagen individual >70% de hoja; escaneo >=80% de hojas; mixto >=10% de hojas con imagen+texto',
        'paginas': total, 'paginas_medidas': total,
        **{key: counts[key] for key in (
            'imagen_entera', 'imagen_entera_con_texto', 'imagen_entera_sin_texto',
            'paginas_con_texto', 'paginas_texto_sin_imagen_entera', 'texto_invisible_mayoritario')},
        'fraccion_hojas_imagen_entera': scan,
        'producer': producer, 'creator': creator,
        'lengua': language, 'evidencia_lengua': evidence,
        'segundos': round(perf_counter() - start, 4), 'hojas': details,
        'limite': 'No detecta con certeza raster fragmentado, vectorizado o ClearScan; nacido_digital es apariencia estructural, no historial certificado.',
    }


def quote_ps(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def aviso_segundo_lector(result: dict, source: Path, output: Path) -> str | None:
    if result.get('veredicto') not in ('escaneo_con_capa', 'mixto_con_capa'):
        return None
    executable = shutil.which('tesseract') or r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    try:
        proc = subprocess.run([executable, '--list-langs'], capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=10)
        installed = set(proc.stdout.splitlines()[1:]) if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        installed = None
    language = result.get('lengua')
    if language and installed is not None and language not in installed:
        return (f'Segundo lector: lengua {language}; falta el paquete {language} en Tesseract. '
                'Instalarlo antes de preparar la orden; no se ejecutó OCR.')
    script = Path(__file__).resolve().parent / '04_ops/scripts/ocr_tesseract.py'
    destination = output.with_name(output.stem + '-tesseract.md')
    command = (f"$env:TESS_LANG={quote_ps(language or '<lengua>')}; "
               f'& {quote_ps(sys.executable)} {quote_ps(script)} '
               f'{quote_ps(source.resolve())} {quote_ps(destination.resolve())}')
    note = '' if language else ' Elegir <lengua> antes de pegar; no se presupone spa.'
    candidates = result.get('evidencia_lengua', {}).get('candidatas')
    if candidates:
        note += f' Candidatas: /Lang={candidates[0]}; palabras funcionales={candidates[1]}.'
    if installed is None:
        note += ' No se pudo comprobar qué paquetes de Tesseract están instalados.'
    elif not language:
        note += ' Instalados: ' + ', '.join(sorted(installed)) + '.'
        if 'deu' not in installed:
            note += ' Para alemán falta deu.'
    # El comentario conserva la orden pegable incluso copiando toda la línea.
    return f'Segundo lector: {command}' + (f' # {note.strip()}' if note else '')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', nargs='+', type=Path)
    args = parser.parse_args()
    for pdf in args.pdf:
        try:
            result = evaluar(pdf)
        except Exception as exc:
            result = {'error': str(exc)}
        print(json.dumps({'pdf': str(pdf), **result}, ensure_ascii=True))
