# ============================================================
# PSInSAR TS - Geostatistica
# Raster kriging caricato come layer temporaneo in QGIS
# (salvabile tramite pulsante nella finestra risultati)
# ============================================================
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QDoubleSpinBox, QTextEdit,
    QDialogButtonBox, QMessageBox, QWidget, QTabWidget,
    QFileDialog, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QGroupBox, QRadioButton, QFrame
)
from qgis.PyQt.QtCore import QVariant
from qgis.utils import iface
from qgis.core import (QgsCoordinateReferenceSystem, QgsRasterLayer,
                        QgsProject, QgsTask, QgsApplication)
from qgis.gui import QgsProjectionSelectionDialog

_active_tasks = []  # previene garbage collection dei QgsTask
import numpy as np
import pandas as pd
import re
import tempfile
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
from scipy.interpolate import griddata
from pyproj import Transformer
from pykrige.ok import OrdinaryKriging
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from osgeo import gdal, osr

# ============================================================
# DIALOGHI
# ============================================================

class SceltaCampoDialog(QDialog):
    """
    Prima finestra: l'utente sceglie il valore da interpolare con il kriging.
    Due opzioni:
      A) Velocità calcolata dalla regressione lineare sulle serie DYYYYMMDD
      B) Campo numerico già presente nel layer (es. VEL_LOS, E, U, pc_mov…)
    Utilizzo:
        dlg = SceltaCampoDialog()
        dlg.populate(layer)
        if dlg.exec_(): tipo, campo = dlg.getChoice()
    """
    def __init__(self):
        super().__init__(None)
        self.setWindowTitle('PSInSAR TS – Geostatistica: scegli il valore da interpolare')
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)

        lbl = QLabel('Seleziona il valore da utilizzare per variogramma e kriging:')
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        # ── Opzione A ─────────────────────────────────────────────────────────
        grp = QGroupBox()
        grp.setFlat(True)
        v = QVBoxLayout(grp)

        self.rb_ts = QRadioButton(
            'Velocità calcolata dalla serie storica (regressione lineare su campi DYYYYMMDD)'
        )
        self.rb_ts.setChecked(True)
        v.addWidget(self.rb_ts)

        # ── Separatore ────────────────────────────────────────────────────────
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        v.addWidget(line)

        # ── Opzione B ─────────────────────────────────────────────────────────
        self.rb_field = QRadioButton('Campo numerico del layer:')
        v.addWidget(self.rb_field)

        self.cb_field = QComboBox()
        self.cb_field.setEnabled(False)
        v.addWidget(self.cb_field)

        layout.addWidget(grp)

        self.rb_ts.toggled.connect(lambda checked: self.cb_field.setEnabled(not checked))

        btn = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn.accepted.connect(self.accept)
        btn.rejected.connect(self.reject)
        layout.addWidget(btn)

    def populate(self, layer):
        """Popola la combo con i campi numerici del layer."""
        self.cb_field.clear()
        numeric_qvariant = {
            QVariant.Int, QVariant.UInt, QVariant.LongLong, QVariant.ULongLong,
            QVariant.Double
        }
        for field in layer.fields():
            if field.type() in numeric_qvariant:
                self.cb_field.addItem(field.name())

    def getChoice(self):
        """
        Restituisce ('ts', None) oppure ('field', 'nome_campo').
        """
        if self.rb_ts.isChecked():
            return ('ts', None)
        else:
            return ('field', self.cb_field.currentText())


class ParametriVariogrammaDialog(QDialog):
    def __init__(self, parent=None, default_lags=12, default_angolo=10):
        super().__init__(parent)
        self.setWindowTitle("Parametri variogramma")
        layout = QVBoxLayout(self)
        self.lag = QDoubleSpinBox()
        self.lag.setRange(2, 40)
        self.lag.setDecimals(0)
        self.lag.setValue(default_lags)
        self.lag.setPrefix("Numero lag: ")
        layout.addWidget(self.lag)
        self.ang = QDoubleSpinBox()
        self.ang.setRange(5, 45)
        self.ang.setDecimals(0)
        self.ang.setSingleStep(5)
        self.ang.setValue(default_angolo)
        self.ang.setPrefix("Ampiezza angolare: ")
        self.ang.setSuffix(" °")
        layout.addWidget(self.ang)
        btn = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn.accepted.connect(self.accept)
        btn.rejected.connect(self.reject)
        layout.addWidget(btn)

    def getValues(self):
        return int(self.lag.value()), int(self.ang.value())


