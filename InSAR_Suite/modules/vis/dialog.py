import os
import math
import tempfile

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
from qgis.PyQt.QtCore import QSize, pyqtSignal, QObject, QTimer

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer,
    QgsMapLayerProxyModel,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsWkbTypes, QgsFields, QgsField, QgsFeature, QgsGeometry,
    QgsPointXY, QgsTask, QgsApplication
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


# ── Helper UI ──────────────────────────────────────────────────────────────────

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
        raise RuntimeError(f"Impossibile caricare il file:\n{path}")
    QgsProject.instance().addMapLayer(layer)
    return layer


def _extent_to_crs(extent, src_crs, dst_crs):
    if src_crs == dst_crs:
        return extent
    return QgsCoordinateTransform(
        src_crs, dst_crs, QgsProject.instance()
    ).transformBoundingBox(extent)


# ── Cuore del calcolo: GDAL Python puro ───────────────────────────────────────
# Tutto qui è thread-safe: usa solo GDAL/OGR/numpy, zero QGIS processing.

def _wgs84_to_wkt(epsg):
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    return srs.ExportToWkt()


def _crs_wkt(qgs_crs):
    """Converte QgsCoordinateReferenceSystem in WKT per GDAL."""
    return qgs_crs.toWkt()


def _reproject_points(xs, ys, src_crs_wkt, dst_crs_wkt):
    """Riproietta array di coordinate con OGR (thread-safe)."""
    src = osr.SpatialReference()
    src.ImportFromWkt(src_crs_wkt)
    dst = osr.SpatialReference()
    dst.ImportFromWkt(dst_crs_wkt)
    # GDAL >= 3: evita warning su asse Y
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(src, dst)
    pts = list(zip(xs, ys))
    results = transform.TransformPoints(pts)
    rx = np.array([r[0] for r in results])
    ry = np.array([r[1] for r in results])
    return rx, ry


def _clip_and_resample_dem(dem_path, dem_crs_wkt, work_crs_wkt,
                            xmin, ymin, xmax, ymax, cell_size,
                            cancelled_fn, progress_fn):
    """
    Clip DEM sull'estensione e ricampiona a cell_size in un unico
    gdal.Warp() in memoria. Nessun file temporaneo su disco.
    Restituisce (array_numpy, geotransform, nodata_value).
    """
    progress_fn(15, "2/5 - Clip e ricampionamento DEM...")
    if cancelled_fn(): return None

    # Calcola numero di pixel
    nx = max(1, int(round((xmax - xmin) / cell_size)))
    ny = max(1, int(round((ymax - ymin) / cell_size)))

    warp_opts = gdal.WarpOptions(
        format='MEM',
        outputBounds=(xmin, ymin, xmax, ymax),
        xRes=cell_size,
        yRes=cell_size,
        dstSRS=work_crs_wkt,
        resampleAlg=gdal.GRA_Bilinear,  # bilineare: conserva il gradiente per slope/aspect
        multithread=True,
        warpMemoryLimit=512,          # MB
        creationOptions=[],
    )
    ds = gdal.Warp('', dem_path, options=warp_opts)
    if ds is None:
        raise RuntimeError("gdal.Warp fallito sul DEM. "
                           "Verifica che il file DEM sia valido e leggibile.")

    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    arr = band.ReadAsArray().astype(np.float64)
    gt = ds.GetGeoTransform()
    ds = None
    return arr, gt, nodata


