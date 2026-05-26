"""
runner_ewud.py  –  Decomposizione East-West / Up-Down da dati InSAR
Usa qgis:joinbylocationsummary (mean) invece di native:intersection +
statisticsbycategories: meno step, stesso risultato, molto più veloce.
Indici spaziali creati prima di ogni join per massima performance.
"""

import traceback, math
from qgis.PyQt.QtCore import QThread, pyqtSignal
from qgis.core import (
    QgsProcessingContext, QgsProcessingFeedback,
    QgsVectorLayer
)
import processing
def _f(v):
    """Formatta un float con il punto decimale, indipendentemente dalla locale."""
    return repr(float(v))


PI = math.pi


class EwudRunner(QThread):
    progress = pyqtSignal(int)
    log      = pyqtSignal(str)
    finished = pyqtSignal(dict, list)
    error    = pyqtSignal(str)

    def __init__(self, params, parent=None):
        super().__init__(parent)
        self.params = params

    def _info(self, msg):
        self.log.emit(f'<span style="color:#aed6f1">&nbsp;&nbsp;{msg}</span>')

    def _step(self, msg, pct):
        self.log.emit(f'<span style="color:#5dade2">→ {msg}</span>')
        self.progress.emit(pct)

    def _run(self, alg, params, ctx, feedback):
        r = processing.run(alg, params,
                           context=ctx, feedback=feedback,
                           is_child_algorithm=False)
        return r['OUTPUT']

    def _index(self, layer, ctx, feedback):
        processing.run('native:createspatialindex', {'INPUT': layer},
                       context=ctx, feedback=feedback,
                       is_child_algorithm=False)

    def run(self):
        try:
            ctx      = QgsProcessingContext()
            feedback = _Feedback(self.progress, self.log, total_steps=18)
            p        = self.params

            griglia    = p['griglia_ricamp']
            id_griglia = p['id_griglia']
            vel_asc    = p['vel_asc']
            vel_desc   = p['vel_desc']
            theta_asc  = p['offnadir_asc']
            theta_desc = p['offnadir_desc']
            alpha_asc  = p['azimut_asc']
            alpha_desc = p['azimut_desc']
            # ── Coefficienti geometrici ───────────────────────────────────────
            # Convenzione EGMS: positivo = avvicinamento al satellite
            # ASC vola verso NNO (es. 349°), guarda a destra verso ENE:
            #   Va>0 (avvicinamento da ENE) → il bersaglio si muove verso OSO (Ovest)
            # DESC vola verso SSO (es. 191°), guarda a destra verso ONO:
            #   Vd<0 (allontanamento verso ONO) → il bersaglio si muove verso OSO (Ovest)
            # sgn=-1 produce Ca_e<0 e Cd_e>0, che con la formula EWUD
            # restituisce E<0 per movimento verso Ovest: corretto.
            feedback.next_step('Pre-calcolo coefficienti geometrici…')
            sgn  = -1.0  # right-looking: positivo=avvicinamento → inversione componente Est
            Ca_e = sgn * math.sin(theta_asc  * PI/180) * math.cos(alpha_asc  * PI/180)
            Ca_u =       math.cos(theta_asc  * PI/180)
            Cd_e = sgn * math.sin(theta_desc * PI/180) * math.cos(alpha_desc * PI/180)
            Cd_u =       math.cos(theta_desc * PI/180)
            A        = Cd_e**2/Cd_u + Cd_u
            B        = Ca_e**2/Ca_u + Ca_u
            D        = Cd_e/Cd_u - Ca_e/Ca_u
            Ce_ratio = Ca_e / Ca_u
            feedback.pushInfo(f'Ca_e={_f(Ca_e)}  Ca_u={_f(Ca_u)}')
            feedback.pushInfo(f'Cd_e={_f(Cd_e)}  Cd_u={_f(Cd_u)}')
            feedback.pushInfo(f'A={_f(A)}  B={_f(B)}  D={_f(D)}')

            # ── Allineamento CRS PS → griglia (con ritaglio preventivo) ──────
            from qgis.core import QgsCoordinateReferenceSystem, QgsProject, QgsCoordinateTransform
            grid_crs  = griglia.crs()
            grid_ext  = griglia.extent()
            ps_asc_w  = p['ps_asc']
            ps_desc_w = p['ps_desc']

            def _align_ps(ps_layer, label):
                """Ritaglia all'area della griglia e riproietta se necessario."""
                ps_crs = ps_layer.crs()
                # Converti l'estensione della griglia nel CRS del layer PS
                xform = QgsCoordinateTransform(grid_crs, ps_crs, QgsProject.instance())
                ext_in_ps_crs = xform.transformBoundingBox(grid_ext)
                # Ritaglia al solo subset nell'area di lavoro
                feedback.pushInfo(f'Ritaglio {label} all\'area della griglia…')
                r = processing.run('native:extractbyextent', {
                    'INPUT':  ps_layer,
                    'EXTENT': ext_in_ps_crs,
                    'CLIP':   False,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                }, context=ctx, feedback=feedback, is_child_algorithm=False)
                clipped = r['OUTPUT']
                # Riproietta se CRS diverso
                if ps_crs != grid_crs:
                    feedback.pushInfo(
                        f'Riproiezione {label} da {ps_crs.authid()} '
                        f'a {grid_crs.authid()}…')
                    r = processing.run('native:reprojectlayer', {
                        'INPUT': clipped, 'TARGET_CRS': grid_crs,
                        'OUTPUT': 'TEMPORARY_OUTPUT',
                    }, context=ctx, feedback=feedback, is_child_algorithm=False)
                    return r['OUTPUT']
                return clipped

            ps_asc_w  = _align_ps(p['ps_asc'],  'PS ascending')
            ps_desc_w = _align_ps(p['ps_desc'], 'PS descending')

            # ── Indici spaziali ───────────────────────────────────────────────
            feedback.next_step('Indici spaziali su PS e griglia…')
            self._index(ps_asc_w,  ctx, feedback)
            self._index(ps_desc_w, ctx, feedback)
            self._index(griglia,   ctx, feedback)

            # ══════════════════════════════════════════════════════════════════
            # FASE 1 — Media Va e Vd per cella con joinbylocationsummary
            #   Questo algoritmo è molto più veloce di intersection +
            #   statisticsbycategories perché usa direttamente l'indice spaziale
            #   e calcola la media in un'unica passata.
            # ══════════════════════════════════════════════════════════════════

            # ── FASE 1: join spaziale con summaries separati ─────────────────
            # Usa due chiamate separate (solo mean, solo count) per evitare
            # ambiguità nell'ordine dei campi prodotti da joinbylocationsummary
            feedback.next_step('Calcolo Va per cella (media ascending)…')
            tmp_va = self._run('qgis:joinbylocationsummary', {
                'INPUT':           griglia,
                'JOIN':            ps_asc_w,
                'JOIN_FIELDS':     [vel_asc],
                'PREDICATE':       [0],
                'SUMMARIES':       [6],          # solo mean
                'DISCARD_NOMATCH': False,
                'OUTPUT':          'TEMPORARY_OUTPUT',
            }, ctx, feedback)
            # Il campo prodotto è l'unico campo aggiunto → nessuna ambiguità
            _fields_grid = set(f.name() for f in griglia.fields())
            va_field = next(f.name() for f in tmp_va.fields()
                            if f.name() not in _fields_grid)
            feedback.pushInfo(f'Campo Va rilevato: {va_field}')
            tmp_va = self._run('native:renametablefield', {
                'INPUT': tmp_va, 'FIELD': va_field,
                'NEW_NAME': 'Va', 'OUTPUT': 'TEMPORARY_OUTPUT',
            }, ctx, feedback)

            feedback.next_step('Calcolo Na per cella (conteggio ascending)…')
            tmp_na = self._run('qgis:joinbylocationsummary', {
                'INPUT':           griglia,
                'JOIN':            ps_asc_w,
                'JOIN_FIELDS':     [vel_asc],
                'PREDICATE':       [0],
                'SUMMARIES':       [0],          # solo count
                'DISCARD_NOMATCH': False,
                'OUTPUT':          'TEMPORARY_OUTPUT',
            }, ctx, feedback)
            na_field = next(f.name() for f in tmp_na.fields()
                            if f.name() not in _fields_grid)
            feedback.pushInfo(f'Campo Na rilevato: {na_field}')
            tmp_na = self._run('native:renametablefield', {
                'INPUT': tmp_na, 'FIELD': na_field,
                'NEW_NAME': 'Na', 'OUTPUT': 'TEMPORARY_OUTPUT',
            }, ctx, feedback)

            feedback.next_step('Calcolo Vd per cella (media descending)…')
            tmp_vd = self._run('qgis:joinbylocationsummary', {
                'INPUT':           griglia,
                'JOIN':            ps_desc_w,
                'JOIN_FIELDS':     [vel_desc],
                'PREDICATE':       [0],
                'SUMMARIES':       [6],          # solo mean
                'DISCARD_NOMATCH': False,
                'OUTPUT':          'TEMPORARY_OUTPUT',
            }, ctx, feedback)
            vd_field = next(f.name() for f in tmp_vd.fields()
                            if f.name() not in _fields_grid)
            feedback.pushInfo(f'Campo Vd rilevato: {vd_field}')
            tmp_vd = self._run('native:renametablefield', {
                'INPUT': tmp_vd, 'FIELD': vd_field,
                'NEW_NAME': 'Vd', 'OUTPUT': 'TEMPORARY_OUTPUT',
            }, ctx, feedback)

            feedback.next_step('Calcolo Nd per cella (conteggio descending)…')
            tmp_nd = self._run('qgis:joinbylocationsummary', {
                'INPUT':           griglia,
                'JOIN':            ps_desc_w,
                'JOIN_FIELDS':     [vel_desc],
                'PREDICATE':       [0],
                'SUMMARIES':       [0],          # solo count
                'DISCARD_NOMATCH': False,
                'OUTPUT':          'TEMPORARY_OUTPUT',
            }, ctx, feedback)
            nd_field = next(f.name() for f in tmp_nd.fields()
                            if f.name() not in _fields_grid)
            feedback.pushInfo(f'Campo Nd rilevato: {nd_field}')
            tmp_nd = self._run('native:renametablefield', {
                'INPUT': tmp_nd, 'FIELD': nd_field,
                'NEW_NAME': 'Nd', 'OUTPUT': 'TEMPORARY_OUTPUT',
            }, ctx, feedback)

            # ── Join Va + Na + Vd + Nd sulla griglia ─────────────────────
            feedback.next_step('Join Va, Na, Vd, Nd per cella…')
            # Join Va + Na
            tmp = self._run('native:joinattributestable', {
                'INPUT':              tmp_va,
                'INPUT_2':            tmp_na,
                'FIELD':              id_griglia,
                'FIELD_2':            id_griglia,
                'FIELDS_TO_COPY':     ['Na'],
                'METHOD':             1,
                'DISCARD_NONMATCHING': False,
                'PREFIX':             '',
                'OUTPUT':             'TEMPORARY_OUTPUT',
            }, ctx, feedback)
            # Join + Vd
            tmp = self._run('native:joinattributestable', {
                'INPUT':              tmp,
                'INPUT_2':            tmp_vd,
                'FIELD':              id_griglia,
                'FIELD_2':            id_griglia,
                'FIELDS_TO_COPY':     ['Vd'],
                'METHOD':             1,
                'DISCARD_NONMATCHING': False,
                'PREFIX':             '',
                'OUTPUT':             'TEMPORARY_OUTPUT',
            }, ctx, feedback)
            # Join + Nd
            tmp = self._run('native:joinattributestable', {
                'INPUT':              tmp,
                'INPUT_2':            tmp_nd,
                'FIELD':              id_griglia,
                'FIELD_2':            id_griglia,
                'FIELDS_TO_COPY':     ['Nd'],
                'METHOD':             1,
                'DISCARD_NONMATCHING': False,
                'PREFIX':             '',
                'OUTPUT':             'TEMPORARY_OUTPUT',
            }, ctx, feedback)
            # Rinomina id_griglia in ID_griglia
            tmp = self._run('native:renametablefield', {
                'INPUT': tmp, 'FIELD': id_griglia,
                'NEW_NAME': 'ID_griglia', 'OUTPUT': 'TEMPORARY_OUTPUT',
            }, ctx, feedback)

            # Tieni solo i campi necessari
            feedback.next_step('Selezione campi ID, Va, Vd, Na, Nd…')
            prev = self._run('native:retainfields', {
                'INPUT':  tmp,
                'FIELDS': ['ID_griglia', 'Va', 'Vd', 'Na', 'Nd'],
                'OUTPUT': 'TEMPORARY_OUTPUT',
            }, ctx, feedback)

            # ══════════════════════════════════════════════════════════════════
            # FASE 2 — Calcolo E, U, vel, ang2, vprev
            # ══════════════════════════════════════════════════════════════════

            feedback.next_step('Calcolo E (East-West)…')
            prev = self._run('native:fieldcalculator', {
                'INPUT': prev, 'OUTPUT': 'TEMPORARY_OUTPUT',
                'FIELD_NAME': 'E', 'FIELD_TYPE': 0,
                'FIELD_LENGTH': 10, 'FIELD_PRECISION': 1,
                'FORMULA': (
                    f'CASE\n'
                    f'WHEN Va IS NOT NULL AND Vd IS NOT NULL\n'
                    f'THEN round(({_f(A)}*Vd - {_f(B)}*Va) / {_f(D)}, 1)\n'
                    f'WHEN Va IS NULL AND Vd IS NOT NULL\n'
                    f'THEN round({_f(Cd_e)}*Vd, 1)\n'
                    f'WHEN Va IS NOT NULL AND Vd IS NULL\n'
                    f'THEN round({_f(Ca_e)}*Va, 1)\n'
                    f'ELSE 9999\n'
                    f'END'
                ),
            }, ctx, feedback)

            feedback.next_step('Calcolo U (Up-Down)…')
            prev = self._run('native:fieldcalculator', {
                'INPUT': prev, 'OUTPUT': 'TEMPORARY_OUTPUT',
                'FIELD_NAME': 'U', 'FIELD_TYPE': 0,
                'FIELD_LENGTH': 10, 'FIELD_PRECISION': 1,
                'FORMULA': (
                    f'CASE\n'
                    f'WHEN Va IS NOT NULL AND Vd IS NOT NULL\n'
                    f'THEN round({_f(-Ce_ratio)}*E + {_f(B)}*Va, 1)\n'
                    f'WHEN Va IS NULL AND Vd IS NOT NULL\n'
                    f'THEN round({_f(Cd_u)}*Vd, 1)\n'
                    f'WHEN Va IS NOT NULL AND Vd IS NULL\n'
                    f'THEN round({_f(Ca_u)}*Va, 1)\n'
                    f'ELSE 9999\n'
                    f'END'
                ),
            }, ctx, feedback)

            feedback.next_step('Calcolo vel, ang2, vprev…')
            prev = self._run('native:fieldcalculator', {
                'INPUT': prev, 'OUTPUT': 'TEMPORARY_OUTPUT',
                'FIELD_NAME': 'vel', 'FIELD_TYPE': 0,
                'FIELD_LENGTH': 10, 'FIELD_PRECISION': 1,
                'FORMULA': 'round(sqrt(E*E + U*U), 1)',
            }, ctx, feedback)

            prev = self._run('native:fieldcalculator', {
                'INPUT': prev, 'OUTPUT': 'TEMPORARY_OUTPUT',
                'FIELD_NAME': 'ang2', 'FIELD_TYPE': 1,
                'FIELD_LENGTH': 20, 'FIELD_PRECISION': 10,
                'FORMULA': (
                    f'CASE\n'
                    f'WHEN E=0 AND U=0 THEN 9999\n'
                    f'WHEN atan2(E,U)*180/{PI} >= 0 THEN atan2(E,U)*180/{PI}\n'
                    f'ELSE atan2(E,U)*180/{PI} + 360\n'
                    f'END'
                ),
            }, ctx, feedback)

            out_param = p.get('Parametri_def', 'TEMPORARY_OUTPUT')
            if out_param == 'TEMPORARY_OUTPUT':
                out_param = 'memory:'
            prev = self._run('native:fieldcalculator', {
                'INPUT': prev, 'OUTPUT': out_param,
                'FIELD_NAME': 'vprev', 'FIELD_TYPE': 2,
                'FIELD_LENGTH': 0, 'FIELD_PRECISION': 0,
                'FORMULA': (
                    "CASE\n"
                    "WHEN E = 9999 THEN 'NO_DATA'\n"
                    "WHEN (Va >= -2 AND Va <= 2 AND Vd >= -2 AND Vd <= 2) OR vel <= 2 THEN 'STABLE'\n"
                    "WHEN (ang2 >= 0 AND ang2 <= 45) OR ang2 > 315 THEN 'UP'\n"
                    "WHEN  ang2 >  45 AND ang2 <= 135 THEN 'EST'\n"
                    "WHEN  ang2 > 135 AND ang2 <= 225 THEN 'DOWN'\n"
                    "WHEN  ang2 > 225 AND ang2 <= 315 THEN 'WEST'\n"
                    "ELSE 'NO_DATA'\n"
                    "END"
                ),
            }, ctx, feedback)
            param_def = prev

            # ── Poligoni_EWUD: griglia + Va, Vd, E, U, vel, ang2, vprev ─────
            feedback.next_step('Join EWUD su griglia (Poligoni_EWUD)…')
            out_poly = p.get('Poligoni_ewud', 'TEMPORARY_OUTPUT')
            if out_poly == 'TEMPORARY_OUTPUT':
                out_poly = 'memory:'
            poly = self._run('native:joinattributestable', {
                'INPUT':               griglia,
                'INPUT_2':             param_def,
                'FIELD':               id_griglia,
                'FIELD_2':             'ID_griglia',
                'FIELDS_TO_COPY':      ['Va', 'Vd', 'Na', 'Nd', 'E', 'U', 'vel', 'ang2', 'vprev'],
                'METHOD':              1,
                'DISCARD_NONMATCHING': True,
                'PREFIX':              '',
                'OUTPUT':              out_poly,
            }, ctx, feedback)

            # ── Centroidi_EWUD ────────────────────────────────────────────────
            feedback.next_step('Calcolo centroidi (Centroidi_EWUD)…')
            out_centr = p.get('Centroidi_ewud', 'TEMPORARY_OUTPUT')
            if out_centr == 'TEMPORARY_OUTPUT':
                out_centr = 'memory:'
            centr = self._run('native:centroids', {
                'ALL_PARTS': True,
                'INPUT':  poly,
                'OUTPUT': out_centr,
            }, ctx, feedback)

            def _to_layer(val, name):
                if isinstance(val, QgsVectorLayer):
                    val.setName(name); return val
                elif isinstance(val, str) and val:
                    lyr = QgsVectorLayer(val, name, 'ogr')
                    return lyr if lyr.isValid() else None
                return None

            results = {
                'Poligoni_ewud':  poly,
                'Centroidi_ewud': centr,
            }
            layers = [
                ('Centroidi_EWUD', _to_layer(centr, 'Centroidi_EWUD')),
                ('Poligoni_EWUD',  _to_layer(poly,  'Poligoni_EWUD')),
            ]
            self.finished.emit(results, layers)

        except Exception:
            self.error.emit(traceback.format_exc())


class _Feedback(QgsProcessingFeedback):
    def __init__(self, progress_signal, log_signal, total_steps):
        super().__init__()
        self._prog  = progress_signal
        self._log   = log_signal
        self._total = total_steps
        self._step  = 0
        self._base  = 0

    def next_step(self, label):
        self._step += 1
        self._base  = int((self._step - 1) / self._total * 100)
        self._log.emit(f'<span style="color:#5dade2">→ {label}</span>')
        self._prog.emit(self._base)

    def setProgress(self, p):
        self._prog.emit(min(self._base + int(p / self._total), 99))

    def pushInfo(self, info):
        self._log.emit(f'<span style="color:#aed6f1">&nbsp;&nbsp;{info}</span>')

    def pushWarning(self, w):
        self._log.emit(f'<span style="color:#f39c12">&nbsp;&nbsp;⚠ {w}</span>')

    def reportError(self, err, fatal=False):
        self._log.emit(f'<span style="color:#e74c3c">&nbsp;&nbsp;✖ {err}</span>')
