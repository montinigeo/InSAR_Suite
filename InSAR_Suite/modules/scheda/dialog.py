# -*- coding: utf-8 -*-
"""
InSAR Scheda — dialogo principale.
Permette di definire l'area di studio (disegno a mano o file geografico),
selezionare i layer PS/VIS/EWUD, e avviare il calcolo della Scheda
Riepilogativa PSInSAR in background (QgsTask).
"""
import os

from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QPushButton, QRadioButton, QButtonGroup,
    QDoubleSpinBox, QTextEdit, QProgressBar, QFileDialog, QMessageBox,
    QToolButton
)
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsWkbTypes, QgsApplication, QgsGeometry
)
from qgis.gui import QgsMapLayerComboBox, QgsFieldComboBox

try:
    from qgis.core import QgsMapLayerProxyModel, QgsFieldProxyModel
except ImportError:
    from qgis.gui import QgsMapLayerProxyModel, QgsFieldProxyModel

from .maptool import PolygonDrawTool
from .runner import SchedaTask
from .results_dialog import SchedaResultsDialog


class TaskBridge(QObject):
    log_signal      = pyqtSignal(str)
    finished_signal = pyqtSignal(object)
    error_signal    = pyqtSignal(str)


def _layer_combo(layer_type, allow_empty=True):
    cb = QgsMapLayerComboBox()
    cb.setFilters(layer_type)
    cb.setAllowEmptyLayer(allow_empty)
    if allow_empty:
        cb.setCurrentIndex(0)
    cb.setStyleSheet(
        'QgsMapLayerComboBox, QComboBox { background-color:#ffffff; '
        'color:#2c3e50; border:1px solid #b0c4d8; border-radius:3px; '
        'padding:3px 6px; min-height:22px; }'
        'QComboBox::drop-down { border: none; }'
    )
    return cb


class SchedaDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.setWindowTitle('InSAR — Scheda Riepilogativa PSInSAR')
        self.setMinimumWidth(560)

        self.area_geom = None          # QgsGeometry, CRS progetto
        self.area_source_label = None
        self._draw_tool = None
        self._prev_map_tool = None

        self.task = None
        self.bridge = None

        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)

        # ── Area di studio ──────────────────────────────────────────────────
        grp_area = QGroupBox('Area di studio')
        v_area = QVBoxLayout(grp_area)

        radio_row = QHBoxLayout()
        self.rb_draw = QRadioButton('Disegna sulla mappa')
        self.rb_file = QRadioButton('Layer o file esistente')
        self.rb_draw.setChecked(True)
        self._area_group = QButtonGroup(self)
        self._area_group.addButton(self.rb_draw)
        self._area_group.addButton(self.rb_file)
        self.rb_draw.toggled.connect(self._on_area_mode_changed)
        radio_row.addWidget(self.rb_draw)
        radio_row.addWidget(self.rb_file)
        radio_row.addStretch()
        v_area.addLayout(radio_row)

        draw_row = QHBoxLayout()
        self.btn_draw = QPushButton('Disegna area sulla mappa…')
        self.btn_draw.clicked.connect(self._start_draw)
        draw_row.addWidget(self.btn_draw)
        v_area.addLayout(draw_row)

        layer_row = QHBoxLayout()
        self.cb_area_layer = _layer_combo(QgsMapLayerProxyModel.PolygonLayer)
        self.cb_area_layer.setEnabled(False)
        self.cb_area_layer.layerChanged.connect(self._on_area_layer_changed)
        self.btn_area_file = QToolButton()
        self.btn_area_file.setText('… da file')
        self.btn_area_file.setEnabled(False)
        self.btn_area_file.clicked.connect(self._browse_area_file)
        layer_row.addWidget(self.cb_area_layer, 3)
        layer_row.addWidget(self.btn_area_file, 1)
        v_area.addLayout(layer_row)

        self.lbl_area_status = QLabel('Nessuna area definita.')
        self.lbl_area_status.setStyleSheet('color:#7f8c8d; font-style:italic;')
        v_area.addWidget(self.lbl_area_status)

        root.addWidget(grp_area)

        # ── PS Ascendente / Discendente ─────────────────────────────────────
        grp_ps = QGroupBox('Layer PS')
        form_ps = QFormLayout(grp_ps)

        self.cb_ps_asc = _layer_combo(QgsMapLayerProxyModel.PointLayer)
        self.cb_ps_asc_field = QgsFieldComboBox()
        self.cb_ps_asc_field.setFilters(QgsFieldProxyModel.Numeric)
        self.cb_ps_asc.layerChanged.connect(self.cb_ps_asc_field.setLayer)
        row_asc = QHBoxLayout()
        row_asc.addWidget(self.cb_ps_asc, 2)
        row_asc.addWidget(self.cb_ps_asc_field, 1)
        form_ps.addRow('PS Ascendente + campo velocità:', row_asc)

        self.cb_ps_desc = _layer_combo(QgsMapLayerProxyModel.PointLayer)
        self.cb_ps_desc_field = QgsFieldComboBox()
        self.cb_ps_desc_field.setFilters(QgsFieldProxyModel.Numeric)
        self.cb_ps_desc.layerChanged.connect(self.cb_ps_desc_field.setLayer)
        row_desc = QHBoxLayout()
        row_desc.addWidget(self.cb_ps_desc, 2)
        row_desc.addWidget(self.cb_ps_desc_field, 1)
        form_ps.addRow('PS Discendente + campo velocità:', row_desc)

        root.addWidget(grp_ps)

        # ── VIS (opzionale) ─────────────────────────────────────────────────
        grp_vis = QGroupBox('Layer VIS (opzionale — deve avere il campo "pc_mov")')
        form_vis = QFormLayout(grp_vis)
        self.cb_vis_asc = _layer_combo(QgsMapLayerProxyModel.PointLayer, allow_empty=True)
        self.cb_vis_desc = _layer_combo(QgsMapLayerProxyModel.PointLayer, allow_empty=True)
        form_vis.addRow('VIS Ascendente:', self.cb_vis_asc)
        form_vis.addRow('VIS Discendente:', self.cb_vis_desc)
        root.addWidget(grp_vis)

        # ── EWUD (opzionale) ────────────────────────────────────────────────
        grp_ewud = QGroupBox('Layer EWUD (opzionale — Centroidi_EWUD o Poligoni_EWUD)')
        form_ewud = QFormLayout(grp_ewud)
        self.cb_ewud = _layer_combo(
            QgsMapLayerProxyModel.PointLayer | QgsMapLayerProxyModel.PolygonLayer,
            allow_empty=True)
        form_ewud.addRow('Layer EWUD:', self.cb_ewud)
        root.addWidget(grp_ewud)

        # ── Parametri ────────────────────────────────────────────────────────
        grp_par = QGroupBox('Parametri')
        form_par = QFormLayout(grp_par)
        self.sp_raggio = QDoubleSpinBox()
        self.sp_raggio.setRange(1, 5000)
        self.sp_raggio.setValue(50)
        self.sp_raggio.setSuffix(' m')
        form_par.addRow('Raggio buffer (copertura areale):', self.sp_raggio)

        self.sp_soglia = QDoubleSpinBox()
        self.sp_soglia.setRange(0, 1)
        self.sp_soglia.setSingleStep(0.05)
        self.sp_soglia.setValue(0.85)
        self.sp_soglia.setDecimals(2)
        form_par.addRow('Soglia coerenza cinematica:', self.sp_soglia)
        root.addWidget(grp_par)

        # ── Log e progress ──────────────────────────────────────────────────
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(100)
        self.log.setVisible(False)
        root.addWidget(self.log)

        # ── Pulsanti ─────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.btn_run = QPushButton('Genera scheda')
        self.btn_run.setDefault(True)
        self.btn_run.clicked.connect(self._on_run)
        btn_close = QPushButton('Chiudi')
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(self.btn_run)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        root.addLayout(btn_row)

    # ── Area di studio ───────────────────────────────────────────────────────
    def _on_area_mode_changed(self, draw_checked):
        self.btn_draw.setEnabled(draw_checked)
        self.cb_area_layer.setEnabled(not draw_checked)
        self.btn_area_file.setEnabled(not draw_checked)

    def _start_draw(self):
        canvas = self.iface.mapCanvas()
        self._prev_map_tool = canvas.mapTool()
        self._draw_tool = PolygonDrawTool(canvas)
        self._draw_tool.finished.connect(self._on_draw_finished)
        self._draw_tool.cancelled.connect(self._on_draw_cancelled)
        canvas.setMapTool(self._draw_tool)
        self.lbl_area_status.setText(
            'Disegna il poligono sulla mappa: clic sinistro per i vertici, '
            'clic destro o doppio clic per chiudere (Esc per annullare).')
        self.lbl_area_status.setStyleSheet('color:#2980b9; font-style:italic;')
        self.hide()  # lascia libera la mappa mentre si disegna

    def _restore_map_tool(self):
        canvas = self.iface.mapCanvas()
        if self._prev_map_tool is not None:
            canvas.setMapTool(self._prev_map_tool)
        self._draw_tool = None
        self.show()
        self.raise_()

    def _on_draw_finished(self, geom):
        self.area_geom = geom
        self.area_source_label = 'disegnata sulla mappa'
        self._update_area_status()
        self._restore_map_tool()

    def _on_draw_cancelled(self):
        self._restore_map_tool()

    def _on_area_layer_changed(self, layer):
        if layer is None:
            return
        self._set_area_from_layer(layer, layer.name())

    def _set_area_from_layer(self, lyr, source_label):
        if not lyr.isValid() or lyr.geometryType() != QgsWkbTypes.PolygonGeometry:
            QMessageBox.warning(self, 'Layer non valido',
                                 'Il layer selezionato non è un layer poligonale valido.')
            return
        feats = list(lyr.getFeatures())
        if not feats:
            QMessageBox.warning(self, 'Layer vuoto', 'Il layer non contiene poligoni.')
            return
        geom = feats[0].geometry()
        for f in feats[1:]:
            geom = geom.combine(f.geometry())
        proj_crs = QgsProject.instance().crs()
        if lyr.crs() != proj_crs:
            from qgis.core import QgsCoordinateTransform
            xform = QgsCoordinateTransform(lyr.crs(), proj_crs, QgsProject.instance())
            geom.transform(xform)
        self.area_geom = geom
        self.area_source_label = source_label
        self._update_area_status()

    def _browse_area_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Seleziona file area di studio', '',
            'Vettoriali (*.gpkg *.shp)')
        if not path:
            return
        lyr = QgsVectorLayer(path, 'area_studio', 'ogr')
        self._set_area_from_layer(lyr, os.path.basename(path))

    def _update_area_status(self):
        if self.area_geom is None:
            self.lbl_area_status.setText('Nessuna area definita.')
            self.lbl_area_status.setStyleSheet('color:#7f8c8d; font-style:italic;')
            return
        from qgis.core import QgsDistanceArea, QgsUnitTypes
        da = QgsDistanceArea()
        da.setSourceCrs(QgsProject.instance().crs(), QgsProject.instance().transformContext())
        da.setEllipsoid(QgsProject.instance().ellipsoid() or 'WGS84')
        area_raw = abs(da.measureArea(self.area_geom))
        area_mq = da.convertAreaMeasurement(area_raw, QgsUnitTypes.AreaSquareMeters)
        self.lbl_area_status.setText(
            f'Area definita ({self.area_source_label}): {area_mq:,.1f} mq')
        self.lbl_area_status.setStyleSheet('color:#27ae60; font-style:italic;')

    # ── Esecuzione ────────────────────────────────────────────────────────────
    def _log_msg(self, msg):
        self.log.append(msg)

    def _on_run(self):
        if self.area_geom is None:
            QMessageBox.warning(self, 'Area di studio mancante',
                                 'Definisci prima l\'area di studio (disegno o file).')
            return
        ps_asc = self.cb_ps_asc.currentLayer()
        ps_desc = self.cb_ps_desc.currentLayer()
        if ps_asc is None and ps_desc is None:
            QMessageBox.warning(self, 'Nessun layer PS',
                                 'Seleziona almeno un layer PS (ascendente o discendente).')
            return

        params = {
            'area_geom': QgsGeometry(self.area_geom),
            'ps_asc': ps_asc,
            'ps_asc_field': self.cb_ps_asc_field.currentField() or None,
            'ps_desc': ps_desc,
            'ps_desc_field': self.cb_ps_desc_field.currentField() or None,
            'vis_asc': self.cb_vis_asc.currentLayer(),
            'vis_desc': self.cb_vis_desc.currentLayer(),
            'ewud': self.cb_ewud.currentLayer(),
            'raggio_m': self.sp_raggio.value(),
            'soglia': self.sp_soglia.value(),
        }

        self.log.clear()
        self.log.setVisible(True)
        self.progress.setVisible(True)
        self.btn_run.setEnabled(False)

        self.bridge = TaskBridge()
        self.bridge.log_signal.connect(self._log_msg)
        self.bridge.finished_signal.connect(self._on_finished)
        self.bridge.error_signal.connect(self._on_error)

        self.task = SchedaTask(params, self.bridge)
        QgsApplication.taskManager().addTask(self.task)

    def _on_finished(self, results):
        self.progress.setVisible(False)
        self.btn_run.setEnabled(True)
        self._log_msg('✅ Scheda completata.')
        dlg = SchedaResultsDialog(results, parent=self)
        dlg.show()
        self._results_dlg = dlg  # mantiene un riferimento

    def _on_error(self, msg):
        self.progress.setVisible(False)
        self.btn_run.setEnabled(True)
        self._log_msg(f'❌ Errore: {msg}')
        QMessageBox.critical(self, 'Errore nel calcolo', msg)

    def closeEvent(self, event):
        if self._draw_tool is not None:
            self._restore_map_tool()
        super().closeEvent(event)