# ============================================================
# TAB CON PULSANTE DI SALVATAGGIO PNG
# ============================================================
class GraphTab(QWidget):
    def __init__(self, figure, tab_name, parent=None):
        super().__init__(parent)
        self.figure = figure
        self.tab_name = tab_name
        layout = QVBoxLayout(self)
        canvas = FigureCanvas(figure)
        layout.addWidget(canvas)
        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel(f"<b>{tab_name}</b>"))
        header_layout.addStretch()
        save_btn = QPushButton("Salva come PNG")
        save_btn.setFixedWidth(140)
        save_btn.clicked.connect(self.salva_png)
        header_layout.addWidget(save_btn)
        layout.addLayout(header_layout)
        layout.insertWidget(0, canvas)

    def salva_png(self):
        default_name = self.tab_name.replace(" ", "_").replace("/", "_") + ".png"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Salva grafico come PNG", default_name,
            "Immagini PNG (*.png);;Tutti i file (*)"
        )
        if file_path:
            if not file_path.lower().endswith('.png'):
                file_path += '.png'
            try:
                self.figure.savefig(file_path, dpi=300, bbox_inches='tight', facecolor='white')
                QMessageBox.information(self, "Salvato", f"Grafico salvato in:\n{file_path}")
            except Exception as e:
                QMessageBox.warning(self, "Errore", f"Impossibile salvare il file:\n{str(e)}")


# ============================================================
# FINESTRA OUTPUT CON TAB + PULSANTE SALVA GEOTIFF
# ============================================================
class OutputWindow(QDialog):
    def __init__(self, testo_parametri, figures_data, cv_combined_fig=None,
                 raster_params=None, parent=None):
        """
        raster_params : dict con 'grid_x', 'grid_y', 'z', 'pixel_size', 'epsg'
                        usato per il salvataggio permanente come GeoTIFF
        """
        super().__init__(parent)
        self.setWindowTitle("Risultati Kriging e Variogrammi")
        self.resize(1300, 900)
        self.raster_params = raster_params

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        for fig, name in figures_data:
            if fig is None:
                continue
            graph_tab = GraphTab(fig, name)
            tabs.addTab(graph_tab, name)

        if cv_combined_fig:
            cv_tab = GraphTab(cv_combined_fig, "Grafici CV")
            tabs.addTab(cv_tab, "Grafici CV")

        text_tab = QTextEdit()
        text_tab.setReadOnly(True)
        text_tab.setText(testo_parametri)
        tabs.insertTab(4, text_tab, "Parametri Kriging")

        # ── Barra pulsanti inferiore ──────────────────────────────────
        btn_row = QHBoxLayout()

        if raster_params is not None:
            self.btn_save_tif = QPushButton("💾  Salva GeoTIFF...")
            self.btn_save_tif.setToolTip(
                "Salva il raster kriging come file GeoTIFF permanente su disco"
            )
            self.btn_save_tif.clicked.connect(self._salva_geotiff)
            btn_row.addWidget(self.btn_save_tif)

        btn_row.addStretch()
        btn_close = QDialogButtonBox(QDialogButtonBox.Close)
        btn_close.rejected.connect(self.reject)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    def _salva_geotiff(self):
        """Apre un file dialog e scrive il GeoTIFF nella posizione scelta."""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Salva raster Kriging come GeoTIFF",
            "Kriging_Velocita.tif",
            "GeoTIFF (*.tif *.tiff);;Tutti i file (*)"
        )
        if not file_path:
            return
        if not file_path.lower().endswith(('.tif', '.tiff')):
            file_path += '.tif'
        try:
            p = self.raster_params
            _scrivi_geotiff(
                file_path,
                p['grid_x'], p['grid_y'], p['z'],
                p['pixel_size'], p['epsg']
            )
            QMessageBox.information(
                self, "GeoTIFF salvato",
                f"File salvato con successo in:\n{file_path}\n\n"
                "Per caricarlo come nuovo layer permanente usa:\n"
                "Layer > Aggiungi layer > Aggiungi layer raster..."
            )
        except Exception as e:
            QMessageBox.warning(self, "Errore salvataggio", str(e))