def _compute_aspect_slope(dem_arr, gt, nodata):
    """
    Calcola Aspect e Slope con differenze finite (stesso algoritmo di GDAL).
    Lavora su numpy in memoria: nessun I/O.
    """
    cell_x = abs(gt[1])
    cell_y = abs(gt[5])

    # Padding con nan per gestire i bordi
    pad = np.pad(dem_arr.astype(float), 1, mode='edge')
    if nodata is not None:
        pad[pad == nodata] = np.nan

    # Finestre 3x3 (notazione Horn)
    a = pad[:-2, :-2]; b = pad[:-2, 1:-1]; c = pad[:-2, 2:]
    d = pad[1:-1, :-2];                    f = pad[1:-1, 2:]
    g = pad[2:,  :-2]; h = pad[2:,  1:-1]; i = pad[2:,  2:]

    dz_dx = ((c + 2*f + i) - (a + 2*d + g)) / (8 * cell_x)
    dz_dy = ((g + 2*h + i) - (a + 2*b + c)) / (8 * cell_y)

    # Slope in gradi
    slope = np.degrees(np.arctan(np.sqrt(dz_dx**2 + dz_dy**2)))

    # Aspect in gradi (0=Nord, senso orario) — formula GDAL standard
    # La formula corretta è: aspect = 90 - arctan2(dz_dy, dz_dx)
    aspect = 90.0 - np.degrees(np.arctan2(dz_dy, dz_dx))
    aspect = np.where(aspect < 0.0, aspect + 360.0, aspect)

    # Aree piatte → aspect = 0 (convenzione GDAL ZERO_FLAT)
    flat = (dz_dx == 0) & (dz_dy == 0)
    aspect[flat] = 0.0

    return aspect, slope


def _sample_raster_at_points(arr, gt, xs, ys, nodata):
    """
    Campiona i valori dell'array numpy alle coordinate (xs, ys).
    Usa nearest-neighbour (coerente con la cella 100m).
    Restituisce array di float con nan dove fuori estensione o nodata.
    """
    x0, dx, _, y0, _, dy = gt
    # np.round prima di astype(int): assegna il punto al pixel il cui
    # centro e piu vicino — coerente con native:rastersampling di QGIS.
    # astype(int) senza round tronca (es. 2.9 → 2 invece di 3) e sposta
    # alcuni punti sul pixel sbagliato quando cadono vicino al bordo di cella.
    cols = np.round((xs - x0) / dx).astype(int)
    rows = np.round((ys - y0) / dy).astype(int)

    nrows, ncols = arr.shape
    valid = (cols >= 0) & (cols < ncols) & (rows >= 0) & (rows < nrows)

    values = np.full(len(xs), np.nan)
    values[valid] = arr[rows[valid], cols[valid]]

    if nodata is not None:
        values[values == nodata] = np.nan

    return values


def _calc_pc_mov(aspect_vals, slope_vals, azimut, off_nadir):
    """
    Proiezione della linea di massima pendenza sul LOS del satellite.

    Convenzioni (coerenti con GDAL e QGIS):
      - azimut φ : track angle del satellite, da Nord senso orario
                   (es. S1 ASC = -13°, S1 DESC = 190°)
      - off_nadir θ: angolo dal nadir, in gradi positivi (es. 39°)
                   coerente con la convenzione EGMS
      - aspect β  : da Nord senso orario, 0=Nord, 90=Est (conv. GDAL)
      - slope α   : gradi sessagesimali

    Assi: x=Est, y=Nord, z=Verticale su.

    Il satellite è right-looking: la direzione di osservazione orizzontale
    è φ + 90°. Il vettore LOS unitario (bersaglio → satellite) è:
        LOS = ( sin(θ)·cos(φ),  −sin(θ)·sin(φ),  cos(θ) )

    Il vettore di spostamento per gravità lungo la massima pendenza (verso il basso):
        U = ( cos(α)·sin(β),  cos(α)·cos(β),  −sin(α) )

    Prodotto scalare:
        LOS·U = sin(α)·sin(θ)·(cos(φ)·sin(β) − sin(φ)·cos(β)) − cos(α)·cos(θ)
              = sin(α)·sin(θ)·sin(β−φ) − cos(α)·cos(θ)

    pc_mov = |LOS·U| × 100
    """
    phi   = math.radians(azimut)
    theta = math.radians(off_nadir)         # positivo per convenzione EGMS

    alpha = np.radians(slope_vals)
    beta  = np.radians(aspect_vals)

    # U = (cos(α)sin(β), cos(α)cos(β), -sin(α))
    # La slope α è l'angolo con l'ORIZZONTALE, quindi:
    #   componente orizzontale del versore = cos(α)
    #   componente verticale (verso il basso) = -sin(α)
    # LOS·U = cos(α)·sin(θ)·sin(β−φ) − sin(α)·cos(θ)
    pc = np.abs(
        np.cos(alpha) * np.sin(theta) * np.sin(beta - phi)
        - np.sin(alpha) * np.cos(theta)
    ) * 100.0

    # Caso piano (slope <= 3°): terreno quasi piatto, aspect non definito con precisione.
    # Si assume movimento verticale puro (subsidenza/sollevamento):
    # pc_mov = cos(θ)  (componente verticale del LOS)
    simple = np.cos(theta) * 100.0
    pc = np.where(slope_vals <= 3, simple, pc)

    # nan dove aspect o slope non disponibili
    pc[np.isnan(aspect_vals) | np.isnan(slope_vals)] = np.nan

    return pc


