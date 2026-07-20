"""
Runner per l'algoritmo Crea Griglia PS.
Esegue i step in un QThread separato per non bloccare la GUI.
Emette progress (0-100), log (HTML), finished (result, QgsVectorLayer).

Gestione CRS:
  Se i layer PS sono in coordinate geografiche (es. EPSG:4326) e il progetto
  è in coordinate piane (es. EPSG:3003), il plugin:
    1. Ritaglia i PS all'estensione del canvas (subset piccolo)
    2. Riproietta il subset nel CRS del progetto
    3. Crea la griglia nel CRS del progetto (celle quadrate e allineate N-S/E-W)
  Se il progetto è già geografico, converte il lato cella in gradi
  (HSPACING e VSPACING distinti) senza nessuna riproiezione.
"""

import traceback, math
from qgis.PyQt.QtCore import QThread, pyqtSignal
from qgis.core import (
    QgsProcessingContext, QgsProcessingFeedback,
    QgsVectorLayer, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsProject,
    QgsVectorFileWriter, QgsCoordinateTransformContext
)
import processing


def _is_geographic(layer):
    return layer.crs().isGeographic()


def _reproject(layer, target_crs, ctx, feedback):
    r = processing.run('native:reprojectlayer', {
        'INPUT':      layer,
        'TARGET_CRS': target_crs,
        'OUTPUT':     'TEMPORARY_OUTPUT',
    }, context=ctx, feedback=feedback, is_child_algorithm=False)
    return r['OUTPUT']


def _clip_to_extent(layer, extent_geog, ctx, feedback):
    """Ritaglia il layer all'estensione geografica (in gradi) per ridurre i dati."""
    r = processing.run('native:extractbyextent', {
        'INPUT':   layer,
        'EXTENT':  extent_geog,
        'CLIP':    False,
        'OUTPUT':  'TEMPORARY_OUTPUT',
    }, context=ctx, feedback=feedback, is_child_algorithm=False)
    return r['OUTPUT']


def _meters_to_degrees(cell_m, extent):
    """Converte lato cella da metri a gradi; restituisce (hspacing, vspacing, lat_center)."""
    lat_center = (extent.yMinimum() + extent.yMaximum()) / 2.0
    vspacing   = cell_m / 111320.0
    hspacing   = cell_m / (111320.0 * math.cos(math.radians(lat_center)))
    return hspacing, vspacing, lat_center