# ============================================================
# UTILITÀ
# ============================================================
def converti_coord(coords, crs):
    if not crs.isGeographic():
        return coords, crs.postgisSrid()
    lon, lat = coords[:, 0], coords[:, 1]
    zone = np.floor((lon + 180) / 6).astype(int) + 1
    epsg = np.where(lat >= 0, 32600 + zone, 32700 + zone)
    unique, counts = np.unique(epsg, return_counts=True)
    epsg_code = unique[np.argmax(counts)]
    lon_min, lon_max = np.min(lon), np.max(lon)
    if len(unique) > 1 or (lon_max - lon_min > 6):
        reply = QMessageBox.question(
            None, "Avvertimento",
            f"I punti coprono multiple zone UTM o un'area ampia ({lon_max - lon_min:.1f}°).\n"
            "Ciò potrebbe causare distorsioni. Procedere comunque?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.No:
            raise ValueError("Operazione annullata dall'utente a causa di area troppo ampia.")
    dlg = QgsProjectionSelectionDialog()
    dlg.setCrs(QgsCoordinateReferenceSystem(f"EPSG:{epsg_code}"))
    dlg.setMessage("Seleziona il CRS proiettato (planare) per l'analisi. Suggerito: UTM zona più comune.")
    if dlg.exec_():
        target_crs = dlg.crs()
        if not target_crs.isValid() or target_crs.isGeographic():
            raise ValueError("Seleziona un CRS proiettato valido.")
        tr = Transformer.from_crs(crs.authid(), target_crs.authid(), always_xy=True)
        x, y = tr.transform(lon, lat)
        return np.column_stack((x, y)), target_crs.postgisSrid()
    else:
        raise ValueError("Operazione annullata dall'utente.")


def max_dist(coords):
    return np.nanmax(np.linalg.norm(coords[:, None] - coords[None], axis=2))


# ============================================================
# VARIOGRAMMI SPERIMENTALI
# ============================================================
def _coppie_vettorizzate(coords, v, max_d):
    """
    Calcola tutte le coppie (distanza, semivarianza) in modo vettorizzato
    usando scipy.spatial.distance. Molto più veloce del doppio loop Python
    su dataset grandi (>500 punti). Applica campionamento casuale se n > MAX_PAIRS
    per evitare uso eccessivo di memoria.
    """
    from scipy.spatial.distance import pdist, squareform
    MAX_PAIRS = 500_000   # soglia oltre la quale si campiona

    n = len(v)
    n_pairs = n * (n - 1) // 2

    if n_pairs > MAX_PAIRS:
        # Campionamento casuale dei punti: riduce n mantenendo la copertura spaziale
        rng = np.random.default_rng(42)
        n_sample = int(np.sqrt(2 * MAX_PAIRS)) + 1
        idx = rng.choice(n, size=min(n_sample, n), replace=False)
        coords_s = coords[idx]
        v_s = v[idx]
    else:
        coords_s = coords
        v_s = v

    # Distanze tra tutte le coppie (forma condensata)
    h_all = pdist(coords_s, metric='euclidean')

    # Differenze dei valori per ogni coppia (indici upper triangle)
    i_idx, j_idx = np.triu_indices(len(v_s), k=1)
    dv = v_s[i_idx] - v_s[j_idx]
    g_all = 0.5 * dv ** 2

    # Filtro per distanza massima
    mask = h_all <= max_d
    h_all = h_all[mask]
    g_all = g_all[mask]

    # Differenze vettoriali (per variogrammi direzionali)
    dx_all = coords_s[j_idx][mask, 0] - coords_s[i_idx][mask, 0]
    dy_all = coords_s[j_idx][mask, 1] - coords_s[i_idx][mask, 1]

    return h_all, g_all, dx_all, dy_all


def semivariogramma_isotropo(coords, v, n_lags, max_d):
    h, g, _, _ = _coppie_vettorizzate(coords, v, max_d)
    bins = np.linspace(0, max_d, n_lags + 1)
    centers = 0.5 * (bins[:-1] + bins[1:])
    gamma = np.full(n_lags, np.nan)
    for k in range(n_lags):
        m = (h >= bins[k]) & (h < bins[k + 1])
        if np.any(m):
            gamma[k] = np.mean(g[m])
    return centers, gamma


def semivariogrammi_direzionali(coords, v, n_lags, max_d, ang_step):
    h_all, g_all, dx_all, dy_all = _coppie_vettorizzate(coords, v, max_d)
    ang = np.mod(np.degrees(np.arctan2(dy_all, dx_all)), 180)
    bins_ang = np.arange(0, 180 + ang_step, ang_step)
    ang_c = 0.5 * (bins_ang[:-1] + bins_ang[1:])
    bins_h = np.linspace(0, max_d, n_lags + 1)
    h_c = 0.5 * (bins_h[:-1] + bins_h[1:])
    gamma = np.full((len(ang_c), n_lags), np.nan)
    for i in range(len(ang_c)):
        m_ang = (ang >= bins_ang[i]) & (ang < bins_ang[i + 1])
        if not np.any(m_ang):
            continue
        h_dir = h_all[m_ang]
        g_dir = g_all[m_ang]
        for k in range(n_lags):
            m = (h_dir >= bins_h[k]) & (h_dir < h_c[k])
            if np.any(m):
                gamma[i, k] = np.mean(g_dir[m])
    return ang_c, h_c, gamma


# ============================================================
# MODELLI TEORICI + FIT
# ============================================================
def exp_var(h, nug, sill, r):
    return nug + sill * (1 - np.exp(-h / r))


def sph_var(h, nug, sill, r):
    return np.where(h <= r,
                    nug + sill * (1.5 * (h / r) - 0.5 * (h / r) ** 3),
                    nug + sill)


def fit_variogram(h, g):
    m = ~np.isnan(g)
    if np.sum(m) < 4:
        return None
    h, g = h[m], g[m]
    p0 = [np.min(g), np.max(g) - np.min(g), h[len(h) // 2]]
    best = None
    err = np.inf
    for name, f in {"exp": exp_var, "sph": sph_var}.items():
        try:
            p, _ = curve_fit(f, h, g, p0=p0,
                             bounds=([0, 0, 0.1], [np.inf, np.inf, np.max(h)]))
            e = np.mean((g - f(h, *p)) ** 2)
            if e < err:
                best = (name, p)
                err = e
        except:
            pass
    if best is None:
        return None
    name_map = {"exp": "exponential", "sph": "spherical"}
    return (name_map[best[0]], best[1])


# ============================================================
# SUPERFICIE POLARE E PLOT
# ============================================================
def interpola_polare(ang, lags, gamma):
    ang2 = np.concatenate([ang, ang + 180])
    g2 = np.concatenate([gamma, gamma], axis=0)
    ang2 = np.mod(ang2, 360)
    a_ext = np.concatenate([ang2 - 360, ang2, ang2 + 360])
    g_ext = np.concatenate([g2, g2, g2], axis=0)
    T, R = np.meshgrid(a_ext, lags, indexing='ij')
    Z = g_ext
    m = ~np.isnan(Z)
    pts = np.column_stack((T[m], R[m]))
    vals = Z[m]
    ti = np.linspace(0, 360, 360)
    ri = np.linspace(lags.min(), lags.max(), 200)
    Tg, Rg = np.meshgrid(ti, ri)
    Zi = griddata(pts, vals, (Tg, Rg), method="cubic")
    Zi /= np.nanmax(Zi)
    return Tg, Rg, Zi


def plot_polare(T, R, Z, ang_max, ang_min):
    fig, ax = plt.subplots(subplot_kw={"projection": "polar"}, figsize=(8, 8))
    ax.pcolormesh(np.radians(T), R, Z, shading="auto", cmap="RdYlGn_r")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    rmax = np.nanmax(R)
    ax.plot([np.radians(ang_max), np.radians(ang_max + 180)], [rmax, rmax],
            color='blue', lw=2, label=f'Massima continuità: {ang_max:.1f}°')
    ax.plot([np.radians(ang_min), np.radians(ang_min + 180)], [rmax, rmax],
            color='red', lw=2, label=f'Minima continuità: {ang_min:.1f}°')
    ax.set_title("Superficie polare normalizzata", y=1.08)
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.05), ncol=2)
    return fig


def plot_var(h, g, fit, label, angle=None):
    if fit is None:
        return None
    name, p = fit
    f = exp_var if name == "exponential" else sph_var
    hh = np.linspace(0, np.nanmax(h), 300)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(h, g, c="k")
    ax.plot(hh, f(hh, *p), "r", lw=2)
    title = f"{label}\nRange = {p[2]:.0f} m"
    if angle is not None:
        title += f" | Angolo: {angle:.1f}°"
    ax.set_title(title)
    ax.grid(alpha=0.3)
    ax.set_xlabel("Distanza h [m]")
    ax.set_ylabel("Semivarianza γ(h)")
    return fig


# ============================================================
# GRAFICO COMBINATO CROSS-VALIDATION
# ============================================================
def plot_combined_cv(residuals, observed, predicted):
    fig = plt.figure(figsize=(12, 5))
    ax1 = fig.add_subplot(1, 2, 1)
    ax1.hist(residuals, bins=15, edgecolor='black', alpha=0.7, color='skyblue')
    ax1.axvline(0, color='red', linestyle='--', lw=1.5)
    ax1.axvline(np.mean(residuals), color='orange', linestyle='-', lw=2,
                label=f'Media: {np.mean(residuals):.3f}')
    ax1.set_title("Distribuzione degli errori")
    ax1.set_xlabel("Errore (osservato - predetto) [mm/anno]")
    ax1.set_ylabel("Frequenza")
    ax1.legend()
    ax1.grid(alpha=0.3)
    ax2 = fig.add_subplot(1, 2, 2)
    min_val = min(observed.min(), predicted.min())
    max_val = max(observed.max(), predicted.max())
    ax2.scatter(observed, predicted, alpha=0.7, color='blue')
    ax2.plot([min_val, max_val], [min_val, max_val], 'r--', lw=2, label='Linea 1:1')
    ax2.set_xlabel("Valori osservati [mm/anno]")
    ax2.set_ylabel("Valori predetti [mm/anno]")
    ax2.set_title("Osservato vs Predetto")
    ax2.grid(alpha=0.3)
    ax2.legend()
    ax2.axis('equal')
    corr = np.corrcoef(observed, predicted)[0, 1]
    ax2.text(0.05, 0.95, f'R = {corr:.3f}', transform=ax2.transAxes,
             bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.8))
    fig.suptitle("Cross-Validation", fontsize=14, y=0.98)
    fig.tight_layout()
    return fig


