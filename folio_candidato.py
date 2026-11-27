"""Folio candidato: el número que cada página del PDF imprime en su pie o su cabecera.

Por cada página del PDF toma el número suelto que haya en la franja superior o inferior y lo
anota como folio CANDIDATO DE ESA MISMA PÁGINA. No es una lectura: es lo que dice la capa de
texto, situado en su página. Sirve para no confundir el folio al pie de una página con la
cabecera de la siguiente.

Qué detecta:
- si el libro lleva el folio arriba o abajo (lo decide la mayoría de las páginas);
- páginas sin folio legible (se dejan vacías y, si las vecinas cuadran, se deduce entre ellas,
  marcado como `deducido`);
- escaneo a doble página: dos números en la misma franja, uno en cada mitad.

Uso:
    python folio_candidato.py LIBRO.pdf [--desde N --hasta M] [--json salida.json]

La página impresa se sigue leyendo en la imagen de la página del pasaje. Esto solo dice dónde
mirar y qué número esperar.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import fitz

NUMERO = re.compile(r'^\d{1,4}$')
PAGINACION_MARCADORES = {'page', 'página', 'pagina', 'sur', 'of', 'von', 'de'}


def palabras_en_la_hoja(page, textpage=None):
    """Palabras con su caja en la hoja tal como se ve, con el giro de la página aplicado.

    PyMuPDF da las cajas sin girar. En un escaneo girado 90°, la posición
    del pie debe transformarse a las coordenadas visibles.
    """
    m = page.rotation_matrix
    salida = []
    for x0, y0, x1, y1, texto, *resto in page.get_text('words', textpage=textpage):
        caja = fitz.Rect(x0, y0, x1, y1) * m
        salida.append((caja.x0, caja.y0, caja.x1, caja.y1, texto, *resto))
    return salida


def numeros_en_franja(page, arriba, palabras=None):
    """Números sueltos en el renglón más alto o el más bajo de la página: [(valor, x_centro)].

    La distancia entre el folio y el borde varía. Se toma el renglón extremo.
    """
    r = page.rect
    if palabras is None:
        palabras = palabras_en_la_hoja(page)
    if not palabras:
        return []
    tolerancia = r.height * 0.01
    if arriba:
        borde = min(w[1] for w in palabras)
        renglon = [w for w in palabras if w[1] <= borde + tolerancia]
    else:
        borde = max(w[3] for w in palabras)
        renglon = [w for w in palabras if w[3] >= borde - tolerancia]
    salida = []
    for x0, y0, x1, y1, texto, *_ in renglon:
        t = texto.strip('.[]()')
        if NUMERO.match(t):
            salida.append((int(t), (x0 + x1) / 2))
    return salida


def _paginacion_del_fichero(page, palabras=None):
    """Detecta una fórmula de paginación del archivo en un renglón extremo.

    Solo se reconoce el patrón explícito ``N marcador M`` o ``N / M`` en la
    cabecera/pie. Los números del cuerpo quedan fuera de esta regla y los dos
    números de la fórmula no alimentan el modelo de folios.
    """
    r = page.rect
    if palabras is None:
        palabras = palabras_en_la_hoja(page)
    if not palabras:
        return []
    extremos = []
    for arriba in (True, False):
        borde = min(w[1] for w in palabras) if arriba else max(w[3] for w in palabras)
        tol = r.height * 0.01
        extremos.extend(w for w in palabras if (w[1] <= borde + tol if arriba else w[3] >= borde - tol))
    # Deduplicate words that belong to both selections on very short pages.
    vistos = set()
    unicas = []
    for w in extremos:
        clave = tuple(w[:5])
        if clave not in vistos:
            vistos.add(clave)
            unicas.append(w)
    lineas = defaultdict(list)
    for w in unicas:
        lineas[round(w[1], 1)].append(w)
    encontrados = []
    for ws in lineas.values():
        if not (ws[0][1] <= r.height * 0.1 or ws[0][3] >= r.height * 0.9):
            continue
        ws.sort(key=lambda w: w[0])
        tokens = [w[4].strip('.,;:()[]') for w in ws]
        for i in range(len(tokens) - 2):
            primero, marcador, segundo = tokens[i:i + 3]
            prefijo = tokens[i - 1].casefold() if i else None
            marcador_es_formula = (
                marcador == '/' and (i == 0 and len(tokens) == 3)
            ) or (
                marcador.casefold() in PAGINACION_MARCADORES
                and prefijo in {'page', 'seite', 'página', 'pagina'}
            )
            if NUMERO.fullmatch(primero) and NUMERO.fullmatch(segundo) \
                    and int(segundo) >= int(primero) and marcador_es_formula:
                encontrados.append((int(primero), int(segundo),
                                    (ws[i][0] + ws[i][2]) / 2,
                                    (ws[i + 2][0] + ws[i + 2][2]) / 2))
        for i, token in enumerate(tokens):
            compacto = re.fullmatch(r'(\d{1,4})/(\d{1,4})', token)
            if compacto and int(compacto.group(2)) >= int(compacto.group(1)) \
                    and len(tokens) == 1 and ws[i][4] == token:
                encontrados.append((int(compacto.group(1)), int(compacto.group(2)),
                                    (ws[i][0] + ws[i][2]) / 2, None))
    return encontrados


def fila_de_pagina(page, n, textpage=None):
    # La misma extracción alimenta ambas franjas y la fórmula del fichero.
    palabras = palabras_en_la_hoja(page, textpage)
    paginacion = _paginacion_del_fichero(page, palabras)
    posiciones_paginacion = {(par[0], par[2]) for par in paginacion}
    posiciones_paginacion.update(
        (par[1], par[3]) for par in paginacion if par[3] is not None
    )
    def conservar(par):
        return not any(par[0] == valor and abs(par[1] - centro) < 1
                       for valor, centro in posiciones_paginacion)
    arriba = [par for par in numeros_en_franja(page, True, palabras) if conservar(par)]
    abajo = [par for par in numeros_en_franja(page, False, palabras) if conservar(par)]
    return dict(pagina_pdf=n, ancho=page.rect.width,
                apaisada=page.rect.width > page.rect.height,
                palabras=len(palabras), arriba=arriba, abajo=abajo,
                paginacion_del_fichero=[par[0] for par in paginacion])


def leer(pdf, desde=1, hasta=None):
    filas = []
    with fitz.open(pdf) as doc:          # cerrado al salir: el alta escribe después
        hasta = hasta or len(doc)
        for n in range(desde, hasta + 1):
            filas.append(fila_de_pagina(doc[n - 1], n))
    return filas


def diagnostico_de_capa(filas):
    """Comprueba si existe capa de texto antes de proponer folios."""
    return 'ok' if any(f['palabras'] for f in filas) else 'sin_capa'


def posicion_del_folio(filas):
    """Arriba o abajo: donde más páginas traen un número que avanza con la página."""
    votos = {'arriba': 0, 'abajo': 0}
    for lado in votos:
        # un folio avanza con la hoja: cuenta los pares de hojas a distancia 1 o 2 cuyo número
        # sube lo mismo que la hoja (o el doble, en los escaneos a doble página). La distancia 2
        # cubre libros que solo imprimen folio en las impares.
        for i, a in enumerate(filas):
            for b in filas[i + 1:i + 3]:
                d = b['pagina_pdf'] - a['pagina_pdf']
                if any(_progresan(va, vb, d) for va, _ in a[lado] for vb, _ in b[lado]):
                    votos[lado] += 1
    return max(votos, key=votos.get), votos


def _progresan(va, vb, distancia):
    """Misma regla para elegir lado y aceptar un número: una o dos páginas por hoja."""
    return distancia in (1, 2) and vb - va in (distancia, 2 * distancia)


def _cuadra(filas, i, lado, valor):
    """Basta una vecina a +/-1 o +/-2 hojas que avance exactamente; no extrapola.

    El apoyo puede estar a un solo lado (inicio/final de capítulo); distancia 2 conserva
    pares sin folio y una lámina/blanco. Fuera de esta ventana se abstiene, aunque el número
    sea un folio real. No certifica la imagen ni recupera dígitos que falten en el OCR.
    """
    actual = filas[i]['pagina_pdf']
    for vecina in filas[max(0, i-2):i] + filas[i+1:i+3]:
        d = vecina['pagina_pdf'] - actual
        for v, _ in vecina[lado]:
            if (_progresan(valor, v, d) if d > 0 else _progresan(v, valor, -d)):
                return True
    return False


def _diferencias_dominantes(filas, i):
    """Modelos locales (paso, diferencia) sustentados por otras hojas, no por la actual.

    Ventana de +/-15 hojas PDF; cada hoja vota una sola vez por modelo aunque el
    número esté repetido en ambas franjas. Se exige un ganador sin empate y al
    menos cuatro hojas de apoyo. Mirar también cada semiventana conserva cambios
    de paginación: el tramo anterior no puede vetar el comienzo del siguiente.
    El paso dos exige pares consecutivos repartidos entre las mitades de una hoja
    apaisada. No convierte dos números de nota en un escaneo doble.
    """
    actual = filas[i]['pagina_pdf']
    ventana = [f for f in filas[max(0, i - 15):i + 16]
               if 0 < abs(f['pagina_pdf'] - actual) <= 15]
    doble = any(_par_doble(f, f[lado]) is not None
                for f in ventana + [filas[i]] for lado in ('arriba', 'abajo'))
    pasos = (2,) if doble else (1,)
    modelos = {}
    for grupo in (ventana, [f for f in ventana if f['pagina_pdf'] < actual],
                  [f for f in ventana if f['pagina_pdf'] > actual]):
        votos = defaultdict(set)
        for f in grupo:
            for lado in ('arriba', 'abajo'):
                par = _par_doble(f, f[lado])
                valores = [par[0]] if par is not None else [v for v, _ in f[lado]]
                for paso in pasos:
                    for valor in valores:
                        votos[(paso, valor - paso * f['pagina_pdf'])].add(f['pagina_pdf'])
        orden = sorted(votos, key=lambda k: len(votos[k]), reverse=True)
        if orden and len(votos[orden[0]]) >= 4 \
                and (len(orden) == 1 or len(votos[orden[0]]) > len(votos[orden[1]])):
            modelo = orden[0]
            modelos[modelo] = max(modelos.get(modelo, 0), len(votos[modelo]))
    return modelos


def _par_doble(fila, nums):
    if not fila['apaisada']:
        return None
    izq = [v for v, x in nums if x < fila['ancho'] / 2]
    der = [v for v, x in nums if x >= fila['ancho'] / 2]
    if len(izq) == len(der) == 1 and der[0] == izq[0] + 1:
        return [izq[0], der[0]]
    return None


def _numeros_con_apoyo(filas, i, lado):
    """Devuelve números leídos compatibles con un modelo y su franja de origen.

    Un documento de hasta cinco hojas no ofrece cuatro testigos independientes:
    mantiene el contrato local anterior. En documentos largos la falta de modelo
    produce abstención, aunque una pareja de notas avance como la paginación.
    """
    f = filas[i]
    if len(filas) <= 5:
        return f[lado], lado, False
    modelos = _diferencias_dominantes(filas, i)
    aceptados = []
    for franja in (lado, 'abajo' if lado == 'arriba' else 'arriba'):
        par = _par_doble(f, f[franja])
        nums = []
        for valor, x in f[franja]:
            if any(valor - paso * f['pagina_pdf'] in
                   ((diferencia, diferencia + 1) if paso == 2 else (diferencia,))
                   for paso, diferencia in modelos):
                nums.append((valor, x))
        if par is not None and len(nums) != 2:
            nums = []
        if nums:
            aceptados.append((nums, franja))
    if not aceptados:
        return [], lado, True
    # Dos franjas que dicen lo mismo no crean ambigüedad artificial.
    valores = {tuple(v for v, _ in nums) for nums, _ in aceptados}
    if len(valores) == 1:
        return *aceptados[0], True
    return [n for nums, _ in aceptados for n in nums], lado, True


def candidatos(filas):
    capa = diagnostico_de_capa(filas)
    apaisadas = sum(1 for f in filas if f['apaisada'])
    if capa != 'ok':
        # No se proponen folios: cualquier número saldría de una geometría que no es la de la
        # imagen. Mejor ninguno que uno plausible y falso.
        return dict(capa=capa, lado=None, votos={}, doble_pagina=apaisadas > len(filas) / 2,
                    paginas=[dict(pagina_pdf=f['pagina_pdf'], folio_candidato=None,
                                  origen=capa, aviso='leer el folio en la imagen')
                             for f in filas])
    lado, votos = posicion_del_folio(filas)
    doble = False
    salida = []
    for i, f in enumerate(filas):
        if f.get('paginacion_del_fichero'):
            salida.append(dict(pagina_pdf=f['pagina_pdf'], folio_candidato=None,
                               origen='paginacion_del_fichero',
                               vistos=f['paginacion_del_fichero'],
                               lado=lado,
                               aviso='número de la paginación del fichero; leer el folio de la publicación en la imagen'))
            continue
        nums, franja, amplio = _numeros_con_apoyo(filas, i, lado)
        entrada = dict(pagina_pdf=f['pagina_pdf'], folio_candidato=None, origen=None,
                       lado=franja, aviso='candidato de la capa de texto; leer en la imagen')
        if len(nums) >= 2:
            izq = [v for v, x in nums if x < f['ancho'] / 2]
            der = [v for v, x in nums if x >= f['ancho'] / 2]
            if f['apaisada'] and len(izq) == 1 and len(der) == 1 and der[0] == izq[0] + 1:
                doble = True
                entrada.update(folio_candidato=[izq[0], der[0]], origen='doble_pagina')
            else:
                entrada.update(origen='ambiguo', vistos=[v for v, _ in nums])
        elif len(nums) == 1:
            valor = nums[0][0]
            if amplio or _cuadra(filas, i, franja, valor):
                entrada.update(folio_candidato=valor, origen='impreso')
            else:
                entrada.update(origen='no_cuadra', vistos=[valor],
                               aviso='número descartado: no progresa con vecinas a 1 o 2 hojas; leer en imagen')
        elif amplio:
            vistos = [v for v, _ in f[lado]]
            if not vistos:
                vistos = [v for v, _ in f['abajo' if lado == 'arriba' else 'arriba']]
            if vistos:
                entrada.update(origen='no_cuadra', vistos=vistos,
                               aviso='número descartado: sin diferencia dominante en otras hojas a +/-15; leer en imagen')
        salida.append(entrada)
    _deducir_huecos(salida)
    _marcar_saltos(salida, doble)
    return dict(capa=capa, lado=lado, votos=votos, doble_pagina=doble, paginas=salida)


def _deducir_huecos(salida):
    """Una página sin folio entre dos que cuadran recibe el número que falta."""
    for i in range(1, len(salida) - 1):
        a, b, c = salida[i - 1], salida[i], salida[i + 1]
        if b['folio_candidato'] is None and b['origen'] != 'paginacion_del_fichero' \
                and isinstance(a['folio_candidato'], int) \
                and isinstance(c['folio_candidato'], int) \
                and b['pagina_pdf'] - a['pagina_pdf'] == c['pagina_pdf'] - b['pagina_pdf'] == 1 \
                and c['folio_candidato'] - a['folio_candidato'] == 2:
            descartado = b['origen'] == 'no_cuadra'
            b.update(folio_candidato=a['folio_candidato'] + 1,
                     origen='deducido_tras_descarte' if descartado else 'deducido')
            if descartado:
                b['aviso'] = 'número visto descartado; candidato deducido de ambas vecinas; leer en imagen'


def _marcar_saltos(salida, doble):
    """Un folio que no sigue a su vecino se marca: cifra mal leída, lámina o página perdida."""
    paso = 2 if doble else 1
    previo = None   # (pagina_pdf, primer folio, último folio)
    for e in salida:
        v = e['folio_candidato']
        if v is None:
            continue
        primero, ultimo = (v[0], v[1]) if isinstance(v, list) else (v, v)
        if previo is not None:
            esperado = previo[2] + 1 + paso * (e['pagina_pdf'] - previo[0] - 1)
            if doble and (primero == ultimo or previo[1] == previo[2]):
                # un solo folio legible en una hoja doble: no se sabe de qué mitad es
                bien = primero in (esperado, esperado + 1) or primero + 1 == esperado
            else:
                bien = primero == esperado
            if not bien:
                e['salto'] = f'tras {previo[2]} (PDF {previo[0]})'
        previo = (e['pagina_pdf'], primero, ultimo)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('pdf')
    ap.add_argument('--desde', type=int, default=1)
    ap.add_argument('--hasta', type=int)
    ap.add_argument('--json')
    a = ap.parse_args(argv)
    res = candidatos(leer(a.pdf, a.desde, a.hasta))
    res['pdf'] = str(Path(a.pdf).name)
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    if res['capa'] != 'ok':
        print(f"SIN FOLIOS: el PDF no tiene capa de texto. Doble página: {res['doble_pagina']}. Leer cada folio en la imagen.")
        return 2
    print(f"folio {res['lado']} (votos {res['votos']}), doble página: {res['doble_pagina']}")
    for e in res['paginas']:
        extra = f"  SALTO {e['salto']}" if 'salto' in e else ''
        print(f"PDF {e['pagina_pdf']:>4}  folio {str(e['folio_candidato']):>10}  {e['origen'] or 'sin folio'}{extra}")


if __name__ == '__main__':
    sys.exit(main())
