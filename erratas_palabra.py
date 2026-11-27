"""Candidatos alfabéticos de OCR; evidencia para revisar, nunca correcciones.

La frecuencia es interna al testigo. Se buscan sustituciones b/h y se conserva
la caja para distinguir los nombres de las palabras comunes.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import unicodedata

VERSION = '2026-10-04.2'
MIN_LENGTH = 4
MIN_REFERENCE_COUNT = 20
MAX_FREQUENCY_RATIO = 0.4
# Provisional: controles EPUB <=0.296/mil; Hoyos=1.120/mil (19 confirmadas).
# No es certificado de limpieza por debajo: solo prioriza la revisión.
REVIEW_PER_THOUSAND = 0.5
CONFUSIONS = {'b': 'h', 'h': 'b', 'B': 'H', 'H': 'B'}
WORD = re.compile(r'[^\W\d_]+', re.UNICODE)


def medir(text: str) -> dict:
    # Import local: fidelidad_capa calls this function, keeping legacy rules intact.
    from fidelidad_capa import sin_andamio
    text = unicodedata.normalize('NFC', sin_andamio(text))
    matches = list(WORD.finditer(text))
    counts = Counter(m.group() for m in matches)
    first = {}
    for m in matches:
        first.setdefault(m.group(), m)
    candidates = []
    for token, count in sorted(counts.items()):
        if len(token) < MIN_LENGTH:
            continue
        alternatives = []
        for i, letter in enumerate(token):
            if letter in CONFUSIONS:
                other = token[:i] + CONFUSIONS[letter] + token[i + 1:]
                n = counts[other]
                if n >= MIN_REFERENCE_COUNT and count / n <= MAX_FREQUENCY_RATIO:
                    alternatives.append((n, other))
        if not alternatives:
            continue
        n, other = sorted(alternatives, key=lambda p: (-p[0], p[1]))[0]
        m = first[token]
        candidates.append({'token': token, 'count': count, 'candidate': other,
                           'candidate_count': n, 'ratio': round(count / n, 4),
                           'alternatives': [w for _, w in sorted(alternatives)],
                           'context': text[max(0, m.start()-70):m.end()+70].replace('\n', ' '),
                           'line_in_cleaned_text': text.count('\n', 0, m.start()) + 1})
    candidates.sort(key=lambda p: (-p['count'], p['token']))
    occurrences = sum(p['count'] for p in candidates)
    rate = occurrences / len(matches) * 1000 if matches else 0.0
    return {'detector': VERSION, 'words': len(matches), 'occurrences': occurrences,
            'distinct_candidates': len(candidates),
            'per_thousand_words': round(rate, 3),
            'review_threshold': REVIEW_PER_THOUSAND,
            'decision': 'review' if rate >= REVIEW_PER_THOUSAND else 'below_alert_not_proof_of_clean_text',
            'parameters': {'min_length': MIN_LENGTH, 'min_reference_count': MIN_REFERENCE_COUNT,
                           'max_frequency_ratio': MAX_FREQUENCY_RATIO, 'substitutions': 'b/h, case-sensitive'},
            'candidates': candidates}


def aviso(measure: dict) -> str:
    if measure['occurrences']:
        label = 'REVISAR' if measure['decision'] == 'review' else 'Candidatos bajo la alerta'
        return (f"{label} variantes alfabéticas: {measure['occurrences']} apariciones "
                f"({measure['per_thousand_words']}/mil); candidatos, no errores confirmados")
    return 'sin candidatos alfabéticos; esto no certifica limpieza'


def lectura_integrada(legacy: str, measure: dict) -> str:
    return aviso(measure) + '. Lectura de las marcas previas: ' + legacy


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='+')
    args = parser.parse_args()
    for path in args.paths:
        p = Path(path)
        print(json.dumps({'file': str(p), **medir(p.read_text(encoding='utf-8'))}, ensure_ascii=False))


if __name__ == '__main__':
    main()
