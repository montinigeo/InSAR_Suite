"""
InSAR TS - Analisi 5: Confronto tra zone
Pannello flottante non modale. Rimane aperto mentre l'utente seleziona PS.
"""

import re
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QDoubleSpinBox, QGroupBox,
    QMessageBox, QFrame
)
from qgis.PyQt.QtCore import Qt
from qgis.utils import iface

plt.close("all")

MIN_PS = 3
COLORI = ["#2980b9", "#e74c3c", "#e67e22"]
NOMI   = ["Zona A", "Zona B", "Zona C (riferimento)"]


def corr_valid(x, y):
    mask = ~np.isnan(x) & ~np.isnan(y)
    if np.sum(mask) < 5:
        return np.nan
    return np.corrcoef(x[mask], y[mask])[0, 1]


def calcola_serie_media(feats, campi_d, t, soglia):
    records = [[f.id()] + [f[c] for c in campi_d] for f in feats]
    df   = pd.DataFrame(records, columns=["ID"] + campi_d)
    vals = df[campi_d].apply(pd.to_numeric, errors="coerce")
    n    = len(df)

    if n > 1:
        # Matrice di correlazione vettorizzata — O(n*t) invece di O(n²*t)
        arr_c = vals.to_numpy(dtype=float)
        arr_c = np.where(np.isnan(arr_c), 0.0, arr_c)
        std_r = np.std(arr_c, axis=1, ddof=1)
        valid_r = std_r > 0
        if np.sum(valid_r) > 1:
            corr_m = np.corrcoef(arr_c)
            corr_m[~valid_r, :] = np.nan
            corr_m[:, ~valid_r] = np.nan
        else:
            corr_m = np.full((n, n), np.nan)
        coerente = (np.ones(n, dtype=bool) if soglia <= 0
                    else (np.nansum(corr_m >= soglia, axis=1) >= n / 2))
        vals_coe = vals.loc[coerente].reset_index(drop=True)
    else:
        vals_coe = vals.copy()

    n_coe = len(vals_coe)
    if n_coe == 0:
        return None, None, None, 0, n

    arr   = vals_coe.to_numpy(dtype=float)
    media = np.nanmean(arr, axis=0)
    sigma = np.nanstd(arr, axis=0)

    mask_t = ~np.isnan(media)
    if np.sum(mask_t) >= 2:
        a, b = np.polyfit(t[mask_t], media[mask_t], 1)
        r2   = np.corrcoef(t[mask_t], media[mask_t])[0, 1] ** 2
    else:
        a, b, r2 = np.nan, np.nan, np.nan

    return media, sigma, (a, b, r2), n_coe, n


