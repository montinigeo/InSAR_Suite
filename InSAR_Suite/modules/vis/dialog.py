import os
import math

import numpy as np
from osgeo import gdal, ogr, osr

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QDoubleSpinBox, QPushButton,
    QGroupBox, QRadioButton, QButtonGroup, QLineEdit,
    QProgressBar, QMessageBox, QFrame,
    QFileDialog, QToolButton
)
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtCore import QSize, pyqtSignal, QTimer

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer,
    QgsMapLayerProxyModel,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsFields, QgsField, QgsFeature, QgsGeometry, QgsPointXY,
    QgsTask, QgsApplication
)
from qgis.gui import QgsMapLayerComboBox, QgsExtentWidget
from qgis.PyQt.QtCore import QVariant

gdal.UseExceptions()


# ── Tabella satelliti ──────────────────────────────────────────────────────────
SATELLITES = {
    "Sentinel-1 (IW - EGMS)": {
        "ASC":  {"azimut": -13,  "off_nadir": 39},
        "DESC": {"azimut": 190,  "off_nadir": 39},
        "band": "C",
    },
    "ERS-1/2 / ENVISAT": {
        "ASC":  {"azimut": -15,  "off_nadir": 23},
        "DESC": {"azimut": 165,  "off_nadir": 23},
        "band": "C",
    },
    "RADARSAT-2": {
        "ASC":  {"azimut": -10,  "off_nadir": 35},
        "DESC": {"azimut": 190,  "off_nadir": 35},
        "band": "C",
    },
    "COSMO-SkyMed (1a gen.)": {
        "ASC":  {"azimut": -15,  "off_nadir": 35},
        "DESC": {"azimut": 165,  "off_nadir": 35},
        "band": "X",
    },
    "COSMO-SkyMed 2nd Gen": {
        "ASC":  {"azimut": -15,  "off_nadir": 35},
        "DESC": {"azimut": 165,  "off_nadir": 35},
        "band": "X",
    },
    "TerraSAR-X / TanDEM-X": {
        "ASC":  {"azimut": -10,  "off_nadir": 35},
        "DESC": {"azimut": 170,  "off_nadir": 35},
        "band": "X",
    },
    "Personalizzato": {
        "ASC":  {"azimut": -13,  "off_nadir": 39},
        "DESC": {"azimut": 190,  "off_nadir": 39},
        "band": "-",
    },
}

PS_FILTERS  = "Shapefile (*.shp);;GeoPackage (*.gpkg);;Tutti i file (*.*)"
DEM_FILTERS = "GeoTIFF (*.tif *.tiff);;ERDAS IMG (*.img);;Tutti i file (*.*)"


# ── Funzioni comuni ────────────────────────────────────────────────────────────

def _extent_to_crs(extent, src_crs, dst_crs):
    if src_crs.authid() == dst_crs.authid():
        return extent
    return QgsCoordinateTransform(
        src_crs, dst_crs, QgsProject.instance()
    ).transformBoundingBox(extent)


def _reproject_points(xs, ys, src_crs_wkt, dst_crs_wkt):
    src = osr.SpatialReference(); src.ImportFromWkt(src_crs_wkt)
    dst = osr.SpatialReference(); dst.ImportFromWkt(dst_crs_wkt)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(src, dst)
    results = transform.TransformPoints(list(zip(xs, ys)))
    return (np.array([r[0] for r in results]),
            np.array([r[1] for r in results]))


def _apply_qml(layer):
    qml = os.path.join(os.path.dirname(__file__), "egms_pc_rilevata.qml")
    if os.path.exists(qml):
        layer.loadNamedStyle(qml)
        layer.triggerRepaint()


def _load_and_add_layer(path, is_raster, name=None):
    display = name or os.path.splitext(os.path.basename(path))[0]
    layer = QgsRasterLayer(path, display) if is_raster \
            else QgsVectorLayer(path, display, "ogr")
    if not layer.isValid():
        raise RuntimeError("Impossibile caricare il file:\n" + path)
    QgsProject.instance().addMapLayer(layer)
    return layer