# ============================================================
# KRIGING E CROSS-VALIDATION
# ============================================================
def descrivi_parametri_kriging(fit_iso, fit_max=None, fit_min=None,
                                anisotropy_angle=None, anisotropy_ratio=None):
    testo = ""
    if fit_max and fit_min and anisotropy_angle is not None and anisotropy_ratio is not None:
        modello, parametri = fit_iso
        testo += f"Modello variogramma: {modello}\n"
        testo += f"Nugget: {parametri[0]:.3f}\n"
        testo += f"Sill: {parametri[1]:.3f}\n"
        testo += f"Range teorico (ellittico): {parametri[2]:.3f}\n\n"
        testo += "Parametri anisotropia:\n"
        testo += f" Angolo (minima continuità): {anisotropy_angle:.1f}°\n"
        testo += f" Range min: {fit_min[1][2]:.3f}\n"
        ang_max = (anisotropy_angle + 90) % 180
        testo += f" Angolo (massima continuità): {ang_max:.1f}°\n"
        testo += f" Range max: {fit_max[1][2]:.3f}\n"
        testo += f" Rapporto range max/min: {anisotropy_ratio:.3f}\n"
    else:
        modello, parametri = fit_iso
        testo += f"Modello variogramma (isotropo): {modello}\n"
        testo += f"Nugget: {parametri[0]:.3f}\n"
        testo += f"Sill: {parametri[1]:.3f}\n"
        testo += f"Range: {parametri[2]:.3f}\n"
        testo += "Modello isotropo (nessuna anisotropia applicata)\n"
    return testo