# ── QgsTask ────────────────────────────────────────────────────────────────────

class InSARTask(QgsTask):
    """
    Task QGIS nativo: sicuro, non blocca la UI, annullabile.
    Usa solo GDAL/numpy — zero processing.run().
    """

    # stepProgress: emesso dal thread di background per aggiornare la progress bar
    stepProgress = pyqtSignal(int, str)
    # finished() chiama on_done/on_error/on_cancelled direttamente (main thread)

    def __init__(self, params, on_done, on_error, on_cancelled):
        super().__init__("InSAR VIS", QgsTask.CanCancel if hasattr(QgsTask, "CanCancel") else QgsTask.Flag.CanCancel)
        self.params       = params
        self._result      = None
        self._error       = None
        self._on_done      = on_done
        self._on_error     = on_error
        self._on_cancelled = on_cancelled

    # Chiamato dal task manager nel thread di background
    def run(self):
        try:
            self._result = self._process()
            return True
        except Exception as e:
            self._error = str(e)
            return False

    # Chiamato nel thread principale dopo run() — già nel main thread, no segnali
    def finished(self, result):
        if result and self._result is not None:
            self._on_done(self._result)
        elif not result:
            if self.isCanceled():
                self._on_cancelled()
            else:
                err = self._error or "Errore sconosciuto durante l'elaborazione."
                self._on_error(err)

    def _chk(self):
        if self.isCanceled():
            raise RuntimeError("__CANCELLED__")

    def _progress(self, pct, msg):
        self.setProgress(pct)
        self.stepProgress.emit(pct, msg)

    def _process(self):
        p        = self.params
        dem_path = p['dem_path']
        ps_path  = p['ps_path']
        cell     = p['cell_size']
        az       = p['azimut']
        on       = p['off_nadir']
        out_name = p['output_name']

        work_wkt    = p['work_crs_wkt']
        work_crs_id = p['work_crs_id']
        dem_wkt     = p['dem_crs_wkt']
        ps_wkt      = p['ps_crs_wkt']
        ps_crs_id   = p['ps_crs_id']
        xmin, ymin, xmax, ymax = p['xmin'], p['ymin'], p['xmax'], p['ymax']

        # ── 1. Lettura PS con OGR (thread-safe, legge direttamente il file) ───
        self._progress(8, "1/5 - Lettura PS da file...")
        self._chk()

        # ps_path può essere:
        #   "path/file.shp"                     → SHP semplice
        #   "path/file.gpkg|layername=nome"      → GPKG con layer specifico
        #   "path/file.gdb|layername=nome"       → GDB con layer specifico
        ps_uri = p['ps_path']
        if '|layername=' in ps_uri:
            ps_file, layer_name = ps_uri.split('|layername=', 1)
        else:
            ps_file, layer_name = ps_uri, None

        ogr_ds = ogr.Open(ps_file, 0)
        if ogr_ds is None:
            raise RuntimeError(f"OGR non riesce ad aprire il file PS:\n{ps_file}")

        if layer_name:
            lyr = ogr_ds.GetLayerByName(layer_name)
            if lyr is None:
                raise RuntimeError(
                    f"Layer '{layer_name}' non trovato nel file:\n{ps_file}\n\n"
                    f"Layer disponibili: {[ogr_ds.GetLayerByIndex(i).GetName() for i in range(ogr_ds.GetLayerCount())]}"
                )
        else:
            lyr = ogr_ds.GetLayer(0)

        # Campi originali
        lyr_defn   = lyr.GetLayerDefn()
        field_defs = []
        for i in range(lyr_defn.GetFieldCount()):
            fd = lyr_defn.GetFieldDefn(i)
            field_defs.append({
                'name': fd.GetName(),
                'ogr_type': fd.GetType(),
            })

        # ── Filtro spaziale OGR sull'estensione PRIMA di iterare ─────────────
        # Con 2.5M feature è indispensabile: OGR usa l'indice spaziale del GPKG
        # e restituisce solo i punti nell'area — da 2.5M a ~600 in un click.
        # L'estensione è in work_crs (es. EPSG:3003); se il PS è in un CRS
        # diverso (es. 4326) dobbiamo riproiettare il bbox prima di passarlo a OGR.
        self._progress(10, "1/5 - Filtro spaziale PS sull'estensione...")
        self._chk()

        # Ricava il bbox nel CRS del layer PS
        if ps_crs_id != work_crs_id:
            # Riproietta i 4 angoli del bbox da work_crs a ps_crs
            corners_x = np.array([xmin, xmax, xmin, xmax])
            corners_y = np.array([ymin, ymin, ymax, ymax])
            cx, cy = _reproject_points(corners_x, corners_y, work_wkt, ps_wkt)
            bbox_xmin, bbox_xmax = cx.min(), cx.max()
            bbox_ymin, bbox_ymax = cy.min(), cy.max()
        else:
            bbox_xmin, bbox_xmax = xmin, xmax
            bbox_ymin, bbox_ymax = ymin, ymax

        lyr.SetSpatialFilterRect(bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax)

        # Numero totale di feature nell'estensione (per la progress bar)
        n_tot = lyr.GetFeatureCount()   # -1 se non disponibile
        lyr.ResetReading()

        xs_list, ys_list, attrs_list = [], [], []
        for i, feat in enumerate(lyr):
            # Controlla annullamento ogni 500 feature
            if i % 500 == 0:
                if self.isCanceled():
                    raise RuntimeError("__CANCELLED__")
                if n_tot > 0:
                    pct = 10 + int(5 * i / n_tot)   # da 10% a 15% durante lettura
                    self._progress(pct, f"1/5 - Lettura PS... ({i}/{n_tot})")

            geom = feat.GetGeometryRef()
            if geom is None:
                continue
            flat = ogr.GT_Flatten(geom.GetGeometryType())
            if flat == ogr.wkbMultiPoint:
                if geom.GetGeometryCount() == 0:
                    continue
                geom = geom.GetGeometryRef(0)
            elif flat != ogr.wkbPoint:
                continue
            xs_list.append(geom.GetX())
            ys_list.append(geom.GetY())
            attrs_list.append([feat.GetField(fd['name']) for fd in field_defs])

        lyr.SetSpatialFilter(None)  # rimuovi il filtro

        if not xs_list:
            n_total = lyr.GetFeatureCount()
            ogr_ds = None
            raise RuntimeError(
                f"Nessun punto PS trovato nell'estensione selezionata.\n\n"
                f"Feature totali nel file: {n_total}\n"
                f"Bbox usato per il filtro: ({bbox_xmin:.4f}, {bbox_ymin:.4f}, "
                f"{bbox_xmax:.4f}, {bbox_ymax:.4f}) [{ps_crs_id}]\n\n"
                f"Verifica che l'estensione di elaborazione intersechi il layer PS."
            )

        ogr_ds = None   # chiudi il file

        xs_orig = np.array(xs_list, dtype=np.float64)
        ys_orig = np.array(ys_list, dtype=np.float64)

        # ── 2. Riproiezione PS nel work_crs ───────────────────────────────────
        # I punti sono già filtrati sull'estensione nel loro CRS originale,
        # ora li riproiettiamo nel work_crs per il campionamento raster.
        self._progress(18, "2/5 - Riproiezione PS nel CRS di lavoro...")
        self._chk()

        if ps_crs_id != work_crs_id:
            xs_w, ys_w = _reproject_points(xs_orig, ys_orig, ps_wkt, work_wkt)
        else:
            xs_w, ys_w = xs_orig.copy(), ys_orig.copy()

        in_ext = ((xs_w >= xmin) & (xs_w <= xmax) &
                  (ys_w >= ymin) & (ys_w <= ymax))
        if not np.any(in_ext):
            raise RuntimeError(
                "Nessun punto PS trovato nell'estensione selezionata.\n"
                "Verifica che l'estensione di elaborazione intersechi il layer PS.")

        xs_sel    = xs_w[in_ext]
        ys_sel    = ys_w[in_ext]
        xs_out    = xs_orig[in_ext]
        ys_out    = ys_orig[in_ext]
        attrs_sel = [a for a, m in zip(attrs_list, in_ext) if m]

        # ── 3. Clip + resample DEM in memoria con gdal.Warp ───────────────────
        self._chk()
        dem_arr, gt, nodata = _clip_and_resample_dem(
            dem_path, dem_wkt, work_wkt,
            xmin, ymin, xmax, ymax, cell,
            self.isCanceled, self._progress
        )

        # ── 4. Aspect e Slope in numpy ────────────────────────────────────────
        self._progress(55, "4/5 - Calcolo Aspect e Slope...")
        self._chk()
        asp_arr, slp_arr = _compute_aspect_slope(dem_arr, gt, nodata)

        # ── 5. Campionamento + calcolo pc_mov ─────────────────────────────────
        self._progress(75, "5/5 - Campionamento e calcolo pc_mov...")
        self._chk()
        asp_vals = _sample_raster_at_points(asp_arr, gt, xs_sel, ys_sel, None)
        slp_vals = _sample_raster_at_points(slp_arr, gt, xs_sel, ys_sel, None)
        pc_vals  = _calc_pc_mov(asp_vals, slp_vals, az, on)

        # ── Costruzione layer risultato in memoria ────────────────────────────
        self._progress(88, "Costruzione layer risultato...")
        self._chk()

        # Mappa tipo OGR -> QVariant
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
            qtype = OGR_TO_QVARIANT.get(fd['ogr_type'], QVariant.String)
            fields.append(QgsField(fd['name'], qtype))
        fields.append(QgsField("esp1",   QVariant.Double))
        fields.append(QgsField("inc1",   QVariant.Double))
        fields.append(QgsField("pc_mov", QVariant.Double))

        mem = QgsVectorLayer(f"Point?crs={ps_crs_id}", out_name, "memory")
        dp  = mem.dataProvider()
        dp.addAttributes(fields)
        mem.updateFields()

        feats = []
        for i in range(len(xs_sel)):
            feat = QgsFeature(fields)
            feat.setGeometry(QgsGeometry.fromPointXY(
                QgsPointXY(xs_out[i], ys_out[i])
            ))
            for j, fd in enumerate(field_defs):
                feat[fd['name']] = attrs_sel[i][j]
            feat["esp1"]   = float(asp_vals[i]) if not np.isnan(asp_vals[i]) else None
            feat["inc1"]   = float(slp_vals[i]) if not np.isnan(slp_vals[i]) else None
            feat["pc_mov"] = float(pc_vals[i])  if not np.isnan(pc_vals[i])  else None
            feats.append(feat)

        dp.addFeatures(feats)
        mem.updateExtents()

        self._progress(100, "Completato.")
        return mem




