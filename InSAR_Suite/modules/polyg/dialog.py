# -*- coding: utf-8 -*-
"""
dialog.py — Finestra di dialogo del plugin InSAR Polygons.

Flusso basato su buffer + dissolve geometrico, fedele al flusso
manuale originale. Nessun clustering astratto: i poligoni nascono
dall'unione fisica dei buffer sovrapposti.
"""

import traceback
import pandas as pd
import geopandas as gpd

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QDoubleSpinBox, QSpinBox,
    QPushButton, QCheckBox, QGroupBox, QTextEdit,
    QProgressBar, QMessageBox, QRadioButton,
    QButtonGroup, QFrame, QLineEdit, QScrollArea, QWidget,
    QSizePolicy, QFileDialog, QToolButton
)
from qgis.PyQt.QtCore import Qt, pyqtSignal, QObject, QSize
from qgis.PyQt.QtGui import QFont
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsField, QgsFeature,
    QgsGeometry, QgsWkbTypes, QgsMessageLog, Qgis,
    QgsFillSymbol, QgsCoordinateTransform, QgsFeatureRequest,
    QgsTask, QgsApplication, QgsVectorFileWriter, QgsCoordinateTransformContext
)
from qgis.PyQt.QtCore import QVariant
from ..qt_compat import FIELD_INT, FIELD_DOUBLE, FIELD_STRING

from .core import run_analysis, AnalysisWarning


# ─────────────────────────────────────────────────────────────────────────────
# Bridge segnali task → dialog
# ─────────────────────────────────────────────────────────────────────────────

class TaskBridge(QObject):
    log_signal      = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(object)
    error_signal    = pyqtSignal(str)


# ─────────────────────────────────────────────────────────────────────────────
# QgsTask
# ─────────────────────────────────────────────────────────────────────────────