def kriging_ordinario(coords, values, variogram_fit,
                      anisotropy_angle=None, anisotropy_ratio=None):
    x, y = coords[:, 0], coords[:, 1]
    xmin, xmax = np.min(x), np.max(x)
    ymin, ymax = np.min(y), np.max(y)
    dx = xmax - xmin
    dy = ymax - ymin
    max_side = max(dx, dy)
    num_cells = 150
    pixel_size = max_side / (num_cells - 1)
    nx = int(np.round(dx / pixel_size)) + 1
    ny = int(np.round(dy / pixel_size)) + 1
    grid_x = np.linspace(xmin, xmin + (nx - 1) * pixel_size, nx)
    grid_y = np.linspace(ymin, ymin + (ny - 1) * pixel_size, ny)
    nugget, sill, vrange = variogram_fit[1]
    OK = OrdinaryKriging(
        coords[:, 0], coords[:, 1], values,
        variogram_model=variogram_fit[0],
        variogram_parameters={'nugget': nugget, 'sill': sill, 'range': vrange},
        anisotropy_scaling=anisotropy_ratio if anisotropy_ratio and anisotropy_ratio > 0 else 1.0,
        anisotropy_angle=np.radians(anisotropy_angle) if anisotropy_angle is not None else 0.0,
        verbose=False,
        enable_plotting=False
    )
    z, ss = OK.execute('grid', grid_x, grid_y)
    return grid_x, grid_y, z, ss, pixel_size


def plot_kriging(grid_x, grid_y, z):
    fig, ax = plt.subplots(figsize=(8, 6))
    im = ax.imshow(np.flipud(z), origin='upper',
                   extent=(grid_x.min(), grid_x.max(), grid_y.min(), grid_y.max()),
                   cmap='RdYlGn')
    fig.colorbar(im, ax=ax, label="Valore interpolato [mm/anno]")
    ax.set_title("Mappa kriging ordinario")
    ax.set_xlabel("X [m]")
    ax.set_ylabel("Y [m]")
    return fig