# ── Dialog ─────────────────────────────────────────────────────────────────────

class InSARVISDialog(QDialog):

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface    = iface
        self.task     = None
        self._closing = False   # True quando chiusura con task attivo
        self.setWindowTitle("InSAR VIS  v2.0")
        self.setMinimumWidth(580)
        self._build_ui()
        self._connect_signals()
        self._update_satellite_params()
        self._update_crs_note()

    # ── UI ─────────────────────────────────────────────────────────────────────

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
                f"{name}  [{b}-band]" if b != "-" else name,
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

    # ── Segnali ────────────────────────────────────────────────────────────────

    def _connect_signals(self):
        self.cb_sat.currentIndexChanged.connect(self._update_satellite_params)
        self.rb_asc.toggled.connect(self._update_satellite_params)
        self.cb_ps.layerChanged.connect(self._update_crs_note)
        self.cb_dem.layerChanged.connect(self._update_crs_note)
        self.btn_browse_ps.clicked.connect(self._browse_ps)
        self.btn_browse_dem.clicked.connect(self._browse_dem)
        self.btn_run.clicked.connect(self._run)
        self.btn_cancel.clicked.connect(self._cancel)

    # ── Browser file ───────────────────────────────────────────────────────────

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

    # ── Satellite params ───────────────────────────────────────────────────────

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
                f"Banda {SATELLITES[key]['band']} - "
                f"{'Ascendente' if orbit=='ASC' else 'Discendente'} - "
                f"Azimut {p['azimut']}  Off-nadir {p['off_nadir']}  "
                "(valori medi - verifica i metadati del tuo dataset)")
        else:
            self.lbl_info.setText(
                "Modalita personalizzata: off-nadir deve essere positivo (es. 39).")

    # ── CRS note ───────────────────────────────────────────────────────────────

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
        note = f"  -> verranno riproiettati in {wstr}" if pc != dc else ""
        lines = [f"Progetto  : {proj.authid()}",
                 f"PS        : {pc.authid()}{note}",
                 f"DEM       : {dc.authid()}",
                 f"CRS lavoro: {wstr}"]
        if extra:
            lines.append(extra)
        self.lbl_crs.setStyleSheet(style)
        self.lbl_crs.setText("\n".join(lines))

    # ── Avvio ──────────────────────────────────────────────────────────────────

    def _run(self):
        ps  = self.cb_ps.currentLayer()
        dem = self.cb_dem.currentLayer()

        if not ps:
            QMessageBox.warning(self, "Attenzione",
                "Seleziona uno shapefile PS valido.\n"
                "Usa il pulsante '...' per caricarlo da disco.")
            return
        if not dem:
            QMessageBox.warning(self, "Attenzione",
                "Seleziona un raster DEM valido.\n"
                "Usa il pulsante '...' per caricarlo da disco.")
            return

        extent = self.ext_widget.outputExtent()
        if not extent.isFinite() or extent.isEmpty():
            QMessageBox.warning(self, "Attenzione",
                "Definisci un'estensione di elaborazione valida.")
            return

        cell = self.spin_cell.value()
        proj_crs = QgsProject.instance().crs()
        work_crs = QgsCoordinateReferenceSystem("EPSG:32632") \
                   if dem.crs().isGeographic() else dem.crs()
        from qgis.core import QgsCoordinateTransform
        extent_w = _extent_to_crs(extent, proj_crs, work_crs)
        nx = (extent_w.xMaximum() - extent_w.xMinimum()) / cell
        ny = (extent_w.yMaximum() - extent_w.yMinimum()) / cell
        if nx < 5 or ny < 5:
            QMessageBox.warning(self, "Attenzione",
                f"L'estensione di elaborazione è troppo piccola rispetto alla cella di ricampionamento ({cell:.0f} m).\n\n"
                f"Larghezza: {extent_w.xMaximum()-extent_w.xMinimum():.0f} m ({nx:.1f} celle)\n"
                f"Altezza:   {extent_w.yMaximum()-extent_w.yMinimum():.0f} m ({ny:.1f} celle)\n\n"
                f"Aumenta l'estensione oppure riduci la cella di ricampionamento.\n"
                f"Si consiglia almeno 10×10 celle (es. {cell*10:.0f} m × {cell*10:.0f} m).")
            return

        on = self.spin_offnadir.value()
        if on <= 0:
            QMessageBox.warning(self, "Attenzione",
                "L'off-nadir deve essere positivo (es. 39).")
            return

        # Recupera il path fisico del DEM (serve a GDAL)
        # Path fisici su disco — usati da GDAL/OGR direttamente, senza QGIS
        dem_path = dem.source().split('|')[0]
        # URI completa del PS: può essere "path/file.gpkg|layername=nome"
        # NON splittare: OGR capisce la sintassi |layername= direttamente
        ps_uri   = ps.source()
        ps_path_only = ps_uri.split('|')[0]

        if not os.path.isfile(dem_path):
            QMessageBox.warning(self, "Attenzione",
                f"Impossibile trovare il file DEM su disco:\n{dem_path}\n\n"
                "Il DEM deve essere un file locale (GeoTIFF o IMG).")
            return

        if not os.path.isfile(ps_path_only):
            QMessageBox.warning(self, "Attenzione",
                f"Impossibile trovare il file PS su disco:\n{ps_path_only}\n\n"
                "Il layer PS deve essere un file locale (SHP o GPKG).")
            return

        dem_crs  = dem.crs()
        ps_crs   = ps.crs()
        work_crs = QgsCoordinateReferenceSystem("EPSG:32632") \
                   if dem_crs.isGeographic() else dem_crs
        proj_crs = QgsProject.instance().crs()
        extent_w = _extent_to_crs(extent, proj_crs, work_crs)

        params = {
            # Path su disco — OGR li legge nel task, senza toccare QGIS
            'ps_path':       ps_uri,  # URI completa con eventuale |layername=
            'ps_crs_wkt':    ps_crs.toWkt(),
            'ps_crs_id':     ps_crs.authid(),
            'dem_path':      dem_path,
            'dem_crs_wkt':   dem_crs.toWkt(),
            'xmin': extent_w.xMinimum(), 'ymin': extent_w.yMinimum(),
            'xmax': extent_w.xMaximum(), 'ymax': extent_w.yMaximum(),
            'work_crs_wkt':  work_crs.toWkt(),
            'work_crs_id':   work_crs.authid(),
            'cell_size':   int(self.spin_cell.value()),
            'azimut':      self.spin_azimut.value(),
            'off_nadir':   on,
            'output_name': self.le_pc.text(),
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

    # ── Annulla ────────────────────────────────────────────────────────────────

    def _cancel(self):
        if self.task:
            self.btn_cancel.setEnabled(False)
            self.btn_cancel.setText("Annullamento...")
            self.task.cancel()

    # ── Slot task ──────────────────────────────────────────────────────────────

    def _on_progress(self, pct, msg):
        if self._closing:
            return
        self.progress.setValue(pct)
        self.lbl_step.setText(msg)

    def _on_finished(self, result):
        self.task = None
        if self._closing:
            return
        self._reset_ui()
        # QTimer.singleShot(0) lascia al task manager il tempo di registrare
        # il completamento prima di eseguire addMapLayer — evita la barra blu
        # che continua a scorrere dopo la fine dell'elaborazione.
        def _add_layer():
            _apply_qml(result)
            QgsProject.instance().addMapLayer(result)
            QMessageBox.information(self, "Completato",
            f"Calcolo completato con successo!\n\n"
            f"Satellite  : {self.cb_sat.currentData()}\n"
            f"Orbita     : {'Ascendente' if self._orbit()=='ASC' else 'Discendente'}\n"
            f"Azimut     : {self.spin_azimut.value()}\n"
            f"Off-nadir  : {self.spin_offnadir.value()}\n\n"
            f"Layer aggiunto al progetto:\n  - {self.le_pc.text()}  (campo: pc_mov)")
        QTimer.singleShot(0, _add_layer)

    def _on_error(self, msg):
        self.task = None
        if self._closing:
            return
        self._reset_ui()
        QMessageBox.critical(self, "Errore",
            f"Errore durante l'elaborazione:\n{msg}")

    def _on_cancelled(self):
        self.task = None
        if self._closing:
            return
        self._reset_ui()
        self.lbl_step.setText("Elaborazione annullata.")

    # ── Reset UI ───────────────────────────────────────────────────────────────

    def _reset_ui(self):
        self.btn_run.setVisible(True)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.setText("Annulla")
        self.progress.setVisible(False)
        self.progress.setValue(0)

    # ── Chiusura ───────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self.task:
            reply = QMessageBox.question(
                self, "Elaborazione in corso",
                "Un'elaborazione è in corso. Annullarla e chiudere?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                # _closing=True: i callback non toccheranno la UI già chiusa
                self._closing = True
                self.task.cancel()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()
