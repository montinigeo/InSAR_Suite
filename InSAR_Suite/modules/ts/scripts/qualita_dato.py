"""


InSAR TS – Analisi 1: Qualità del dato e omogeneità
====================================================
Sostituisce la verifica di normalità v2.2.
Obiettivo: capire se i PS selezionati sono un gruppo omogeneo e affidabile.

Analisi delle velocità lineari individuali:
- Strip plot (ogni PS visibile come punto)
- Q-Q plot
- Boxplot con outlier evidenziati
- Pannello indicatori: n, mediana, IQR, dispersione relativa, outlier (z-score robusto)
- Avviso se n < 30 (statistica descrittiva, non inferenziale)
"""

import re
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy import stats
from scipy.stats import yeojohnson, boxcox
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QDoubleSpinBox,
    QDialogButtonBox, QMessageBox, QComboBox, QLabel
)
from qgis.utils import iface

plt.close('all')

MIN_PS = 3  # minimo assoluto per avviare l'analisi


# ── Dialogo soglia correlazione ───────────────────────────────────────────────

def _qv(v):
    """Converte QVariant/NULL a float; restituisce None se NULL."""
    if v is None:
        return None
    try:
        from qgis.PyQt.QtCore import QVariant as _QVT
        if isinstance(v, _QVT):
            return None if v.isNull() else float(v.value())
    except Exception:
        pass
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

class SogliaDialog(QDialog):
    def __init__(self, parent=None, default_value=0.85):
        super().__init__(parent)
        self.setWindowTitle('Soglia di correlazione')
        self.setMinimumWidth(320)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 16, 16, 16)

        lbl = QLabel('Soglia di correlazione tra le serie PS:')
        layout.addWidget(lbl)
        self.spin = QDoubleSpinBox(self)
        self.spin.setRange(0.0, 1.0)
        self.spin.setSingleStep(0.05)
        self.spin.setDecimals(2)
        self.spin.setValue(default_value)
        self.spin.setMinimumHeight(26)
        layout.addWidget(self.spin)

        btn = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn.accepted.connect(self.accept)
        btn.rejected.connect(self.reject)
        layout.addWidget(btn)

    def getValue(self):
        return self.spin.value()


def corr_valid(x, y):
    mask = ~np.isnan(x) & ~np.isnan(y)
    if np.sum(mask) < 5:
        return np.nan
    return np.corrcoef(x[mask], y[mask])[0, 1]


def zscore_robusto(v):
    """Z-score robusto basato su mediana e MAD (non influenzato da outlier)."""
    med = np.nanmedian(v)
    mad = np.nanmedian(np.abs(v - med))
    if mad == 0:
        return np.zeros_like(v)
    return 0.6745 * (v - med) / mad