# ── Task di calcolo ────────────────────────────────────────────────────────────

class InSARTask(QgsTask):
    """
    Flusso calcolo VIS sui punti PS:
      1. Legge i PS da file (OGR) e filtra quelli nell'estensione
      2. Riproietta i PS nel CRS del DEM
      3. Ritaglia il DEM alla cella configurata con targetAlignedPixels=True
      4. Calcola aspect e slope con algoritmo Horn
      5. Campiona aspect e slope alla posizione di ogni PS
      6. Calcola pc_mov e costruisce il layer risultato
    """

    stepProgress = pyqtSignal(int, str)

    def __init__(self, params, on_done, on_error, on_cancelled):
        super().__init__("InSAR VIS",
            QgsTask.CanCancel if hasattr(QgsTask, "CanCancel")
            else QgsTask.Flag.CanCancel)
        self.params        = params
        self._result       = None
        self._error        = None
        self._on_done      = on_done
        self._on_error     = on_error
        self._on_cancelled = on_cancelled

    def run(self):
        try:
            self._result = self._process()
            return True
        except Exception as e:
            import traceback
            self._error = traceback.format_exc()
            return False

    def finished(self, result):
        if result and self._result is not None:
            self._on_done(self._result)
        elif not result:
            if self.isCanceled():
                self._on_cancelled()
            else:
                self._on_error(self._error or "Errore sconosciuto.")

    def _chk(self):
        if self.isCanceled():
            raise RuntimeError("__CANCELLED__")

    def _prog(self, pct, msg):
        self.setProgress(pct)
        self.stepProgress.emit(pct, msg)

    def _process(self):
        p        = self.params
        dem_path = p["dem_path"]
        ps_uri   = p["ps_path"]
        az       = p["azimut"]
        on_nad   = p["off_nadir"]
        out_name = p["output_name"]
        work_wkt = p["work_crs_wkt"]
        work_id  = p["work_crs_id"]
        ps_wkt   = p["ps_crs_wkt"]
        ps_id    = p["ps_crs_id"]
        xmin, ymin, xmax, ymax = p["xmin"], p["ymin"], p["xmax"], p["ymax"]
        cell     = p["cell_size"]

        # ── 1. Lettura PS con OGR ─────────────────────────────────────────────
        self._prog(5, "1/5 - Lettura PS...")
        self._chk()

        ps_file = ps_uri.split("|layername=")[0]
        layer_name = ps_uri.split("|layername=")[1] if "|layername=" in ps_uri else None
        ogr_ds = ogr.Open(ps_file, 0)
        if ogr_ds is None:
            raise RuntimeError("OGR non riesce ad aprire:\n" + ps_file)
        lyr = ogr_ds.GetLayerByName(layer_name) if layer_name else ogr_ds.GetLayer(0)
        if lyr is None:
            raise RuntimeError("Layer non trovato in:\n" + ps_file)

        lyr_defn  = lyr.GetLayerDefn()
        field_defs = [{"name": lyr_defn.GetFieldDefn(i).GetName(),
                       "ogr_type": lyr_defn.GetFieldDefn(i).GetType()}
                      for i in range(lyr_defn.GetFieldCount())]

        # Filtro spaziale
        if ps_id != work_id:
            cx, cy = _reproject_points(
                np.array([xmin, xmax, xmin, xmax]),
                np.array([ymin, ymin, ymax, ymax]),
                work_wkt, ps_wkt)
            lyr.SetSpatialFilterRect(cx.min(), cy.min(), cx.max(), cy.max())
        else:
            lyr.SetSpatialFilterRect(xmin, ymin, xmax, ymax)

        xs_list, ys_list, attrs_list = [], [], []
        n_tot = lyr.GetFeatureCount()
        lyr.ResetReading()
        for i, feat in enumerate(lyr):
            if i % 500 == 0:
                self._chk()
                if n_tot > 0:
                    pct = 5 + int(10 * i / n_tot)
                    self._prog(pct, "1/5 - Lettura PS... (" + str(i) + "/" + str(n_tot) + ")")
            geom = feat.GetGeometryRef()
            if geom is None: continue
            flat = ogr.GT_Flatten(geom.GetGeometryType())
            if flat == ogr.wkbMultiPoint:
                if geom.GetGeometryCount() == 0: continue
                geom = geom.GetGeometryRef(0)
            elif flat != ogr.wkbPoint: continue
            xs_list.append(geom.GetX())
            ys_list.append(geom.GetY())
            attrs_list.append([feat.GetField(fd["name"]) for fd in field_defs])
        lyr.SetSpatialFilter(None)
        ogr_ds = None

        if not xs_list:
            raise RuntimeError(
                "Nessun punto PS trovato nell estensione selezionata.")

        xs_orig = np.array(xs_list, dtype=np.float64)
        ys_orig = np.array(ys_list, dtype=np.float64)

        # ── 2. Riproiezione PS nel CRS di lavoro ─────────────────────────────
        self._prog(18, "2/5 - Riproiezione PS...")
        self._chk()

        if ps_id != work_id:
            xs_w, ys_w = _reproject_points(xs_orig, ys_orig, ps_wkt, work_wkt)
        else:
            xs_w, ys_w = xs_orig.copy(), ys_orig.copy()

        in_ext = ((xs_w >= xmin) & (xs_w <= xmax) &
                  (ys_w >= ymin) & (ys_w <= ymax))
        if not np.any(in_ext):
            raise RuntimeError(
                "Nessun punto PS trovato nell estensione selezionata.")

        xs_sel    = xs_w[in_ext]
        ys_sel    = ys_w[in_ext]
        xs_out    = xs_orig[in_ext]
        ys_out    = ys_orig[in_ext]
        attrs_sel = [a for a, m in zip(attrs_list, in_ext) if m]

        # ── 3. Warp DEM alla cella configurata con snap to grid ───────────────
        self._prog(30, "3/5 - Clip e ricampionamento DEM...")
        self._chk()

        ds_src = gdal.Open(dem_path, gdal.GA_ReadOnly)
        if ds_src is None:
            raise RuntimeError("Impossibile aprire il DEM:\n" + dem_path)
        gt_src = ds_src.GetGeoTransform()
        ds_src = None

        warp_opts = gdal.WarpOptions(
            format="MEM",
            outputBounds=(xmin, ymin, xmax, ymax),
            xRes=cell, yRes=cell,
            dstSRS=work_wkt,
            resampleAlg=gdal.GRA_Bilinear,
            targetAlignedPixels=True,
            multithread=True,
            warpMemoryLimit=512,
        )
        ds = gdal.Warp("", dem_path, options=warp_opts)
        if ds is None:
            raise RuntimeError("gdal.Warp fallito sul DEM.")
        gt     = ds.GetGeoTransform()
        nodata = ds.GetRasterBand(1).GetNoDataValue()
        arr    = ds.GetRasterBand(1).ReadAsArray().astype(np.float64)
        ds     = None

        # ── 4. Aspect e slope (algoritmo Horn) ───────────────────────────────
        self._prog(55, "4/5 - Calcolo aspect e slope...")
        self._chk()

        cell_x = abs(gt[1])
        cell_y = abs(gt[5])
        pad = np.pad(arr, 1, mode="edge")
        if nodata is not None:
            pad[pad == nodata] = np.nan

        a=pad[:-2,:-2]; b=pad[:-2,1:-1]; c=pad[:-2,2:]
        d=pad[1:-1,:-2];                  f=pad[1:-1,2:]
        g=pad[2:, :-2]; h=pad[2:, 1:-1]; ii=pad[2:, 2:]

        dz_dx = ((c + 2*f + ii) - (a + 2*d + g)) / (8 * cell_x)
        dz_dy = ((g + 2*h + ii) - (a + 2*b + c)) / (8 * cell_y)

        if gt[1] < 0: dz_dx = -dz_dx
        if gt[5] > 0: dz_dy = -dz_dy

        asp_arr = np.degrees(np.arctan2(-dz_dx, dz_dy)) % 360.0
        slp_arr = np.degrees(np.arctan(np.sqrt(dz_dx**2 + dz_dy**2)))
        flat = (dz_dx == 0) & (dz_dy == 0)
        asp_arr[flat] = 0.0

        # ── 5. Campionamento e calcolo pc_mov ─────────────────────────────────
        self._prog(75, "5/5 - Campionamento e calcolo pc_mov...")
        self._chk()

        x0, dx = gt[0], gt[1]
        y0, dy = gt[3], gt[5]
        nrows, ncols = arr.shape

        cols = np.round((xs_sel - x0) / dx).astype(int)
        rows = np.round((ys_sel - y0) / dy).astype(int)
        valid = (cols >= 0) & (cols < ncols) & (rows >= 0) & (rows < nrows)

        asp_vals = np.full(len(xs_sel), np.nan)
        slp_vals = np.full(len(xs_sel), np.nan)
        asp_vals[valid] = asp_arr[rows[valid], cols[valid]]
        slp_vals[valid] = slp_arr[rows[valid], cols[valid]]
        if nodata is not None:
            asp_vals[asp_vals == nodata] = np.nan
            slp_vals[slp_vals == nodata] = np.nan

        phi   = math.radians(az)
        theta = math.radians(on_nad)
        alpha = np.radians(slp_vals)
        beta  = np.radians(asp_vals)
        pc_vals = np.abs(
            np.cos(alpha) * np.sin(theta) * np.sin(beta - phi)
            + np.sin(alpha) * np.cos(theta)
        ) * 100.0
        simple = math.cos(theta) * 100.0
        pc_vals = np.where(slp_vals <= 3, simple, pc_vals)
        pc_vals[np.isnan(asp_vals) | np.isnan(slp_vals)] = np.nan

        # ── 6. Costruzione layer risultato ────────────────────────────────────
        self._prog(88, "Costruzione layer risultato...")
        self._chk()

        OGR_TO_QVARIANT = {
            ogr.OFTInteger:   QVariant.Int,
            ogr.OFTInteger64: QVariant.LongLong,
            ogr.OFTReal:      QVariant.Double,
            ogr.OFTString:    QVariant.String,
            ogr.OFTDate:      QVariant.Date,
            ogr.OFTDateTime:  QVariant.DateTime,
        }
        fields = QgsFields()
        for fd in field_defs:
            fields.append(QgsField(fd["name"],
                OGR_TO_QVARIANT.get(fd["ogr_type"], QVariant.String)))
        fields.append(QgsField("esp1",   QVariant.Double))
        fields.append(QgsField("inc1",   QVariant.Double))
        fields.append(QgsField("pc_mov", QVariant.Double))

        mem = QgsVectorLayer("Point?crs=" + ps_id, out_name, "memory")
        dp  = mem.dataProvider()
        dp.addAttributes(fields)
        mem.updateFields()

        feats = []
        for i in range(len(xs_sel)):
            feat = QgsFeature(fields)
            feat.setGeometry(QgsGeometry.fromPointXY(
                QgsPointXY(xs_out[i], ys_out[i])))
            for j, fd in enumerate(field_defs):
                feat[fd["name"]] = attrs_sel[i][j]
            feat["esp1"]   = float(asp_vals[i]) if not np.isnan(asp_vals[i]) else None
            feat["inc1"]   = float(slp_vals[i]) if not np.isnan(slp_vals[i]) else None
            feat["pc_mov"] = float(pc_vals[i])  if not np.isnan(pc_vals[i])  else None
            feats.append(feat)

        dp.addFeatures(feats)
        mem.updateExtents()
        self._prog(100, "Completato.")
        return mem