def cross_validation_kriging(coords, values, variogram_fit,
                              anisotropy_angle=None, anisotropy_ratio=None):
    n = len(values)

    # Con molti punti la CV leave-one-out è troppo lenta (n fit di kriging).
    # Si campiona casualmente un sottoinsieme rappresentativo.
    MAX_CV = 150
    if n > MAX_CV:
        rng = np.random.default_rng(42)
        idx_cv = rng.choice(n, size=MAX_CV, replace=False)
    else:
        idx_cv = np.arange(n)

    predicted = np.full(n, np.nan)
    observed  = np.full(n, np.nan)
    for i in idx_cv:
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        coords_train = coords[mask]
        values_train = values[mask]
        val_true = values[i]
        if np.isnan(val_true):
            continue
        observed[i] = val_true
        nugget, sill, vrange = variogram_fit[1]
        OK = OrdinaryKriging(
            coords_train[:, 0], coords_train[:, 1], values_train,
            variogram_model=variogram_fit[0],
            variogram_parameters={'nugget': nugget, 'sill': sill, 'range': vrange},
            anisotropy_scaling=anisotropy_ratio if anisotropy_ratio and anisotropy_ratio > 0 else 1.0,
            anisotropy_angle=np.radians(anisotropy_angle) if anisotropy_angle is not None else 0.0,
            verbose=False,
            enable_plotting=False
        )
        z, _ = OK.execute('points', np.array([coords[i, 0]]), np.array([coords[i, 1]]))
        predicted[i] = z[0]
    valid = ~np.isnan(observed) & ~np.isnan(predicted)
    residuals       = observed[valid] - predicted[valid]
    observed_valid  = observed[valid]
    predicted_valid = predicted[valid]
    rmse       = np.sqrt(np.mean(residuals ** 2))
    mae        = np.mean(np.abs(residuals))
    bias       = np.mean(residuals)
    std_res    = np.std(residuals)
    median_res = np.median(residuals)
    max_error  = np.max(residuals)
    min_error  = np.min(residuals)
    return {
        'residuals': residuals,
        'observed':  observed_valid,
        'predicted': predicted_valid,
        'rmse': rmse, 'mae': mae, 'bias': bias,
        'std': std_res, 'median': median_res,
        'max_error': max_error, 'min_error': min_error,
        'n_points': len(residuals)
    }