class GridRunner(QThread):
    progress = pyqtSignal(int)
    log      = pyqtSignal(str)
    finished = pyqtSignal(dict, object)
    error    = pyqtSignal(str)

    def __init__(self, params, callback_layer=None, parent=None):
        super().__init__(parent)
        self.params         = params
        self.callback_layer = callback_layer

    def _info(self, feedback, msg):
        feedback._log.emit(f'<span style="color:#aed6f1">&nbsp;&nbsp;{msg}</span>')

    def _warn(self, feedback, msg):
        feedback._log.emit(f'<span style="color:#f39c12">&nbsp;&nbsp;{msg}</span>')

    def run(self):
        try:
            ctx      = QgsProcessingContext()
            feedback = _Feedback(self.progress, self.log, total_steps=6)
            outputs  = {}

            cell_m     = self.params['lato_cella']
            tipo_cella = self.params.get('tipo_cella', 'square')
            # TYPE: 2=Rettangolo, 4=Esagono piatto (flat-top), 5=Esagono puntato (pointy-top)
            grid_type  = 4 if tipo_cella == 'hex' else 2
            ps_asc   = self.params['ps_ascendenti']
            ps_desc  = self.params['ps_discendenti']
            ps_crs   = ps_asc.crs()
            proj_crs = QgsProject.instance().crs()

            ps_geo   = ps_crs.isGeographic()
            proj_geo = proj_crs.isGeographic()

            # ── Step 1 – Analisi CRS e preparazione estensione ────────────────
            feedback.next_step('Analisi CRS e preparazione estensione…')

            # L'estensione dal widget è sempre nel CRS del progetto
            extent_proj = self.params['estensione_griglia']

            if ps_geo and not proj_geo:
                # ══ CASO PRINCIPALE: PS geografici, progetto metrico ══════════
                # Converte l'estensione del progetto in gradi per ritagliare i PS
                self._warn(feedback,
                    f'PS in {ps_crs.authid()} (geografico), '
                    f'progetto in {proj_crs.authid()} (metrico): '
                    f'ritaglio e riproiezione subset PS.')

                xform_to_geo = QgsCoordinateTransform(
                    proj_crs, ps_crs, QgsProject.instance())
                extent_geo = xform_to_geo.transformBoundingBox(extent_proj)

                self._info(feedback,
                    f'Estensione in gradi: '
                    f'{extent_geo.xMinimum():.4f},{extent_geo.yMinimum():.4f} - '
                    f'{extent_geo.xMaximum():.4f},{extent_geo.yMaximum():.4f}')

                # Step 1b: ritaglia PS all'estensione geografica
                self._info(feedback, 'Ritaglio PS all\'estensione del canvas…')
                ps_asc_clip  = _clip_to_extent(ps_asc,  extent_geo, ctx, feedback)
                ps_desc_clip = _clip_to_extent(ps_desc, extent_geo, ctx, feedback)

                # Step 1c: riproietta il subset nel CRS del progetto
                self._info(feedback,
                    f'Riproiezione subset PS in {proj_crs.authid()}…')
                ps_asc_w  = _reproject(ps_asc_clip,  proj_crs, ctx, feedback)
                ps_desc_w = _reproject(ps_desc_clip, proj_crs, ctx, feedback)

                extent_use   = extent_proj
                cell_use_h   = cell_m
                cell_use_v   = cell_m
                crs_use      = proj_crs

            elif ps_geo and proj_geo:
                # ══ PS geografici, progetto geografico ════════════════════════
                # Conversione lato cella in gradi, HSPACING ≠ VSPACING
                xform = QgsCoordinateTransform(
                    proj_crs, ps_crs, QgsProject.instance())
                extent_use = xform.transformBoundingBox(extent_proj)

                hsp, vsp, lat_c = _meters_to_degrees(cell_m, extent_use)
                self._warn(feedback,
                    f'CRS geografico ({ps_crs.authid()}): '
                    f'lato cella {cell_m} m → '
                    f'HSPACING={hsp:.6f}° VSPACING={vsp:.6f}° '
                    f'(lat. centrale {lat_c:.2f}°).')

                ps_asc_w   = ps_asc
                ps_desc_w  = ps_desc
                cell_use_h = hsp
                cell_use_v = vsp
                crs_use    = ps_crs

            else:
                # ══ PS metrici (o stesso CRS del progetto) ════════════════════
                # Se i PS hanno CRS diverso dal progetto, li riproiettiamo
                if ps_crs.authid() != proj_crs.authid():
                    self._warn(feedback,
                        f'PS in {ps_crs.authid()}, progetto in {proj_crs.authid()}: '
                        f'riproiezione PS nel CRS del progetto.')
                    ps_asc_w  = _reproject(ps_asc,  proj_crs, ctx, feedback)
                    ps_desc_w = _reproject(ps_desc, proj_crs, ctx, feedback)
                else:
                    ps_asc_w  = ps_asc
                    ps_desc_w = ps_desc
                extent_use = extent_proj
                cell_use_h = cell_m
                cell_use_v = cell_m
                crs_use    = proj_crs

            # ── Step 2 – Indici spaziali sui PS ───────────────────────────────
            feedback.next_step('Creazione indici spaziali PS…')
            processing.run('native:createspatialindex', {'INPUT': ps_asc_w},
                           context=ctx, feedback=feedback, is_child_algorithm=False)
            processing.run('native:createspatialindex', {'INPUT': ps_desc_w},
                           context=ctx, feedback=feedback, is_child_algorithm=False)

            # ── Step 3 – Crea griglia ─────────────────────────────────────────
            feedback.next_step('Creazione griglia…')
            r = processing.run('native:creategrid', {
                'CRS':      crs_use,
                'EXTENT':   extent_use,
                'HOVERLAY': 0, 'HSPACING': cell_use_h,
                'VOVERLAY': 0, 'VSPACING': cell_use_v,
                'TYPE':     grid_type,
                'OUTPUT':   'TEMPORARY_OUTPUT',
            }, context=ctx, feedback=feedback, is_child_algorithm=False)
            outputs['grid'] = r['OUTPUT']

            # ── Step 4 – Rimuovi campi inutili + indice spaziale griglia ──────
            feedback.next_step('Pulizia campi e indice spaziale griglia…')
            r = processing.run('native:deletecolumn', {
                'COLUMN': (['left', 'top', 'right', 'bottom', 'row_index', 'col_index']
                            if tipo_cella == 'square' else ['left', 'top', 'right', 'bottom']),
                'INPUT':  outputs['grid'],
                'OUTPUT': 'TEMPORARY_OUTPUT',
            }, context=ctx, feedback=feedback, is_child_algorithm=False)
            outputs['clean'] = r['OUTPUT']

            # Rinomina 'id' in 'grid_id': GDAL, scrivendo su GeoPackage, tende
            # a interpretare un campo chiamato "id" come candidato per la
            # chiave primaria interna (fid), il che può alterare l'ordine o
            # la corrispondenza dei valori dopo un salvataggio/ricarica.
            # Un nome non ambiguo evita il problema, mantenendo il campo come
            # attributo normale con i valori originali intatti.
            r = processing.run('native:renametablefield', {
                'INPUT':     outputs['clean'],
                'FIELD':     'id',
                'NEW_NAME':  'grid_id',
                'OUTPUT':    'TEMPORARY_OUTPUT',
            }, context=ctx, feedback=feedback, is_child_algorithm=False)
            outputs['clean'] = r['OUTPUT']

            import logging as _logging_r, os as _os_r, tempfile as _tempfile_r
            _diag_r = _logging_r.getLogger("InSAR_Suite.qt_compat")
            if not _diag_r.handlers:
                _base_r = _os_r.path.join(_tempfile_r.gettempdir(), "insar_suite_logs")
                _os_r.makedirs(_base_r, exist_ok=True)
                _h_r = _logging_r.FileHandler(_os_r.path.join(_base_r, "insar_suite_qt_compat.log"), encoding="utf-8")
                _h_r.setFormatter(_logging_r.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
                _diag_r.setLevel(_logging_r.DEBUG)
                _diag_r.addHandler(_h_r)
            _check_layer = outputs['clean'] if isinstance(outputs['clean'], QgsVectorLayer) else QgsVectorLayer(outputs['clean'], 'check', 'ogr')
            _diag_r.debug("[ewud-grid] dopo renametablefield: tipo restituito=%r | campi=%r",
                          type(outputs['clean']).__name__,
                          _check_layer.fields().names() if _check_layer.isValid() else 'LAYER NON VALIDO')

            processing.run('native:createspatialindex', {'INPUT': outputs['clean']},
                           context=ctx, feedback=feedback, is_child_algorithm=False)

            # ── Step 5 – Filtra celle con PS ascending ────────────────────────
            feedback.next_step('Filtro celle ascending…')
            r = processing.run('native:extractbylocation', {
                'INPUT':     outputs['clean'],
                'INTERSECT': ps_asc_w,
                'PREDICATE': [0],
                'OUTPUT':    'TEMPORARY_OUTPUT',
            }, context=ctx, feedback=feedback, is_child_algorithm=False)
            outputs['asc'] = r['OUTPUT']

            # ── Step 6 – Filtra celle con PS descending ───────────────────────
            feedback.next_step('Filtro celle descending…')
            out_path = self.params.get('Egms_grid', 'TEMPORARY_OUTPUT')
            r = processing.run('native:extractbylocation', {
                'INPUT':     outputs['asc'],
                'INTERSECT': ps_desc_w,
                'PREDICATE': [0],
                'OUTPUT':    'memory:',
            }, context=ctx, feedback=feedback, is_child_algorithm=False)
            outputs['final'] = r['OUTPUT']

            # ── Costruisci layer finale ───────────────────────────────────────
            final = outputs['final']
            # Nome del layer: se è stato salvato su file permanente, usa il
            # nome del file (senza estensione) così è distinguibile da altre
            # griglie salvate in run diversi; altrimenti nome generico.
            if out_path not in ('TEMPORARY_OUTPUT', 'memory:'):
                import os as _os_grid
                layer_display_name = _os_grid.path.splitext(_os_grid.path.basename(out_path))[0] or 'InSAR_Grid'
            else:
                layer_display_name = 'InSAR_Grid'
            if isinstance(final, QgsVectorLayer):
                grid_layer = final
                grid_layer.setName(layer_display_name)
            elif isinstance(final, str):
                grid_layer = QgsVectorLayer(final, layer_display_name, 'ogr')
            else:
                grid_layer = None

            # ── Salvataggio permanente (se richiesto) ───────────────────────
            # Fatto DOPO che grid_layer è già completo in memoria, non come
            # OUTPUT diretto della catena di elaborazione: scrivere il
            # GeoPackage dentro un algoritmo processing può, in presenza del
            # campo "id", corrompere silenziosamente gli attributi in output
            # (stesso problema già risolto per Centroidi_EWUD/Poligoni_EWUD).
            if out_path not in ('TEMPORARY_OUTPUT', 'memory:') and grid_layer is not None and grid_layer.isValid():
                opts = QgsVectorFileWriter.SaveVectorOptions()
                opts.driverName = 'GPKG'
                opts.fileEncoding = 'UTF-8'
                opts.actionOnExistingFile = QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
                opts.layerName = layer_display_name
                err, msg, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
                    grid_layer, out_path, QgsCoordinateTransformContext(), opts)
                if err == QgsVectorFileWriter.WriterError.NoError:
                    saved = QgsVectorLayer(out_path, layer_display_name, 'ogr')
                    if saved.isValid():
                        grid_layer = saved
                        import logging as _logging_g, os as _os_g, tempfile as _tempfile_g
                        _diag_g = _logging_g.getLogger("InSAR_Suite.qt_compat")
                        if not _diag_g.handlers:
                            _base_g = _os_g.path.join(_tempfile_g.gettempdir(), "insar_suite_logs")
                            _os_g.makedirs(_base_g, exist_ok=True)
                            _h_g = _logging_g.FileHandler(_os_g.path.join(_base_g, "insar_suite_qt_compat.log"), encoding="utf-8")
                            _h_g.setFormatter(_logging_g.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
                            _diag_g.setLevel(_logging_g.DEBUG)
                            _diag_g.addHandler(_h_g)
                        _diag_g.debug(
                            "[ewud-grid] griglia salvata e ricaricata: campi=%r | esempio grid_id prime 3 feature=%r",
                            saved.fields().names(),
                            [f['grid_id'] for f in list(saved.getFeatures())[:3]] if saved.fields().indexFromName('grid_id') >= 0 else 'CAMPO grid_id ASSENTE',
                        )
                    else:
                        self._info(feedback, "⚠ Salvataggio riuscito ma rilettura del file fallita; uso il layer in memoria.")
                else:
                    self._info(feedback, f"⚠ Salvataggio permanente della griglia fallito: {msg}")

            n = grid_layer.featureCount() if grid_layer and grid_layer.isValid() else 0
            self._info(feedback, f'Griglia completata: {n} celle valide.')

            self.finished.emit(outputs, grid_layer)

        except Exception:
            self.error.emit(traceback.format_exc())


# ══════════════════════════════════════════════════════════════════════════════
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