# ── Dialog ─────────────────────────────────────────────────────────────────────

class InSARVISDialog(QDialog):

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface    = iface
        self.task     = None
        self._closing = False
        self.setWindowTitle("InSAR VIS  v2.0")
        self.setMinimumWidth(580)
        self._build_ui()
        self._connect_signals()
        self._update_satellite_params()
        self._update_crs_note()

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setSpacing(10)

        # Input
        grp = QGroupBox("Layer di input")
        g   = QGridLayout(grp)

        g.addWidget(QLabel("Shapefile PS (punti):"), 0, 0)
        row = QHBoxLayout()
        self.cb_ps = QgsMapLayerComboBox()
        self.cb_ps.setFilters(QgsMapLayerProxyModel.PointLayer)
        row.addWidget(self.cb_ps)
        self.btn_browse_ps = QToolButton()
        self.btn_browse_ps.setText("…")
        self.btn_browse_ps.setFixedSize(QSize(28, 28))
        row.addWidget(self.btn_browse_ps)
        g.addLayout(row, 0, 1)

        g.addWidget(QLabel("Raster quote (DEM):"), 1, 0)
        row2 = QHBoxLayout()
        self.cb_dem = QgsMapLayerComboBox()
        self.cb_dem.setFilters(QgsMapLayerProxyModel.RasterLayer)
        row2.addWidget(self.cb_dem)
        self.btn_browse_dem = QToolButton()
        self.btn_browse_dem.setText("…")
        self.btn_browse_dem.setFixedSize(QSize(28, 28))
        row2.addWidget(self.btn_browse_dem)
        g.addLayout(row2, 1, 1)

        g.addWidget(QLabel("Estensione elaborazione:"), 2, 0)
        self.ext_widget = QgsExtentWidget()
        self.ext_widget.setMapCanvas(self.iface.mapCanvas())
        g.addWidget(self.ext_widget, 2, 1)

        self.lbl_crs = QLabel()
        self.lbl_crs.setWordWrap(True)
        self.lbl_crs.setStyleSheet(
            "color:#1565c0;background:#e3f2fd;"
            "border-radius:4px;padding:4px 8px;font-size:11px;")
        g.addWidget(self.lbl_crs, 3, 0, 1, 2)

        g.addWidget(QLabel("Cella ricampionamento DEM (m):"), 4, 0)
        self.spin_cell = QDoubleSpinBox()
        self.spin_cell.setRange(1, 10000)
        self.spin_cell.setValue(100)
        self.spin_cell.setDecimals(0)
        g.addWidget(self.spin_cell, 4, 1)
        main.addWidget(grp)

        # Geometria SAR
        grp2 = QGroupBox("Geometria di acquisizione SAR")
        v    = QVBoxLayout(grp2)

        orb = QHBoxLayout()
        orb.addWidget(QLabel("Orbita:"))
        self.rb_asc  = QRadioButton("Ascendente")
        self.rb_desc = QRadioButton("Discendente")
        self.rb_asc.setChecked(True)
        self.orb_group = QButtonGroup()
        self.orb_group.addButton(self.rb_asc)
        self.orb_group.addButton(self.rb_desc)
        orb.addWidget(self.rb_asc)
        orb.addWidget(self.rb_desc)
        orb.addStretch()
        v.addLayout(orb)

        sat = QHBoxLayout()
        sat.addWidget(QLabel("Satellite:"))
        self.cb_sat = QComboBox()
        for name, data in SATELLITES.items():
            b = data["band"]
            self.cb_sat.addItem(
                name + "  [" + b + "-band]" if b != "-" else name,
                userData=name)
        sat.addWidget(self.cb_sat, 1)
        v.addLayout(sat)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        v.addWidget(sep)

        pg = QGridLayout()
        pg.addWidget(QLabel("Azimut (track angle, gradi):"), 0, 0)
        self.spin_azimut = QDoubleSpinBox()
        self.spin_azimut.setRange(-360, 360)
        self.spin_azimut.setDecimals(1)
        self.spin_azimut.setValue(-13)
        pg.addWidget(self.spin_azimut, 0, 1)

        pg.addWidget(QLabel("Off-nadir (incidence angle, gradi):"), 1, 0)
        self.spin_offnadir = QDoubleSpinBox()
        self.spin_offnadir.setRange(0, 90)
        self.spin_offnadir.setDecimals(1)
        self.spin_offnadir.setValue(39)
        pg.addWidget(self.spin_offnadir, 1, 1)

        self.lbl_info = QLabel()
        self.lbl_info.setStyleSheet(
            "color:#555;font-style:italic;font-size:10px;")
        self.lbl_info.setWordWrap(True)
        pg.addWidget(self.lbl_info, 2, 0, 1, 2)
        v.addLayout(pg)
        main.addWidget(grp2)

        # Output
        grp3 = QGroupBox("Layer di output (in memoria)")
        g3   = QGridLayout(grp3)
        g3.addWidget(QLabel("Nome layer:"), 0, 0)
        self.le_pc = QLineEdit("calcolo_pc_rilevata")
        g3.addWidget(self.le_pc, 0, 1)
        main.addWidget(grp3)

        # Progress
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setVisible(False)
        main.addWidget(self.progress)

        self.lbl_step = QLabel("")
        self.lbl_step.setStyleSheet("color:#666;font-size:11px;")
        main.addWidget(self.lbl_step)

        # Bottoni
        bl = QHBoxLayout()
        self.btn_run = QPushButton("Esegui calcolo")
        self.btn_run.setMinimumHeight(32)
        f = QFont(); f.setBold(True)
        self.btn_run.setFont(f)
        self.btn_run.setStyleSheet(
            "QPushButton{background:#4CAF50;color:white;border-radius:4px;}"
            "QPushButton:hover{background:#45a049;}"
            "QPushButton:disabled{background:#aaa;}")
        self.btn_cancel = QPushButton("Annulla")
        self.btn_cancel.setMinimumHeight(32)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setStyleSheet(
            "QPushButton{background:#e53935;color:white;border-radius:4px;}"
            "QPushButton:hover{background:#c62828;}")
        btn_close = QPushButton("Chiudi")
        btn_close.setMinimumHeight(32)
        btn_close.clicked.connect(self.close)
        bl.addWidget(self.btn_run)
        bl.addWidget(self.btn_cancel)
        bl.addWidget(btn_close)
        main.addLayout(bl)

    def _connect_signals(self):
        self.cb_sat.currentIndexChanged.connect(self._update_satellite_params)
        self.rb_asc.toggled.connect(self._update_satellite_params)
        self.cb_ps.layerChanged.connect(self._update_crs_note)
        self.cb_dem.layerChanged.connect(self._update_crs_note)
        self.btn_browse_ps.clicked.connect(self._browse_ps)
        self.btn_browse_dem.clicked.connect(self._browse_dem)
        self.btn_run.clicked.connect(self._run)
        self.btn_cancel.clicked.connect(self._cancel)

    def _browse_ps(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Seleziona shapefile PS", "", PS_FILTERS)
        if path:
            try:
                self.cb_ps.setLayer(_load_and_add_layer(path, False))
                self._update_crs_note()
            except RuntimeError as e:
                QMessageBox.warning(self, "Errore", str(e))

    def _browse_dem(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Seleziona raster DEM", "", DEM_FILTERS)
        if path:
            try:
                self.cb_dem.setLayer(_load_and_add_layer(path, True))
                self._update_crs_note()
            except RuntimeError as e:
                QMessageBox.warning(self, "Errore", str(e))

    def _orbit(self):
        return "ASC" if self.rb_asc.isChecked() else "DESC"

    def _update_satellite_params(self):
        key    = self.cb_sat.currentData()
        orbit  = self._orbit()
        custom = (key == "Personalizzato")
        self.spin_azimut.setEnabled(custom)
        self.spin_offnadir.setEnabled(custom)
        if not custom and key in SATELLITES:
            p = SATELLITES[key][orbit]
            self.spin_azimut.setValue(p["azimut"])
            self.spin_offnadir.setValue(p["off_nadir"])
            self.lbl_info.setText(
                "Banda " + SATELLITES[key]["band"] + " - " +
                ("Ascendente" if orbit == "ASC" else "Discendente") +
                " - Azimut " + str(p["azimut"]) +
                "  Off-nadir " + str(p["off_nadir"]) +
                "  (valori medi - verifica i metadati del tuo dataset)")
        else:
            self.lbl_info.setText(
                "Modalita personalizzata: off-nadir deve essere positivo (es. 39).")

    def _update_crs_note(self):
        ps  = self.cb_ps.currentLayer()
        dem = self.cb_dem.currentLayer()
        if not ps or not dem:
            self.lbl_crs.setStyleSheet(
                "color:#555;background:#f5f5f5;"
                "border-radius:4px;padding:4px 8px;font-size:11px;")
            self.lbl_crs.setText(
                "Seleziona PS e DEM per visualizzare le informazioni CRS.")
            return
        pc   = ps.crs()
        dc   = dem.crs()
        proj = QgsProject.instance().crs()
        if dc.isGeographic():
            wstr  = "EPSG:32632 (auto - DEM geografico)"
            style = ("color:#b71c1c;background:#ffebee;"
                     "border-radius:4px;padding:4px 8px;font-size:11px;")
            extra = "Attenzione: DEM in gradi, verra usato UTM 32N come CRS di lavoro."
        else:
            wstr  = dc.authid()
            style = ("color:#1565c0;background:#e3f2fd;"
                     "border-radius:4px;padding:4px 8px;font-size:11px;")
            extra = ""
        note = "  -> verranno riproiettati in " + wstr if pc.authid() != dc.authid() else ""
        lines = ["Progetto  : " + proj.authid(),
                 "PS        : " + pc.authid() + note,
                 "DEM       : " + dc.authid(),
                 "CRS lavoro: " + wstr]
        if extra:
            lines.append(extra)
        self.lbl_crs.setStyleSheet(style)
        self.lbl_crs.setText("\n".join(lines))

    def _run(self):
        ps  = self.cb_ps.currentLayer()
        dem = self.cb_dem.currentLayer()

        if not ps:
            QMessageBox.warning(self, "Attenzione",
                "Seleziona uno shapefile PS valido.\n"
                "Usa il pulsante ... per caricarlo da disco.")
            return
        if not dem:
            QMessageBox.warning(self, "Attenzione",
                "Seleziona un raster DEM valido.\n"
                "Usa il pulsante ... per caricarlo da disco.")
            return

        extent = self.ext_widget.outputExtent()
        if not extent.isFinite() or extent.isEmpty():
            QMessageBox.warning(self, "Attenzione",
                "Definisci un estensione di elaborazione valida.")
            return

        on = self.spin_offnadir.value()
        if on <= 0:
            QMessageBox.warning(self, "Attenzione",
                "L off-nadir deve essere positivo (es. 39).")
            return

        dem_path = dem.source().split("|")[0]
        ps_uri   = ps.source()
        ps_path_only = ps_uri.split("|")[0]

        if not os.path.isfile(dem_path):
            QMessageBox.warning(self, "Attenzione",
                "Impossibile trovare il file DEM su disco:\n" + dem_path)
            return
        if not os.path.isfile(ps_path_only):
            QMessageBox.warning(self, "Attenzione",
                "Impossibile trovare il file PS su disco:\n" + ps_path_only)
            return

        dem_crs  = dem.crs()
        ps_crs   = ps.crs()
        work_crs = QgsCoordinateReferenceSystem("EPSG:32632") \
                   if dem_crs.isGeographic() else dem_crs
        proj_crs = QgsProject.instance().crs()
        extent_w = _extent_to_crs(extent, proj_crs, work_crs)

        cell = int(self.spin_cell.value())

        # Avviso se estensione troppo piccola
        nx = (extent_w.xMaximum() - extent_w.xMinimum()) / cell
        ny = (extent_w.yMaximum() - extent_w.yMinimum()) / cell
        if nx < 5 or ny < 5:
            QMessageBox.warning(self, "Attenzione",
                "L estensione di elaborazione e troppo piccola rispetto "
                "alla cella di ricampionamento (" + str(cell) + " m).\n\n"
                "Larghezza: " + str(round(extent_w.xMaximum()-extent_w.xMinimum(),0)) +
                " m (" + str(round(nx,1)) + " celle)\n"
                "Altezza:   " + str(round(extent_w.yMaximum()-extent_w.yMinimum(),0)) +
                " m (" + str(round(ny,1)) + " celle)\n\n"
                "Aumenta l estensione oppure riduci la cella di ricampionamento.\n"
                "Si consiglia almeno 10x10 celle.")
            return

        params = {
            "ps_path":       ps_uri,
            "ps_crs_wkt":    ps_crs.toWkt(),
            "ps_crs_id":     ps_crs.authid(),
            "dem_path":      dem_path,
            "dem_crs_wkt":   dem_crs.toWkt(),
            "xmin": extent_w.xMinimum(), "ymin": extent_w.yMinimum(),
            "xmax": extent_w.xMaximum(), "ymax": extent_w.yMaximum(),
            "work_crs_wkt":  work_crs.toWkt(),
            "work_crs_id":   work_crs.authid(),
            "cell_size":     cell,
            "azimut":        self.spin_azimut.value(),
            "off_nadir":     on,
            "output_name":   self.le_pc.text(),
        }

        self.btn_run.setVisible(False)
        self.btn_cancel.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.lbl_step.setText("Avvio elaborazione...")

        self.task = InSARTask(
            params,
            on_done      = self._on_finished,
            on_error     = self._on_error,
            on_cancelled = self._on_cancelled,
        )
        self.task.stepProgress.connect(self._on_progress)
        QgsApplication.taskManager().addTask(self.task)

    def _cancel(self):
        if self.task:
            self.btn_cancel.setEnabled(False)
            self.btn_cancel.setText("Annullamento...")
            self.task.cancel()

    def _on_progress(self, pct, msg):
        if self._closing: return
        self.progress.setValue(pct)
        self.lbl_step.setText(msg)

    def _on_finished(self, result):
        self.task = None
        if self._closing: return
        self._reset_ui()
        def _add_layer():
            _apply_qml(result)
            QgsProject.instance().addMapLayer(result)
            QMessageBox.information(self, "Completato",
                "Calcolo completato con successo!\n\n"
                "Satellite  : " + self.cb_sat.currentData() + "\n"
                "Orbita     : " + ("Ascendente" if self._orbit()=="ASC" else "Discendente") + "\n"
                "Azimut     : " + str(self.spin_azimut.value()) + "\n"
                "Off-nadir  : " + str(self.spin_offnadir.value()) + "\n\n"
                "Layer aggiunto al progetto:\n  - " + self.le_pc.text() +
                "  (campo: pc_mov)")
        QTimer.singleShot(0, _add_layer)

    def _on_error(self, msg):
        self.task = None
        if self._closing: return
        self._reset_ui()
        QMessageBox.critical(self, "Errore",
            "Errore durante l elaborazione:\n" + msg)

    def _on_cancelled(self):
        self.task = None
        if self._closing: return
        self._reset_ui()
        self.lbl_step.setText("Elaborazione annullata.")

    def _reset_ui(self):
        self.btn_run.setVisible(True)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.setText("Annulla")
        self.progress.setVisible(False)
        self.progress.setValue(0)

    def closeEvent(self, event):
        if self.task:
            reply = QMessageBox.question(
                self, "Elaborazione in corso",
                "Un elaborazione e in corso. Annullarla e chiudere?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self._closing = True
                self.task.cancel()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()