class InSARTask(QgsTask):
    def __init__(self, arrays, analysis_params, bridge):
        super().__init__("InSAR Polygons — analisi", QgsTask.Flag.CanCancel)
        # arrays: dict con coords_all_proj, vel_all, coords_unst_proj, vel_unst
        self.arrays          = arrays
        self.analysis_params = analysis_params
        self.bridge          = bridge
        self.result          = None
        self.error_msg       = None

    def _prog(self, v):
        if self.isCanceled():
            raise InterruptedError("Elaborazione interrotta dall'utente.")
        self.setProgress(v)
        self.bridge.progress_signal.emit(v)

    def _log(self, msg):
        self.bridge.log_signal.emit(msg)

    def run(self):
        try:
            if self.isCanceled():
                raise InterruptedError("Elaborazione interrotta dall'utente.")

            p = self.analysis_params
            a = self.arrays
            result = run_analysis(
                coords_all_proj   = a["coords_all_proj"],
                vel_all           = a["vel_all"],
                coords_unst_proj  = a["coords_unst_proj"],
                vel_unst          = a["vel_unst"],
                velocity_col_unused = "",
                threshold         = p["threshold"],
                radius_m          = p["radius_m"],
                min_ps            = p["min_ps"],
                min_ratio         = p["min_ratio"],
                min_ps_poly       = p["min_ps_poly"],
                smooth            = p["smooth"],
                progress_callback = self._prog,
                log_callback      = self._log,
            )
            self.result = result
            return True

        except InterruptedError as e:
            self.error_msg = f"[INTERRUZIONE] {e}"
            return False
        except AnalysisWarning as e:
            self.error_msg = f"[AVVISO] {e}"
            return False
        except Exception as e:
            self.error_msg = (
                f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
            return False

    def finished(self, success):
        if success and self.result is not None:
            self.bridge.finished_signal.emit(self.result)
        else:
            self.bridge.error_signal.emit(
                self.error_msg or "Errore sconosciuto.")

    def cancel(self):
        self.bridge.log_signal.emit("Interruzione richiesta — attendi...")
        super().cancel()


# ─────────────────────────────────────────────────────────────────────────────
# Dialog principale
# ─────────────────────────────────────────────────────────────────────────────

class InSARPolygonsDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface  = iface
        self.task   = None
        self.bridge = None
        self.setWindowTitle("InSAR Polygons")
        self.setMinimumWidth(520)
        self._build_ui()
        self._populate_layers()

    def _build_ui(self):
        # Layout esterno della finestra (non scrollabile)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Area scrollabile per il contenuto
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        # Widget contenitore dentro lo scroll
        container = QWidget()
        root = QVBoxLayout(container)
        root.setSpacing(8)
        root.setContentsMargins(8, 8, 8, 8)

        title = QLabel("InSAR Polygons — Aree di deformazione")
        f = QFont(); f.setPointSize(11); f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(sep)

        # ── Input ─────────────────────────────────────────────────────────────
        grp_in = QGroupBox("Layer di input")
        g = QGridLayout(grp_in)

        g.addWidget(QLabel("Layer punti:"), 0, 0)
        self.combo_layer = QComboBox()
        self.combo_layer.currentIndexChanged.connect(self._on_layer_changed)
        g.addWidget(self.combo_layer, 0, 1, 1, 2)

        g.addWidget(QLabel("Colonna velocità (mm/yr):"), 1, 0)
        self.combo_vel = QComboBox()
        g.addWidget(self.combo_vel, 1, 1, 1, 2)

        g.addWidget(QLabel("Punti da elaborare:"), 2, 0)
        self.radio_selected = QRadioButton("Solo punti selezionati")
        self.radio_canvas   = QRadioButton("Canvas extent corrente")
        self.radio_all      = QRadioButton("Tutti i punti del layer")
        self.radio_canvas.setChecked(True)
        bg = QButtonGroup(self)
        for r in [self.radio_selected, self.radio_canvas, self.radio_all]:
            bg.addButton(r)
        rl = QVBoxLayout(); rl.setSpacing(2)
        for r in [self.radio_selected, self.radio_canvas, self.radio_all]:
            rl.addWidget(r)
        g.addLayout(rl, 2, 1, 1, 2)

        root.addWidget(grp_in)

        # ── Parametri fisici ──────────────────────────────────────────────────
        grp_phys = QGroupBox("Parametri fisici")
        g2 = QGridLayout(grp_phys)

        g2.addWidget(QLabel("Soglia velocità instabile (mm/yr, |vel|):"), 0, 0)
        self.spin_threshold = QDoubleSpinBox()
        self.spin_threshold.setRange(0.1, 100.0)
        self.spin_threshold.setSingleStep(0.5)
        self.spin_threshold.setValue(2.0)
        self.spin_threshold.setDecimals(1)
        g2.addWidget(self.spin_threshold, 0, 1)

        g2.addWidget(QLabel("Raggio buffer e ricerca (m):"), 1, 0)
        self.spin_radius = QDoubleSpinBox()
        self.spin_radius.setRange(1.0, 10000.0)
        self.spin_radius.setSingleStep(10.0)
        self.spin_radius.setValue(50.0)
        self.spin_radius.setDecimals(0)
        g2.addWidget(self.spin_radius, 1, 1)

        root.addWidget(grp_phys)

        # ── Criteri di validazione ────────────────────────────────────────────
        grp_val = QGroupBox("Criteri di validazione")
        g3 = QGridLayout(grp_val)

        g3.addWidget(QLabel("PS instabili minimi nell'intorno:"), 0, 0)
        self.spin_min_ps = QSpinBox()
        self.spin_min_ps.setRange(1, 200)
        self.spin_min_ps.setValue(5)
        g3.addWidget(self.spin_min_ps, 0, 1)

        g3.addWidget(QLabel("Rapporto minimo instabili/totali (0–1):"), 1, 0)
        self.spin_min_ratio = QDoubleSpinBox()
        self.spin_min_ratio.setRange(0.0, 1.0)
        self.spin_min_ratio.setSingleStep(0.05)
        self.spin_min_ratio.setValue(0.75)
        self.spin_min_ratio.setDecimals(2)
        g3.addWidget(self.spin_min_ratio, 1, 1)

        g3.addWidget(QLabel("PS minimi nel poligono finale:"), 2, 0)
        self.spin_min_ps_poly = QSpinBox()
        self.spin_min_ps_poly.setRange(1, 200)
        self.spin_min_ps_poly.setValue(5)
        g3.addWidget(self.spin_min_ps_poly, 2, 1)

        root.addWidget(grp_val)

        # ── Post-processing ───────────────────────────────────────────────────
        grp_post = QGroupBox("Post-processing")
        g4 = QGridLayout(grp_post)

        self.chk_smooth = QCheckBox("Smoothing morfologico del bordo")
        self.chk_smooth.setChecked(True)
        g4.addWidget(self.chk_smooth, 0, 0, 1, 2)

        root.addWidget(grp_post)

        # ── Output ────────────────────────────────────────────────────────────
        grp_out = QGroupBox("Output")
        g5 = QGridLayout(grp_out)
        g5.addWidget(QLabel("Nome layer risultato:"), 0, 0)
        self.edit_out_name = QLineEdit("InSAR Polygons")
        g5.addWidget(self.edit_out_name, 0, 1)
        self.chk_add_layer = QCheckBox("Aggiungi al progetto QGIS")
        self.chk_add_layer.setChecked(True)
        g5.addWidget(self.chk_add_layer, 1, 0, 1, 2)
        self.chk_save = QCheckBox("Salva su file (GeoPackage)")
        self.chk_save.setChecked(False)
        g5.addWidget(self.chk_save, 2, 0, 1, 2)
        row_save = QHBoxLayout()
        self.le_save_path = QLineEdit()
        self.le_save_path.setPlaceholderText("Percorso file .gpkg ...")
        self.le_save_path.setEnabled(False)
        self.btn_save_path = QToolButton()
        self.btn_save_path.setText("…")
        self.btn_save_path.setFixedSize(QSize(28, 28))
        self.btn_save_path.setEnabled(False)
        row_save.addWidget(self.le_save_path)
        row_save.addWidget(self.btn_save_path)
        g5.addLayout(row_save, 3, 0, 1, 2)
        root.addWidget(grp_out)

        # ── Log ───────────────────────────────────────────────────────────────
        grp_log = QGroupBox("Log")
        gl = QVBoxLayout(grp_log)
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(120)
        self.log_box.setFont(QFont("Courier", 9))
        gl.addWidget(self.log_box)
        root.addWidget(grp_log)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        root.addWidget(self.progress_bar)

        self.chk_save.toggled.connect(self._toggle_save)
        self.btn_save_path.clicked.connect(self._browse_save)

        # ── Pulsanti ──────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self.btn_run = QPushButton("▶  Esegui")
        self.btn_run.setDefault(True)
        self.btn_run.clicked.connect(self._on_run)

        self.btn_stop = QPushButton("■  Interrompi")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)

        self.btn_reset = QPushButton("↺  Azzera")
        self.btn_reset.setToolTip("Azzera log e barra di avanzamento")
        self.btn_reset.clicked.connect(self._on_reset)

        self.btn_close = QPushButton("Chiudi")
        self.btn_close.clicked.connect(self.close)

        for btn in [self.btn_run, self.btn_stop,
                    self.btn_reset, self.btn_close]:
            btn_row.addWidget(btn)

        # I pulsanti vanno nel container scrollabile
        root.addLayout(btn_row)

        # Collega container → scroll → outer
        scroll.setWidget(container)
        outer.addWidget(scroll)

        # Adatta la finestra allo schermo disponibile
        from qgis.PyQt.QtWidgets import QApplication
        screen = QApplication.primaryScreen().availableGeometry()
        max_h = int(screen.height() * 0.85)
        self.setMaximumHeight(max_h)
        self.resize(540, min(max_h, 720))

    # ── Layer ─────────────────────────────────────────────────────────────────

    def _populate_layers(self):
        self.combo_layer.blockSignals(True)
        self.combo_layer.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if (isinstance(layer, QgsVectorLayer) and
                    layer.geometryType() == QgsWkbTypes.GeometryType.PointGeometry):
                self.combo_layer.addItem(layer.name(), layer.id())
        self.combo_layer.blockSignals(False)
        self._on_layer_changed()

    def _on_layer_changed(self):
        self.combo_vel.clear()
        layer = self._current_layer()
        if layer is None:
            return
        for field in layer.fields():
            if field.isNumeric():
                self.combo_vel.addItem(field.name())
        for hint in ["vel", "VEL", "velocity", "Velocity",
                     "vel_mm_yr", "VEL_MM_YR", "v_mm_a", "VELOCITY"]:
            idx = self.combo_vel.findText(hint, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self.combo_vel.setCurrentIndex(idx)
                break
        n_sel = layer.selectedFeatureCount()
        self.radio_selected.setText(
            f"Solo punti selezionati ({n_sel} selezionati)")
        self.radio_selected.setEnabled(n_sel > 0)
        if n_sel == 0 and self.radio_selected.isChecked():
            self.radio_canvas.setChecked(True)

    def _current_layer(self):
        if self.combo_layer.count() == 0:
            return None
        return QgsProject.instance().mapLayer(
            self.combo_layer.currentData())

    def _toggle_save(self, checked):
        self.le_save_path.setEnabled(checked)
        self.btn_save_path.setEnabled(checked)

    def _browse_save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Salva layer InSAR Polygons", "", "GeoPackage (*.gpkg)")
        if path:
            if not path.endswith(".gpkg"):
                path += ".gpkg"
            self.le_save_path.setText(path)

    # ── Esecuzione ────────────────────────────────────────────────────────────

    def _on_run(self):
        layer = self._current_layer()
        if layer is None:
            QMessageBox.warning(self, "Attenzione",
                                "Nessun layer di punti disponibile.")
            return
        if self.combo_vel.count() == 0:
            QMessageBox.warning(self, "Attenzione",
                                "Nessun campo numerico trovato nel layer.")
            return

        self.log_box.clear()
        self.progress_bar.setValue(0)
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)

        # Parametri di estrazione (thread principale — accesso sicuro a QGIS)
        if self.radio_selected.isChecked() and layer.selectedFeatureCount() > 0:
            extract_params = {"mode": "selected"}
        elif self.radio_canvas.isChecked():
            canvas_extent = self.iface.mapCanvas().extent()
            canvas_crs    = self.iface.mapCanvas().mapSettings().destinationCrs()
            layer_crs     = layer.crs()
            if canvas_crs != layer_crs:
                transform   = QgsCoordinateTransform(
                    canvas_crs, layer_crs, QgsProject.instance())
                canvas_rect = transform.transformBoundingBox(canvas_extent)
            else:
                canvas_rect = canvas_extent
            extract_params = {"mode": "canvas", "canvas_rect": canvas_rect}
        else:
            extract_params = {"mode": "all"}

        analysis_params = {
            "velocity_col": self.combo_vel.currentText(),
            "threshold":    self.spin_threshold.value(),
            "radius_m":     self.spin_radius.value(),
            "min_ps":       self.spin_min_ps.value(),
            "min_ratio":    self.spin_min_ratio.value(),
            "min_ps_poly":  self.spin_min_ps_poly.value(),
            "smooth":       self.chk_smooth.isChecked(),
        }

        self._log(f"Layer: {layer.name()}  |  "
                  f"Campo vel.: {analysis_params['velocity_col']}  |  "
                  f"Modalità: {extract_params['mode']}")

        # ── Estrazione e riproiezione nel thread principale ───────────────────
        try:
            arrays, crs_authid = self._extract_and_project(
                layer, extract_params, analysis_params["velocity_col"],
                analysis_params["threshold"])
        except Exception as e:
            self._on_error(f"Errore lettura layer: {e}")
            return

        self._log(f"Punti estratti: {arrays['n_all']:,}  |  "
                  f"PS instabili: {arrays['n_unst']:,}")

        # Salva crs_authid per ricostruire il GeoDataFrame finale
        self._crs_authid = crs_authid

        self.bridge = TaskBridge()
        self.bridge.log_signal.connect(self._log)
        self.bridge.progress_signal.connect(self.progress_bar.setValue)
        self.bridge.finished_signal.connect(self._on_finished)
        self.bridge.error_signal.connect(self._on_error)

        self.task = InSARTask(
            arrays          = arrays,
            analysis_params = analysis_params,
            bridge          = self.bridge,
        )
        QgsApplication.taskManager().addTask(self.task)

    def _on_stop(self):
        if self.task is not None:
            self.task.cancel()
            self.btn_stop.setEnabled(False)

    def _on_reset(self):
        if self.task is not None:
            QMessageBox.warning(self, "Attenzione",
                                "Interrompi prima l'elaborazione in corso.")
            return
        # Disconnette il bridge precedente per evitare doppie chiamate
        # alla prossima esecuzione
        if self.bridge is not None:
            try:
                self.bridge.log_signal.disconnect()
                self.bridge.progress_signal.disconnect()
                self.bridge.finished_signal.disconnect()
                self.bridge.error_signal.disconnect()
            except Exception as _e:
                QgsMessageLog.logMessage(f"InSAR Suite: eccezione ignorata: {_e}", "InSAR Suite", level=Qgis.MessageLevel.Warning)
                pass
            self.bridge = None
        self.log_box.clear()
        self.progress_bar.setValue(0)

    def _reset_buttons(self):
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)

    # ── Estrazione e riproiezione (thread principale) ─────────────────────────

    def _extract_and_project(self, layer, extract_params,
                              velocity_col, threshold):
        """
        Estrae i punti dal layer QGIS, li riproietta in EPSG:3857 usando
        QgsCoordinateTransform (API QGIS, non pyproj) e restituisce array
        numpy puri. Nessun oggetto geopandas/pyproj viene creato qui.
        """
        import numpy as np
        from qgis.core import (QgsCoordinateTransform,
                               QgsCoordinateReferenceSystem, QgsPointXY)

        request  = QgsFeatureRequest()
        mode     = extract_params["mode"]

        if mode == "selected":
            features = layer.selectedFeatures()
        elif mode == "canvas":
            request.setFilterRect(extract_params["canvas_rect"])
            request.setFlags(QgsFeatureRequest.Flag.ExactIntersect)
            features = layer.getFeatures(request)
        else:
            features = layer.getFeatures()

        # Trasformazione QGIS → EPSG:3857 (usa GDAL/PROJ via QGIS, thread-safe)
        crs_src  = layer.crs()
        crs_dest = QgsCoordinateReferenceSystem("EPSG:3857")
        crs_authid = crs_src.authid()
        transform  = QgsCoordinateTransform(
            crs_src, crs_dest, QgsProject.instance())

        xs_all, ys_all, vel_all_list = [], [], []

        for feat in features:
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            pt  = geom.asPoint()
            vel = feat[velocity_col]
            if vel is None:
                continue
            try:
                vel = float(vel)
            except (TypeError, ValueError):
                continue
            pt3857 = transform.transform(pt)
            xs_all.append(pt3857.x())
            ys_all.append(pt3857.y())
            vel_all_list.append(vel)

        if not xs_all:
            raise ValueError(
                "Nessun punto trovato con i criteri selezionati.")

        coords_all_proj = np.column_stack(
            [np.array(xs_all), np.array(ys_all)])
        vel_all = np.array(vel_all_list)

        # Separa instabili
        mask_unst       = np.abs(vel_all) >= threshold
        coords_unst_proj = coords_all_proj[mask_unst]
        vel_unst         = vel_all[mask_unst]

        arrays = {
            "coords_all_proj":  coords_all_proj,
            "vel_all":          vel_all,
            "coords_unst_proj": coords_unst_proj,
            "vel_unst":         vel_unst,
            "n_all":            len(vel_all),
            "n_unst":           int(mask_unst.sum()),
        }
        return arrays, crs_authid

    # ── Risultati ─────────────────────────────────────────────────────────────

    def _on_finished(self, records):
        self.task = None
        if self.bridge is not None:
            try:
                self.bridge.log_signal.disconnect()
                self.bridge.progress_signal.disconnect()
                self.bridge.finished_signal.disconnect()
                self.bridge.error_signal.disconnect()
            except Exception as _e:
                QgsMessageLog.logMessage(f"InSAR Suite: eccezione ignorata: {_e}", "InSAR Suite", level=Qgis.MessageLevel.Warning)
                pass
            self.bridge = None
        self._reset_buttons()

        if not records:
            self._log("Nessun poligono trovato.")
            return

        # Costruisce GeoDataFrame nel thread principale (pyproj sicuro qui)
        from shapely.geometry import mapping
        crs_authid = getattr(self, "_crs_authid", "EPSG:4326")
        gdf_poly = gpd.GeoDataFrame(records, crs="EPSG:3857")
        gdf_poly = gdf_poly.to_crs(crs_authid)

        self._log(f"✓ Completato — {len(gdf_poly)} poligono/i trovato/i.")
        cols = ["cluster_id", "vel_class", "n_ps", "n_unstable",
                "vel_mean", "vel_min", "vel_max", "area_km2"]
        for _, row in gdf_poly[cols].iterrows():
            self._log(
                f"  C{int(row.cluster_id):02d} | "
                f"{row.vel_class:18s} | "
                f"n={int(row.n_ps):4d} (inst={int(row.n_unstable)}) | "
                f"vel_mean={row.vel_mean:+.2f} "
                f"[{row.vel_min:+.2f}, {row.vel_max:+.2f}] mm/yr | "
                f"area={row.area_km2:.4f} km²"
            )
        if self.chk_add_layer.isChecked():
            self._add_to_qgis(gdf_poly)

    def _on_error(self, msg):
        self.task = None
        if self.bridge is not None:
            try:
                self.bridge.log_signal.disconnect()
                self.bridge.progress_signal.disconnect()
                self.bridge.finished_signal.disconnect()
                self.bridge.error_signal.disconnect()
            except Exception as _e:
                QgsMessageLog.logMessage(f"InSAR Suite: eccezione ignorata: {_e}", "InSAR Suite", level=Qgis.MessageLevel.Warning)
                pass
            self.bridge = None
        self._reset_buttons()
        self.progress_bar.setValue(0)

        if msg.startswith("[INTERRUZIONE]"):
            self._log(msg)
            self._log("Pronto per una nuova elaborazione.")

        elif msg.startswith("[AVVISO]"):
            # Condizione attesa legata ai dati/parametri: finestra informativa
            testo = msg[len("[AVVISO] "):].strip()
            self._log(f"⚠ {testo.splitlines()[0]}")
            dlg = QMessageBox(self)
            dlg.setWindowTitle("Attenzione — Nessun risultato")
            dlg.setIcon(QMessageBox.Icon.Warning)
            dlg.setText(testo)
            dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
            dlg.exec()

        else:
            # Errore imprevisto: mostra il traceback completo
            self._log(f"[ERRORE] {msg}")
            QMessageBox.critical(self, "Errore durante l'elaborazione",
                                 str(msg))

    def _log(self, msg):
        self.log_box.append(msg)
        self.log_box.verticalScrollBar().setValue(
            self.log_box.verticalScrollBar().maximum())
        QgsMessageLog.logMessage(msg, "InSAR Polygons", Qgis.MessageLevel.Info)

    # ── Aggiunta layer QGIS ───────────────────────────────────────────────────

    def _add_to_qgis(self, gdf_poly):
        crs_str  = gdf_poly.crs.to_epsg()
        crs_str  = f"EPSG:{crs_str}" if crs_str else (gdf_poly.crs.to_string() if gdf_poly.crs else "EPSG:4326")
        out_name = self.edit_out_name.text() or "InSAR Polygons"
        vl = QgsVectorLayer(f"Polygon?crs={crs_str}", out_name, "memory")
        pr = vl.dataProvider()

        field_defs = [
            ("cluster_id",  FIELD_INT),
            ("vel_class",   FIELD_STRING),
            ("priority",    FIELD_INT),
            ("n_ps",        FIELD_INT),
            ("n_unstable",  FIELD_INT),
            ("vel_mean",    FIELD_DOUBLE),
            ("vel_std",     FIELD_DOUBLE),
            ("vel_min",     FIELD_DOUBLE),
            ("vel_max",     FIELD_DOUBLE),
            ("area_km2",    FIELD_DOUBLE),
        ]
        pr.addAttributes([QgsField(n, t) for n, t in field_defs])
        vl.updateFields()

        feats = []
        for _, row in gdf_poly.iterrows():
            feat = QgsFeature()
            feat.setGeometry(QgsGeometry.fromWkt(row.geometry.wkt))
            feat.setAttributes([
                int(row.cluster_id),
                str(row.vel_class),
                int(row.priority),
                int(row.n_ps),
                int(row.n_unstable),
                float(row.vel_mean),
                float(row.vel_std),
                float(row.vel_min),
                float(row.vel_max),
                float(row.area_km2),
            ])
            feats.append(feat)

        pr.addFeatures(feats)
        vl.updateExtents()
        self._apply_classified_style(vl)

        # ── Salvataggio permanente su GeoPackage (se richiesto) ─────────────
        # Fatto DOPO che il layer è già completo in memoria (stesso approccio
        # usato in EWUD/VIS), per evitare scritture dirette dentro catene di
        # elaborazione dove i conflitti sul campo fid di GeoPackage possono
        # corrompere silenziosamente gli attributi.
        save_path = self.le_save_path.text().strip() if self.chk_save.isChecked() else None
        if self.chk_save.isChecked() and not save_path:
            QMessageBox.warning(self, "Attenzione",
                "Hai selezionato 'Salva su file' ma non hai indicato un percorso.\n"
                "Il layer verrà comunque aggiunto al progetto solo in memoria.")
            save_path = None

        final_layer = vl
        if save_path:
            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName = "GPKG"
            opts.fileEncoding = "UTF-8"
            opts.actionOnExistingFile = QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
            opts.layerName = out_name
            err, msg, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
                vl, save_path, QgsCoordinateTransformContext(), opts)
            if err == QgsVectorFileWriter.WriterError.NoError:
                import os as _os_poly
                display_name = _os_poly.path.splitext(_os_poly.path.basename(save_path))[0] or out_name
                saved_layer = QgsVectorLayer(save_path, display_name, "ogr")
                if saved_layer.isValid():
                    self._apply_classified_style(saved_layer)
                    final_layer = saved_layer
                    self._log(f"Layer salvato su file: <b>{save_path}</b>")
                else:
                    self._log("<span style='color:#f39c12'>⚠ Salvataggio riuscito ma "
                               "rilettura del file fallita; uso il layer in memoria.</span>")
            else:
                QMessageBox.warning(self, "Attenzione",
                    "Salvataggio fallito:\n" + str(msg) +
                    "\n\nIl layer verrà comunque aggiunto al progetto solo in memoria.")
                self._log(f"<span style='color:#e74c3c'>⚠ Salvataggio fallito: {msg}</span>")

        if self.chk_add_layer.isChecked():
            QgsProject.instance().addMapLayer(final_layer)
            self.iface.mapCanvas().refresh()
            self._log(f"Layer '{final_layer.name()}' aggiunto al progetto.")

    def _apply_classified_style(self, layer):
        """
        Stile categorizzato su vel_class: un colore fisso per classe,
        topologicamente coerente con il clip gerarchico.
        """
        from qgis.core import (
            QgsCategorizedSymbolRenderer, QgsRendererCategory
        )

        CLASS_COLORS = {
            "< -10 mm/yr":     "#cb181d",
            "-10 ÷ -5 mm/yr":  "#fd8d3c",
            "-5 ÷ -2 mm/yr":   "#fee391",
            "+2 ÷ +5 mm/yr":   "#9ecae1",
            "+5 ÷ +10 mm/yr":  "#4292c6",
            "> +10 mm/yr":     "#08306b",
        }

        categories = []
        for label, color in CLASS_COLORS.items():
            sym = QgsFillSymbol.createSimple({
                "color":        color,
                "color_border": "#333333",
                "width_border": "0.4",
                "style":        "solid",
            })
            sym.setOpacity(0.65)
            categories.append(QgsRendererCategory(label, sym, label))

        renderer = QgsCategorizedSymbolRenderer("vel_class", categories)
        layer.setRenderer(renderer)
        layer.triggerRepaint()