# ── Classe principale ─────────────────────────────────────────────────────────
class QualitaDato:
    def __init__(self):
        layer = iface.activeLayer()
        if not layer:
            QMessageBox.warning(None, 'InSAR TS – Layer non attivo',
            'Nessun layer PS attivo.\n\n'
            'Per attivarlo: clicca sul layer PS nel pannello Layer '
            '(evidenziato in blu), poi riavvia l\'analisi.')
            return

        feats = list(layer.selectedFeatures())
        if not feats:
            QMessageBox.warning(None, 'InSAR TS – Nessun PS selezionato!',
            'Nessun punto PS selezionato nel layer attivo.\n\n'
            'Seleziona uno o più punti PS sulla mappa con gli strumenti '
            'di selezione di QGIS, poi riavvia l\'analisi.')
            return

        if len(feats) < MIN_PS:
            QMessageBox.warning(None, 'InSAR TS – PS insufficienti',
                f'Seleziona almeno {MIN_PS} punti PS per avviare l\'analisi.\n\n'
                'Usa gli strumenti di selezione di QGIS.')
            return

        campi_d = [f.name() for f in layer.fields()
                   if re.match(r'^D\d{8}$', f.name())]
        if not campi_d:
            QMessageBox.warning(None, 'InSAR TS',
                'Nessun campo DYYYYMMDD trovato nel layer.')
            return

        # Soglia correlazione
        soglia = 0.85
        if len(feats) > 1:
            dlg = SogliaDialog(parent=iface.mainWindow())
            if not dlg.exec_():
                return  # utente ha annullato
            soglia = dlg.getValue()

        self.run(feats, campi_d, soglia)

    def run(self, feats, campi_d, soglia):
        # DataFrame valori
        records = [[f.id()] + [_qv(f[c]) for c in campi_d] for f in feats]
        df = pd.DataFrame(records, columns=['ID'] + campi_d)
        vals = df[campi_d].apply(pd.to_numeric, errors='coerce')

        n_tot = len(df)

        # Filtro coerenza
        if n_tot > 1:
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
                corr_m = np.full((n_tot, n_tot), np.nan)
            coerente = (np.nansum(corr_m >= soglia, axis=1) >= n_tot / 2)
            df_coe = df.loc[coerente].reset_index(drop=True)
            vals_coe = vals.loc[coerente].reset_index(drop=True)
        else:
            df_coe = df.copy()
            vals_coe = vals.copy()

        n_coe = len(df_coe)
        if n_coe == 0:
            QMessageBox.warning(None, 'InSAR TS – Nessun PS coerente',
                'Nessun PS coerente trovato. Prova ad abbassare la soglia di correlazione.')
            return

        # Calcola velocità per PS coerenti
        t = np.array([
            (pd.to_datetime(c[1:], format='%Y%m%d') -
             pd.to_datetime(campi_d[0][1:], format='%Y%m%d')).days / 365.25
            for c in campi_d
        ])
        vel = []
        for i in range(n_coe):
            y = vals_coe.iloc[i].values.astype(float)
            m = ~np.isnan(y)
            if np.sum(m) >= 2:
                a, _ = np.polyfit(t[m], y[m], 1)
                vel.append(a)
            else:
                vel.append(np.nan)
        vel = np.array(vel)
        vel_ok = vel[~np.isnan(vel)]

        if len(vel_ok) < MIN_PS:
            QMessageBox.warning(None, 'InSAR TS',
                f'Velocità calcolabili solo per {len(vel_ok)} PS — insufficienti.')
            return

        self.plot(vel_ok, n_tot, n_coe, soglia)

    def plot(self, vel, n_tot, n_coe, soglia):
        n = len(vel)

        # ── Statistiche robuste ───────────────────────────────────────────────
        trasf_label = ''
        vel_orig = vel.copy()  # conserva i dati originali per il ricalcolo
        med  = np.median(vel)
        q1, q3 = np.percentile(vel, [25, 75])
        iqr  = q3 - q1
        mad  = np.median(np.abs(vel - med))
        disp_rel = abs(iqr / med * 100) if med != 0 else np.nan
        zrob = zscore_robusto(vel)
        outliers = np.abs(zrob) > 2.5
        n_out = int(np.sum(outliers))

        # Shapiro-Wilk
        if n >= 8:
            W, p_sw = stats.shapiro(vel)
            sw_ok   = p_sw >= 0.05
            sw_txt  = "W={:.3f}, p={:.3f}".format(W, p_sw)
            sw_lbl  = "compatibile con normalita" if sw_ok else "normalita rifiutata"
            sw_col  = "#27ae60" if sw_ok else "#e74c3c"
        else:
            sw_txt, sw_lbl, sw_col = "n<8 — non applicato", "interpretazione visiva", "#888"

        # ── Layout: 2 righe, 3 colonne ────────────────────────────────────────
        fig = plt.figure(figsize=(13, 7), facecolor="white")
        fig.suptitle(
            "Qualita del dato e omogeneita — velocita PS\n"
            "[sel:{} | coer:{} | soglia:{:.2f}]".format(n_tot, n_coe, soglia),
            fontsize=10, color="#222222", y=0.97
        )

        gs = gridspec.GridSpec(2, 3, figure=fig,
                               hspace=0.55, wspace=0.40,
                               left=0.07, right=0.97, top=0.88, bottom=0.14)

        col_bg   = "#f5f5f5"
        col_text = "#333333"
        col_ax   = "#cccccc"

        def style_ax(ax, title):
            ax.set_facecolor(col_bg)
            ax.tick_params(colors=col_text, labelsize=8)
            ax.set_title(title, color=col_text, fontsize=9, pad=6)
            for sp in ax.spines.values():
                sp.set_edgecolor(col_ax)
            ax.xaxis.label.set_color(col_text)
            ax.yaxis.label.set_color(col_text)

        # ── 1. Istogramma con curva normale (colonna sinistra, riga 0) ──────
        ax1 = fig.add_subplot(gs[0, 0])
        rng  = np.random.default_rng(42)

        # Numero bins ottimale (regola di Sturges, minimo 5)
        n_bins = max(5, int(np.ceil(np.log2(n) + 1)))
        counts, bin_edges, patches = ax1.hist(
            vel, bins=n_bins, density=True,
            color="#3498db", alpha=0.65, edgecolor="white", lw=0.5,
            zorder=2, label="Frequenza osservata"
        )
        # Colora in rosso le barre degli outlier
        if n_out > 0:
            for patch, left in zip(patches, bin_edges[:-1]):
                right = left + (bin_edges[1] - bin_edges[0])
                if any((vel[outliers] >= left) & (vel[outliers] < right)):
                    patch.set_facecolor("#e74c3c")
                    patch.set_alpha(0.8)

        # Curva normale teorica
        mu_v  = np.mean(vel)
        std_v = np.std(vel, ddof=1)
        x_curve = np.linspace(vel.min() - std_v, vel.max() + std_v, 200)
        y_curve = stats.norm.pdf(x_curve, mu_v, std_v)
        ax1.plot(x_curve, y_curve, color="#e74c3c", lw=2, zorder=4,
                 label="Normale N({:.2f}, {:.2f})".format(mu_v, std_v))

        # Linee verticali: mediana (arancione), media (verde)
        ymax_hist = counts.max() * 1.05 if len(counts) > 0 else 1
        ax1.axvline(med,   color="#f39c12", lw=1.8, ls="--", zorder=5,
                    label="Mediana {:.3f}".format(med))
        ax1.axvline(mu_v,  color="#27ae60", lw=1.8, ls="-",  zorder=5,
                    label="Media {:.3f}".format(mu_v))
        ax1.axvline(mu_v - std_v, color="#27ae60", lw=1, ls=":", alpha=0.6, zorder=4)
        ax1.axvline(mu_v + std_v, color="#27ae60", lw=1, ls=":", alpha=0.6, zorder=4,
                    label="±1σ = {:.3f}".format(std_v))

        ax1.set_xlabel("Velocita (mm/anno)", fontsize=8)
        ax1.set_ylabel("Densita", fontsize=8)
        ax1.legend(fontsize=7, loc="upper center",
                   bbox_to_anchor=(0.5, -0.22),
                   ncol=2, framealpha=0.85, edgecolor=col_ax)
        style_ax(ax1, "Distribuzione velocita")

        # ── 2. Q-Q plot (colonna centrale, riga 0) ────────────────────────────
        ax2 = fig.add_subplot(gs[0, 1])
        norm = ~outliers  # PS non outlier — usato per colorare i punti Q-Q
        (osm, osr), (slope, intercept, r) = stats.probplot(vel, dist="norm")
        osm_arr = np.array(osm)
        ax2.scatter(osm_arr[norm], np.array(osr)[norm],
                    c="#3498db", s=22, alpha=0.85,
                    edgecolors="white", linewidths=0.3, zorder=3)
        if n_out > 0:
            ax2.scatter(osm_arr[outliers], np.array(osr)[outliers],
                        c="#e74c3c", s=35, marker="D", alpha=0.95, zorder=4,
                        edgecolors="white", linewidths=0.4)
        ax2.plot(osm_arr, slope * osm_arr + intercept,
                 color="#f39c12", lw=1.5, ls="--", zorder=2)
        ax2.set_xlabel("Quantili teorici", fontsize=8)
        ax2.set_ylabel("Quantili osservati", fontsize=8)
        style_ax(ax2, "Q-Q Plot  (R²={:.3f})".format(r**2))

        # ── 3. Boxplot (colonna destra, riga 0) ───────────────────────────────
        ax3 = fig.add_subplot(gs[0, 2])
        bp = ax3.boxplot(vel, vert=True, patch_artist=True, widths=0.45,
                         medianprops=dict(color="#f39c12", lw=2.5),
                         boxprops=dict(facecolor="#aed6f1", alpha=0.5,
                                       edgecolor="#2980b9"),
                         whiskerprops=dict(color=col_ax, lw=1.2),
                         capprops=dict(color=col_ax, lw=1.2),
                         flierprops=dict(marker="D", color="#e74c3c",
                                         markerfacecolor="#e74c3c",
                                         markersize=5, markeredgewidth=0.5,
                                         markeredgecolor="white"))
        # Sovrappone i singoli punti — blu scuro per contrasto col box azzurro
        ax3.scatter(np.ones(n) + rng.uniform(-0.15, 0.15, n), vel,
                    c="#1a5276", s=22, alpha=0.75, zorder=3,
                    edgecolors="white", linewidths=0.4)
        ax3.set_xticks([])
        ax3.set_ylabel("Velocita (mm/anno)", fontsize=8)

        # Linee orizzontali per media e std
        mu_v3  = np.mean(vel)
        std_v3 = np.std(vel, ddof=1)
        ax3.axhline(mu_v3,           color="#27ae60", lw=1.5, ls="-",
                    label="Media {:.3f} mm/a".format(mu_v3), zorder=4)
        ax3.axhline(mu_v3 + std_v3,  color="#27ae60", lw=1.0, ls=":",
                    label="±σ = {:.3f} mm/a".format(std_v3), zorder=4)
        ax3.axhline(mu_v3 - std_v3,  color="#27ae60", lw=1.0, ls=":", zorder=4)

        # Legenda boxplot sotto il grafico
        # Aggiunge handle fittizi per scatola, baffi, mediana, outlier
        from matplotlib.patches import Patch
        from matplotlib.lines import Line2D
        leg_handles = [
            Patch(facecolor="#aed6f1", edgecolor="#2980b9", alpha=0.5, label="Scatola = Q1–Q3 (IQR)"),
            Line2D([0], [0], color="#f39c12", lw=2.5,       label="Mediana"),
            Line2D([0], [0], color=col_ax,   lw=1.2,        label="Baffi = 1.5 × IQR"),
            Line2D([0], [0], color="#e74c3c", marker="D",
                   markersize=5, ls="none",                 label="Outlier Tukey"),
            Line2D([0], [0], color="#27ae60", lw=1.5,       label="Media ± 1σ"),
        ]
        ax3.legend(handles=leg_handles, fontsize=6.5,
                   loc="upper center", bbox_to_anchor=(0.5, -0.04),
                   ncol=3, framealpha=0.85, edgecolor=col_ax)
        style_ax(ax3, "Boxplot + dati individuali")

        # ── 4. Pannello statistiche (riga 1, tutta larghezza) ─────────────────
        ax4 = fig.add_subplot(gs[1, :])
        ax4.set_facecolor(col_bg)
        ax4.axis("off")
        for sp in ax4.spines.values():
            sp.set_edgecolor(col_ax)

        avviso_n = "  |  n<30: indicatori descrittivi" if n < 30 else ""
        disp_str = "—" if np.isnan(disp_rel) else "{:.1f}%".format(disp_rel)
        out_str  = "{} PS".format(n_out) if n_out == 0 else                    "{} PS: {}".format(
                       n_out,
                       ", ".join(["{:.2f}".format(v) for v in vel[outliers]])
                   )

        mu_stat  = np.mean(vel)
        std_stat = np.std(vel, ddof=1)
        righe = [
            ["n PS analizzati", str(n) + avviso_n,
             "Media", "{:.3f} mm/a".format(mu_stat),
             "Dev. standard", "{:.3f} mm/a".format(std_stat)],
            ["Mediana", "{:.3f} mm/a".format(med),
             "IQR", "{:.3f} mm/a".format(iqr),
             "MAD", "{:.3f} mm/a".format(mad)],
            ["Dispersione rel.", disp_str,
             "Outlier (z-rob>2.5)", out_str,
             "", ""],
            ["Shapiro-Wilk", sw_txt,
             "Esito", sw_lbl, "", ""],
        ]

        y0 = 0.88
        dy = 0.28
        for row in righe:
            for k in range(0, len(row), 2):
                label = row[k]
                value = row[k+1] if k+1 < len(row) else ""
                x = 0.02 + (k // 2) * 0.34
                ax4.text(x, y0, label + ":", transform=ax4.transAxes,
                         fontsize=9, color="#94a8be", va="top")
                vc = sw_col if label == "Esito" else col_text
                ax4.text(x + 0.01, y0 - 0.13, value,
                         transform=ax4.transAxes,
                         fontsize=9.5, color=vc, va="top", fontweight="bold")
            y0 -= dy

        # ── Toolbar trasformazione con ComboBox Qt incorporata nel grafico ────
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
        from qgis.PyQt.QtWidgets import (QWidget as _QW, QHBoxLayout as _QH,
                                          QPushButton as _QPB, QComboBox as _QCB,
                                          QLabel as _QL, QVBoxLayout as _QV)
        from qgis.PyQt.QtCore import Qt as _Qt

        # Finestra contenitore Qt — parent=mainWindow() + Qt.Window per renderla
        # indipendente e visibile sopra QGIS
        win = _QW(iface.mainWindow(), _Qt.Window)
        win.setWindowTitle('Qualita del dato — velocita PS')
        # Dimensiona la finestra all'80% dello schermo disponibile
        from qgis.PyQt.QtWidgets import QApplication as _QApp
        screen = _QApp.primaryScreen().availableGeometry()
        w = int(screen.width()  * 0.80)
        h = int(screen.height() * 0.80)
        win.setMinimumSize(900, 580)
        win.resize(w, h)
        win_layout = _QV(win)
        win_layout.setContentsMargins(0, 0, 0, 0)
        win_layout.setSpacing(0)

        # Barra superiore con controlli trasformazione
        bar = _QW()
        bar.setStyleSheet('background:#f0f0f0; border-bottom:1px solid #cccccc;')
        bar.setFixedHeight(38)
        bar_lay = _QH(bar)
        bar_lay.setContentsMargins(10, 4, 10, 4)
        bar_lay.setSpacing(8)

        lbl_trasf = _QL('Trasformazione velocità:')
        bar_lay.addWidget(lbl_trasf)

        cb = _QCB()
        cb.addItem('Nessuna')
        cb.addItem('Logaritmica  (solo valori positivi)')
        cb.addItem('Yeo-Johnson  (consigliata per PSI)')
        cb.addItem('Box-Cox  (solo valori positivi)')
        cb.setFixedWidth(260)
        bar_lay.addWidget(cb)

        btn_ric = _QPB('Ricalcola')
        btn_ric.setFixedWidth(90)
        btn_ric.setStyleSheet(
            'background:#2980b9; color:white; font-weight:bold;'
            'border-radius:3px; padding:3px 8px;')
        bar_lay.addWidget(btn_ric)

        btn_png = _QPB('Salva PNG')
        btn_png.setFixedWidth(90)
        btn_png.setStyleSheet(
            'background:#27ae60; color:white; font-weight:bold;'
            'border-radius:3px; padding:3px 8px;')
        bar_lay.addWidget(btn_png)
        bar_lay.addStretch()

        win_layout.addWidget(bar)

        # Canvas matplotlib
        canvas = FigureCanvasQTAgg(fig)
        win_layout.addWidget(canvas)

        # Funzione ricalcolo — ridisegna i 4 grafici con i dati trasformati
        def _ricalcola():
            idx = cb.currentIndex()
            trasf_now = ['none', 'log', 'yeojohnson', 'boxcox'][idx]
            v = vel_orig.copy()
            tl = ''
            if trasf_now == 'log':
                try:
                    v_s = vel_orig - vel_orig.min() + 1e-6  # rende positivi
                    v = np.log(v_s)
                    tl = '  [Logaritmica ln(v - min + ε)]'
                except Exception:
                    tl = '  [Logaritmica: errore]'
            elif trasf_now == 'yeojohnson':
                try:
                    v, lam = yeojohnson(vel_orig)
                    tl = '  [Yeo-Johnson λ={:.2f}]'.format(lam)
                except Exception:
                    tl = '  [Yeo-Johnson: errore]'
            elif trasf_now == 'boxcox':
                try:
                    v_s = vel_orig - vel_orig.min() + 1e-6
                    v, lam = boxcox(v_s)
                    tl = '  [Box-Cox λ={:.2f}]'.format(lam)
                except Exception:
                    tl = '  [Box-Cox: errore]'

            n_v = len(v)
            med_v  = np.median(v)
            q1_v, q3_v = np.percentile(v, [25, 75])
            iqr_v  = q3_v - q1_v
            mad_v  = np.median(np.abs(v - med_v))
            disp_v = abs(iqr_v / med_v * 100) if med_v != 0 else float('nan')
            zrob_v = zscore_robusto(v)
            out_v  = np.abs(zrob_v) > 2.5
            n_out_v = int(np.sum(out_v))

            if n_v >= 8:
                W_v, p_v = stats.shapiro(v)
                sw_t = 'W={:.3f}, p={:.3f}'.format(W_v, p_v)
                sw_c = '#27ae60' if p_v >= 0.05 else '#e74c3c'
                sw_l = 'compatibile con normalita' if p_v >= 0.05 else 'normalita rifiutata'
            else:
                sw_t, sw_c, sw_l = 'n<8 - non applicato', '#888', 'interpretazione visiva'

            rng_v = np.random.default_rng(42)
            xj_v  = rng_v.uniform(-0.2, 0.2, n_v)

            # Aggiorna istogramma (ax1)
            ax1.cla()
            ax1.set_facecolor(col_bg)
            ax1.spines['top'].set_visible(False); ax1.spines['right'].set_visible(False)
            ax1.spines['left'].set_color(col_ax); ax1.spines['bottom'].set_color(col_ax)
            n_bins_v = max(5, int(np.ceil(np.log2(n_v) + 1)))
            cnts_v, edges_v, patches_v = ax1.hist(
                v, bins=n_bins_v, density=True,
                color='#3498db', alpha=0.65, edgecolor='white', lw=0.5,
                zorder=2, label='Frequenza osservata')
            if n_out_v > 0:
                for patch, left in zip(patches_v, edges_v[:-1]):
                    right = left + (edges_v[1] - edges_v[0])
                    if any((v[out_v] >= left) & (v[out_v] < right)):
                        patch.set_facecolor('#e74c3c'); patch.set_alpha(0.8)
            mu_v1  = np.mean(v)
            std_v1 = np.std(v, ddof=1)
            xc = np.linspace(v.min() - std_v1, v.max() + std_v1, 200)
            ax1.plot(xc, stats.norm.pdf(xc, mu_v1, std_v1),
                     color='#e74c3c', lw=2, zorder=4,
                     label='Normale N({:.2f},{:.2f})'.format(mu_v1, std_v1))
            ax1.axvline(med_v,  color='#f39c12', lw=1.8, ls='--', zorder=5,
                        label='Mediana {:.3f}'.format(med_v))
            ax1.axvline(mu_v1,  color='#27ae60', lw=1.8, ls='-',  zorder=5,
                        label='Media {:.3f}'.format(mu_v1))
            ax1.axvline(mu_v1 - std_v1, color='#27ae60', lw=1, ls=':', alpha=0.6, zorder=4)
            ax1.axvline(mu_v1 + std_v1, color='#27ae60', lw=1, ls=':', alpha=0.6, zorder=4,
                        label='±1σ = {:.3f}'.format(std_v1))
            ax1.set_xlabel('Velocita (mm/anno)', fontsize=8)
            ax1.set_ylabel('Densita', fontsize=8)
            ax1.legend(fontsize=7, loc='upper center',
                       bbox_to_anchor=(0.5, -0.22),
                       ncol=2, framealpha=0.85, edgecolor=col_ax)
            style_ax(ax1, 'Distribuzione velocita')

            # Aggiorna Q-Q plot (ax2)
            ax2.cla()
            ax2.set_facecolor(col_bg)
            ax2.spines['top'].set_visible(False); ax2.spines['right'].set_visible(False)
            ax2.spines['left'].set_color(col_ax); ax2.spines['bottom'].set_color(col_ax)
            (osm_v, osr_v), (sl_v, ic_v, r_v) = stats.probplot(v, dist='norm')
            osm_a = np.array(osm_v); osr_a = np.array(osr_v)
            ax2.scatter(osm_a[~out_v], osr_a[~out_v], c='#3498db', s=22, alpha=0.85,
                        edgecolors='white', linewidths=0.3, zorder=3)
            if n_out_v > 0:
                ax2.scatter(osm_a[out_v], osr_a[out_v], c='#e74c3c', s=35, marker='D',
                            alpha=0.95, edgecolors='white', linewidths=0.4, zorder=4)
            ax2.plot(osm_a, sl_v * osm_a + ic_v, color='#f39c12', lw=1.5, ls='--', zorder=2)
            ax2.set_xlabel('Quantili teorici', fontsize=8)
            ax2.set_ylabel('Quantili osservati', fontsize=8)
            style_ax(ax2, 'Q-Q Plot  (R²={:.3f})'.format(r_v**2))

            # Aggiorna boxplot (ax3)
            ax3.cla()
            ax3.set_facecolor(col_bg)
            ax3.spines['top'].set_visible(False); ax3.spines['right'].set_visible(False)
            ax3.spines['left'].set_color(col_ax); ax3.spines['bottom'].set_color(col_ax)
            bp_v = ax3.boxplot(v, vert=True, patch_artist=True, widths=0.45,
                               medianprops=dict(color='#f39c12', lw=2.5),
                               boxprops=dict(facecolor='#aed6f1', alpha=0.5,
                                             edgecolor='#2980b9'),
                               whiskerprops=dict(color=col_ax, lw=1.2),
                               capprops=dict(color=col_ax, lw=1.2),
                               flierprops=dict(marker='D', color='#e74c3c',
                                               markerfacecolor='#e74c3c',
                                               markersize=5, markeredgewidth=0.5,
                                               markeredgecolor='white'))
            ax3.scatter(np.ones(n_v) + rng_v.uniform(-0.15, 0.15, n_v), v,
                        c='#1a5276', s=22, alpha=0.75, zorder=3,
                        edgecolors='white', linewidths=0.4)
            ax3.set_xticks([])
            ax3.set_ylabel('Velocita (mm/anno)', fontsize=8)
            from matplotlib.patches import Patch as _Pr
            from matplotlib.lines import Line2D as _L2Dr
            leg_hr = [
                _Pr(facecolor='#aed6f1', edgecolor='#2980b9', alpha=0.5, label='Scatola = Q1–Q3 (IQR)'),
                _L2Dr([0],[0], color='#f39c12', lw=2.5, label='Mediana'),
                _L2Dr([0],[0], color=col_ax, lw=1.2, label='Baffi = 1.5 × IQR'),
                _L2Dr([0],[0], color='#e74c3c', marker='D', markersize=5, ls='none', label='Outlier Tukey'),
                _L2Dr([0],[0], color='#27ae60', lw=1.5, label='Media ± 1σ'),
            ]
            ax3.legend(handles=leg_hr, fontsize=6.5,
                       loc='upper center', bbox_to_anchor=(0.5, -0.04),
                       ncol=3, framealpha=0.85, edgecolor=col_ax)
            style_ax(ax3, 'Boxplot + dati individuali')

            # Aggiorna pannello statistiche (ax4)
            ax4.cla(); ax4.set_facecolor(col_bg); ax4.axis('off')
            avv = '  |  n<30: indicatori descrittivi' if n_v < 30 else ''
            disp_s = '-' if np.isnan(disp_v) else '{:.1f}%'.format(disp_v)
            out_s  = ('{} PS: {}'.format(n_out_v,
                       ', '.join(['{:.2f}'.format(x) for x in v[out_v]]))
                      if n_out_v > 0 else '0 PS')
            mu_sr  = np.mean(v)
            std_sr = np.std(v, ddof=1)
            righe = [
                ['n PS analizzati', str(n_v) + avv,
                 'Media', '{:.3f} mm/a'.format(mu_sr),
                 'Dev. standard', '{:.3f} mm/a'.format(std_sr)],
                ['Mediana', '{:.3f} mm/a'.format(med_v),
                 'IQR', '{:.3f} mm/a'.format(iqr_v),
                 'MAD', '{:.3f} mm/a'.format(mad_v)],
                ['Dispersione rel.', disp_s,
                 'Outlier (z-rob>2.5)', out_s, '', ''],
                ['Shapiro-Wilk', sw_t, 'Esito', sw_l, '', ''],
            ]
            y0 = 0.88; dy = 0.28
            for row in righe:
                for k in range(0, len(row), 2):
                    lbl_t = row[k]; val_t = row[k+1] if k+1 < len(row) else ''
                    x = 0.02 + (k // 2) * 0.34
                    ax4.text(x, y0, lbl_t + ':', transform=ax4.transAxes,
                             fontsize=9, color='#94a8be', va='top')
                    vc = sw_c if lbl_t == 'Esito' else col_text
                    ax4.text(x + 0.01, y0 - 0.13, val_t, transform=ax4.transAxes,
                             fontsize=9.5, color=vc, va='top', fontweight='bold')
                y0 -= dy

            # Aggiorna titolo
            trasf_i = '' if trasf_now == 'none' else '  ' + tl.strip()
            fig.suptitle(
                'Qualita del dato e omogeneita - velocita PS' + trasf_i + '\n'
                '[sel:{} | coer:{} | soglia:{:.2f}]'.format(n_tot, n_coe, soglia),
                fontsize=10, color='#222222', y=0.97)
            canvas.draw()

        btn_ric.clicked.connect(_ricalcola)

        def _salva_png():
            from qgis.PyQt.QtWidgets import QFileDialog as _QFD
            percorso, _ = _QFD.getSaveFileName(
                win, "Salva grafico", "qualita_dato.png",
                "Immagini PNG (*.png);;Immagini JPEG (*.jpg)"
            )
            if percorso:
                fig.savefig(percorso, dpi=150, bbox_inches="tight",
                            facecolor="white")
        btn_png.clicked.connect(_salva_png)
        win.show()
        win.raise_()
        win.activateWindow()
        # Mantiene riferimento per evitare garbage collection
        self._win = win


QualitaDato()