# ============================================================
# FUNZIONE DI SUPPORTO: scrivi un GeoTIFF su disco
# ============================================================
def _scrivi_geotiff(path, grid_x, grid_y, z, pixel_size, epsg):
    xmin   = grid_x.min()
    ymax   = grid_y.max()
    width  = len(grid_x)
    height = len(grid_y)
    driver = gdal.GetDriverByName('GTiff')
    ds = driver.Create(path, width, height, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((xmin, pixel_size, 0, ymax, 0, -pixel_size))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.WriteArray(np.flipud(z))
    band.SetNoDataValue(np.nan)
    band.FlushCache()
    ds = None


# ============================================================
# CLASSE PRINCIPALE
# ============================================================
class GeostatisticaTask(QgsTask):
    """
    Esegue tutto il calcolo pesante (variogramma, kriging, cross-validation)
    in un thread separato per mantenere QGIS responsivo.
    I risultati vengono passati al thread principale tramite self.result.
    """
    def __init__(self, coords_m, vel, n_lags, ang_step, raster_epsg, label_valore):
        super().__init__('PSInSAR TS – Geostatistica', QgsTask.CanCancel)
        self.coords_m    = coords_m
        self.vel         = vel
        self.n_lags      = n_lags
        self.ang_step    = ang_step
        self.raster_epsg = raster_epsg
        self.label_valore = label_valore
        self.result      = None
        self.error_msg   = None

    def run(self):
        try:
            coords_m  = self.coords_m
            vel       = self.vel
            n_lags    = self.n_lags
            ang_step  = self.ang_step
            md        = max_dist(coords_m)

            # ── Variogramma isotropo ──────────────────────────────────
            h_iso, g_iso = semivariogramma_isotropo(coords_m, vel, n_lags, md)
            fit_iso = fit_variogram(h_iso, g_iso)

            # ── Variogrammi direzionali ───────────────────────────────
            ang, h_dir, g_dir = semivariogrammi_direzionali(
                coords_m, vel, n_lags, md, ang_step)

            fits, ranges = [], []
            for i in range(len(ang)):
                fit_dir = fit_variogram(h_dir, g_dir[i])
                fits.append(fit_dir)
                ranges.append(fit_dir[1][2] if fit_dir else np.nan)
            ranges = np.array(ranges)

            i_max     = np.nanargmax(ranges)
            ang_ortho = (ang[i_max] + 90) % 180
            tolleranza = ang_step / 2
            candidati_min = [i for i, a in enumerate(ang)
                             if abs((a - ang_ortho + 90) % 180 - 90) < tolleranza]
            if not candidati_min:
                candidati_min = list(range(len(ang)))
            min_ranges = [(i, ranges[i]) for i in candidati_min
                          if not np.isnan(ranges[i])]
            i_min = (min(min_ranges, key=lambda x: x[1])[0]
                     if min_ranges else (i_max + len(ang) // 2) % len(ang))

            fit_max = fits[i_max]
            fit_min = fits[i_min]

            Tg, Rg, Zg = interpola_polare(ang, h_dir, g_dir)

            if fit_max and fit_min:
                ratio            = fit_max[1][2] / fit_min[1][2]
                variogram_fit    = fit_iso
                anisotropy_angle = ang[i_min]
                anisotropy_ratio = ratio
            else:
                variogram_fit    = fit_iso
                anisotropy_angle = None
                anisotropy_ratio = None

            if not variogram_fit:
                self.error_msg = "Impossibile fittare il variogramma."
                return False

            # ── Kriging ───────────────────────────────────────────────
            grid_x, grid_y, z, ss, pixel_size = kriging_ordinario(
                coords_m, vel, variogram_fit,
                anisotropy_angle, anisotropy_ratio)

            # ── Cross-validation ──────────────────────────────────────
            cv_results = cross_validation_kriging(
                coords_m, vel, variogram_fit,
                anisotropy_angle, anisotropy_ratio)

            # ── Scrivi raster temporaneo ──────────────────────────────
            tmp_file = tempfile.NamedTemporaryFile(
                suffix='.tif', prefix='kriging_vel_', delete=False)
            tmp_path = tmp_file.name
            tmp_file.close()
            _scrivi_geotiff(tmp_path, grid_x, grid_y, z, pixel_size, self.raster_epsg)

            self.result = {
                'h_iso': h_iso, 'g_iso': g_iso, 'fit_iso': fit_iso,
                'ang': ang, 'h_dir': h_dir, 'g_dir': g_dir,
                'fits': fits, 'i_max': i_max, 'i_min': i_min,
                'fit_max': fit_max, 'fit_min': fit_min,
                'Tg': Tg, 'Rg': Rg, 'Zg': Zg,
                'grid_x': grid_x, 'grid_y': grid_y, 'z': z,
                'pixel_size': pixel_size, 'tmp_path': tmp_path,
                'cv_results': cv_results,
                'variogram_fit': variogram_fit,
                'anisotropy_angle': anisotropy_angle,
                'anisotropy_ratio': anisotropy_ratio,
            }
            return True

        except Exception as e:
            self.error_msg = str(e)
            return False

    def finished(self, result):
        if not result or self.result is None:
            QMessageBox.warning(None, 'PSInSAR TS – Geostatistica',
                f'Errore nel calcolo:\n{self.error_msg or "Errore sconosciuto"}')
            return

        r             = self.result
        label_valore  = self.label_valore
        raster_epsg   = self.raster_epsg

        # ── Carica raster in QGIS ─────────────────────────────────
        rl = QgsRasterLayer(r['tmp_path'], f"Kriging_{label_valore} [temp]")
        if rl.isValid():
            QgsProject.instance().addMapLayer(rl)
        else:
            QMessageBox.warning(None, 'PSInSAR TS – Kriging',
                'Impossibile caricare il raster temporaneo in QGIS.')

        # ── Genera grafici nel thread principale ──────────────────
        fit_iso  = r['fit_iso']
        fit_max  = r['fit_max']
        fit_min  = r['fit_min']
        ang      = r['ang']
        h_dir    = r['h_dir']
        g_dir    = r['g_dir']
        i_max    = r['i_max']
        i_min    = r['i_min']
        cv       = r['cv_results']

        fig_iso    = plot_var(r['h_iso'], r['g_iso'], fit_iso, "Variogramma Isotropo")
        fig_max    = plot_var(h_dir, g_dir[i_max], fit_max,
                              "Massima Continuità", ang[i_max])
        fig_min    = plot_var(h_dir, g_dir[i_min], fit_min,
                              "Minima Continuità", ang[i_min])
        fig_polare = plot_polare(r['Tg'], r['Rg'], r['Zg'], ang[i_max], ang[i_min])
        fig_kriging = plot_kriging(r['grid_x'], r['grid_y'], r['z'])
        fig_cv      = plot_combined_cv(cv['residuals'], cv['observed'], cv['predicted'])

        testo  = descrivi_parametri_kriging(
            fit_iso, fit_max, fit_min,
            r['anisotropy_angle'], r['anisotropy_ratio'])
        testo += "\n\n=== CROSS-VALIDATION ===\n"
        testo += f"Punti usati: {cv['n_points']}\n"
        testo += f"RMSE: {cv['rmse']:.4f} mm/anno\n"
        testo += f"MAE: {cv['mae']:.4f} mm/anno\n"
        testo += f"Bias (media errore): {cv['bias']:.4f} mm/anno\n"
        testo += f"Deviazione std errori: {cv['std']:.4f} mm/anno\n"
        testo += f"Mediana errore: {cv['median']:.4f} mm/anno\n"
        testo += f"Errore massimo: {cv['max_error']:.4f} mm/anno\n"
        testo += f"Errore minimo: {cv['min_error']:.4f} mm/anno"

        figures_data = [
            (fig_polare,  "Superficie variogramma polare"),
            (fig_iso,     "Variogramma Isotropo"),
            (fig_max,     "Massima Continuità"),
            (fig_min,     "Minima Continuità"),
            (fig_kriging, "Mappa Kriging"),
        ]
        raster_params = {
            'grid_x':     r['grid_x'],
            'grid_y':     r['grid_y'],
            'z':          r['z'],
            'pixel_size': r['pixel_size'],
            'epsg':       raster_epsg,
        }

        out_win = OutputWindow(
            testo, figures_data,
            cv_combined_fig=fig_cv,
            raster_params=raster_params,
            parent=iface.mainWindow()
        )
        out_win.exec_()


class DistribuzioneSpostamentiVelocita:
    def __init__(self):
        self.layer = iface.activeLayer()
        if not self.layer:
            QMessageBox.warning(None, 'PSInSAR TS',
                'Nessun layer PS attivo.\n'
                'Seleziona un layer PS puntuale nel pannello Layer prima di avviare l\'analisi.')
            return
        self.feat = list(self.layer.selectedFeatures())
        if not self.feat:
            QMessageBox.warning(None, 'PSInSAR TS – Nessun PS selezionato!',
                'Nessun punto PS selezionato nel layer attivo.\n\n'
                'Seleziona uno o più punti PS sulla mappa con gli strumenti di selezione di QGIS, '
                'poi avvia nuovamente l\'analisi.')
            return
        self.campi = [f.name() for f in self.layer.fields()
                      if re.match(r"^D\d{8}$", f.name())]

        dlg_campo = SceltaCampoDialog()
        dlg_campo.populate(self.layer)
        if not dlg_campo.exec_():
            return
        self.scelta_tipo, self.scelta_campo = dlg_campo.getChoice()

        if self.scelta_tipo == 'ts' and not self.campi:
            QMessageBox.warning(None, 'PSInSAR TS',
                'Nessun campo data trovato nel layer.\n'
                'I campi delle date devono avere formato DYYYYMMDD (es. D20170101).')
            return
        if self.scelta_tipo == 'field' and not self.scelta_campo:
            QMessageBox.warning(None, 'PSInSAR TS', 'Nessun campo numerico selezionato.')
            return

        self.run()

    def run(self):
        try:
            rec, coords = [], []
            for f in self.feat:
                if self.scelta_tipo == 'ts':
                    rec.append([f.id()] + [f[c] for c in self.campi])
                else:
                    rec.append([f.id()])
                p = f.geometry().asPoint()
                coords.append([p.x(), p.y()])

            if self.scelta_tipo == 'ts':
                df   = pd.DataFrame(rec, columns=["ID"] + self.campi)
                vals = df[self.campi].apply(pd.to_numeric, errors="coerce")
                t = np.array([
                    (pd.to_datetime(c[1:], format='%Y%m%d') -
                     pd.to_datetime(self.campi[0][1:], format='%Y%m%d')).days / 365.25
                    for c in self.campi
                ])
                vel = []
                for i in range(len(df)):
                    y = vals.iloc[i].values
                    m = ~np.isnan(y)
                    if np.sum(m) >= 2:
                        a, _ = np.polyfit(t[m], y[m], 1)
                        vel.append(a)
                    else:
                        vel.append(np.nan)
                vel = np.array(vel)
                label_valore = 'Velocità TS (mm/anno)'
            else:
                vel = np.array([
                    float(f[self.scelta_campo])
                    if f[self.scelta_campo] is not None else np.nan
                    for f in self.feat
                ])
                label_valore = self.scelta_campo

            coords = np.array(coords)
            coords_m, raster_epsg = converti_coord(coords, self.layer.crs())

            dlg = ParametriVariogrammaDialog()
            if dlg.exec_():
                n_lags, ang_step = dlg.getValues()
            else:
                n_lags, ang_step = 12, 10

            # ── Avvia il task in background ───────────────────────────
            task = GeostatisticaTask(
                coords_m, vel, n_lags, ang_step, raster_epsg, label_valore)
            _active_tasks.append(task)
            QgsApplication.taskManager().addTask(task)
            iface.messageBar().pushMessage(
                'PSInSAR TS – Geostatistica',
                'Calcolo avviato in background. QGIS rimane responsivo.',
                level=0, duration=6)

        except ValueError as e:
            QMessageBox.warning(None, "Errore", str(e))


# ============================================================
# AVVIO
# ============================================================
DistribuzioneSpostamentiVelocita()