def disegna_confronto(date, t, risultati, soglia):
    fig, ax = plt.subplots(figsize=(13, 6))
    fig.patch.set_facecolor('white')
    ax.set_facecolor('#f5f5f5')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#cccccc')
    ax.spines['bottom'].set_color('#cccccc')
    ax.tick_params(colors='#444444')
    ax.yaxis.label.set_color('#444444')
    ax.xaxis.label.set_color('#444444')
    ax.title.set_color('#222222')
    fig.suptitle("Confronto serie storiche tra zone  (soglia coerenza: {:.2f})".format(soglia), fontsize=13, y=0.98)

    for i, (nome, media, sigma, reg, n_coe, n_tot) in enumerate(risultati):
        col      = COLORI[i]
        a, b, r2 = reg
        vel_txt  = "{:.2f} mm/a, R2={:.2f}".format(a, r2) if not np.isnan(a) else "-"
        label    = "{} (n={}/{} PS)  vel={}".format(nome, n_coe, n_tot, vel_txt)

        ax.plot(date, media, color=col, lw=2, label=label)
        ax.fill_between(date, media - sigma, media + sigma,
                        color=col, alpha=0.12)
        if not np.isnan(a):
            ax.plot(date, a * t + b, color=col, lw=1.2, ls="--", alpha=0.7)

    ax.axhline(0, color="gray", lw=0.7, ls=":", alpha=0.5)
    ax.set_xlabel("Data")
    ax.set_ylabel("Spostamento medio (mm)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.xaxis.set_major_locator(mdates.YearLocator())
    fig.autofmt_xdate()
    ax.legend(fontsize=9, loc="best")
    ax.grid(True, alpha=0.25)
    plt.tight_layout()
    plt.show()
    # Ridimensiona all'80% dello schermo e porta in primo piano
    try:
        from qgis.PyQt.QtWidgets import QApplication as _QApp
        _geo = _QApp.primaryScreen().availableGeometry()
        fig_manager = plt.get_current_fig_manager()
        if hasattr(fig_manager, "window"):
            fig_manager.window.resize(int(_geo.width() * 0.80),
                                      int(_geo.height() * 0.80))
            fig_manager.window.move(
                int(_geo.left() + _geo.width()  * 0.10),
                int(_geo.top()  + _geo.height() * 0.10))
            fig_manager.window.raise_()
            fig_manager.window.activateWindow()
    except Exception:
        pass
    return fig


class ConfrontoZonePanel(QDialog):

    def __init__(self, layer, campi_d, parent=None):
        super().__init__(parent, Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("InSAR TS - Confronto tra zone")
        self.setMinimumWidth(370)

        self.layer   = layer
        self.campi_d = campi_d
        self._zone    = {}
        self._last_fig = None

        self._t = np.array([
            (pd.to_datetime(c[1:], format="%Y%m%d") -
             pd.to_datetime(campi_d[0][1:], format="%Y%m%d")).days / 365.25
            for c in campi_d
        ])
        self._date = pd.to_datetime([c[1:] for c in campi_d], format="%Y%m%d")

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        lbl_info = QLabel(
            "<b>Come usare questo pannello:</b><br>"
            "1. Seleziona i PS della <b>Zona A</b> sulla mappa<br>"
            "2. Clicca <b>Conferma Zona A</b><br>"
            "3. Seleziona i PS della <b>Zona B</b> sulla mappa<br>"
            "4. Clicca <b>Conferma Zona B</b><br>"
            "5. (Facoltativo) Zona C di riferimento<br>"
            "6. Clicca <b>Calcola confronto</b>"
        )
        lbl_info.setWordWrap(True)
        lbl_info.setStyleSheet(
            "color:#333; font-size:11px;"
            "background:#eef2f7; padding:8px; border-radius:4px;")
        layout.addWidget(lbl_info)

        row_s = QHBoxLayout()
        row_s.addWidget(QLabel("Soglia correlazione:"))
        self.sp_soglia = QDoubleSpinBox()
        self.sp_soglia.setRange(0.0, 1.0)
        self.sp_soglia.setSingleStep(0.05)
        self.sp_soglia.setDecimals(2)
        self.sp_soglia.setValue(0.85)
        row_s.addWidget(self.sp_soglia)
        layout.addLayout(row_s)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        self._labels = {}
        for key, nome, col in zip(
                ["A", "B", "C"],
                ["Zona A", "Zona B", "Zona C (riferimento - facoltativa)"],
                COLORI):
            grp = QGroupBox(nome)
            v   = QVBoxLayout(grp)

            lbl = QLabel("Non ancora confermata")
            lbl.setStyleSheet("color:#c0392b; font-weight:bold;")
            self._labels[key] = lbl
            v.addWidget(lbl)

            btn_txt = "Conferma Zona {}".format(key)
            btn = QPushButton(btn_txt)
            btn.setStyleSheet(
                "background-color:{}; color:white; font-weight:bold;"
                "border-radius:4px; padding:5px 10px;".format(col))
            btn.clicked.connect(lambda _, k=key: self._conferma(k))
            v.addWidget(btn)
            layout.addWidget(grp)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        layout.addWidget(sep2)

        self.btn_calcola = QPushButton("  Calcola confronto")
        self.btn_calcola.setEnabled(False)
        self.btn_calcola.setStyleSheet(
            "background-color:#27ae60; color:white; font-weight:bold;"
            "font-size:13px; border-radius:4px; padding:8px;")
        self.btn_calcola.clicked.connect(self._calcola)
        layout.addWidget(self.btn_calcola)

        btn_reset = QPushButton("Azzera tutto")
        btn_reset.clicked.connect(self._reset)
        layout.addWidget(btn_reset)

        btn_chiudi = QPushButton("Chiudi")
        btn_chiudi.clicked.connect(self.close)
        layout.addWidget(btn_chiudi)

    def _conferma(self, key):
        feats = list(self.layer.selectedFeatures())
        n_sel = len(feats)
        if n_sel == 0:
            QMessageBox.warning(
                self, "InSAR TS – Nessun PS selezionato",
                "Nessun punto PS selezionato nel layer attivo.\n\n"
                "Seleziona i punti PS sulla mappa con gli strumenti "
                "di selezione di QGIS, poi premi Conferma."
            )
            return
        if n_sel < MIN_PS:
            msg = "Seleziona almeno {} PS sulla mappa prima di confermare la Zona {}.\n\nPS selezionati ora: {}".format(
                MIN_PS, key, n_sel)
            QMessageBox.warning(self, "PS insufficienti", msg)
            return

        self._zone[key] = feats
        self._labels[key].setText("  {} PS confermati".format(n_sel))
        self._labels[key].setStyleSheet("color:#27ae60; font-weight:bold;")

        if "A" in self._zone and "B" in self._zone:
            self.btn_calcola.setEnabled(True)

    def closeEvent(self, event):
        """Quando il panel si chiude, porta la figura matplotlib in primo piano."""
        import matplotlib.pyplot as _plt
        try:
            if self._last_fig is not None:
                mgr = self._last_fig.canvas.manager
                if hasattr(mgr, "window"):
                    mgr.window.raise_()
                    mgr.window.activateWindow()
        except Exception:
            pass
        event.accept()

    def _reset(self):
        self._zone.clear()
        for key in ["A", "B", "C"]:
            self._labels[key].setText("Non ancora confermata")
            self._labels[key].setStyleSheet("color:#c0392b; font-weight:bold;")
        self.btn_calcola.setEnabled(False)

    def _calcola(self):
        soglia = self.sp_soglia.value()
        chiavi = ["A", "B"] + (["C"] if "C" in self._zone else [])

        risultati = []
        for i, key in enumerate(chiavi):
            nome  = NOMI[i]
            feats = self._zone[key]
            media, sigma, reg, n_coe, n_tot = calcola_serie_media(
                feats, self.campi_d, self._t, soglia)
            if media is None:
                msg = "Nessun PS coerente per {}.\nProva ad abbassare la soglia di correlazione.".format(nome)
                QMessageBox.warning(self, "Nessun PS coerente", msg)
                return
            risultati.append((nome, media, sigma, reg, n_coe, n_tot))

        self._last_fig = disegna_confronto(self._date, self._t, risultati, soglia)


# Riferimento globale per evitare garbage collection
_panel_confronto = None


def avvia():
    global _panel_confronto
    layer = iface.activeLayer()
    if not layer:
        QMessageBox.warning(None, "InSAR TS – Layer non attivo",
            "Nessun layer PS attivo.\n\n"
            "Per attivarlo: clicca sul layer PS nel pannello Layer "
            "(evidenziato in blu), poi riavvia l'analisi.")
        return
    from qgis.core import QgsVectorLayer
    if not isinstance(layer, QgsVectorLayer):
        QMessageBox.warning(None, "InSAR TS – Layer non valido",
            "Il layer attivo non e un layer vettoriale PS.\n\n"
            "Seleziona un layer PS puntuale nel pannello Layer "
            "(clicca su di esso per renderlo attivo), poi riavvia l'analisi.")
        return

    campi_d = [f.name() for f in layer.fields()
               if re.match(r"^D\d{8}$", f.name()) or re.match(r"^\d{8}$", f.name())]
    campi_d = ["D" + c if re.match(r"^\d{8}$", c) else c for c in campi_d]
    if not campi_d:
        QMessageBox.warning(None, "InSAR TS",
            "Nessun campo nel formato DYYYYMMDD o YYYYMMDD trovato nel layer.")
        return

    _panel_confronto = ConfrontoZonePanel(
        layer, campi_d, parent=iface.mainWindow())
    _panel_confronto.show()


avvia()
