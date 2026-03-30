"""


InSAR TS – Analisi 6: Rilevamento anomalie temporali
=====================================================
Identifica le date di acquisizione anomale nella serie media dei PS
coerenti prima che l'utente interpreti i breakpoint della piecewise.

Un'acquisizione è anomala se il suo residuo dal modello lineare supera
una soglia configurabile (default: 3σ dei residui), oppure se la
variazione tra acquisizioni consecutive supera un'altra soglia.

Output:
- Grafico della serie media con acquisizioni anomale evidenziate in rosso
- Lista delle date anomale con valore del residuo
- Avviso se un breakpoint potenziale coincide con un'acquisizione anomala
"""

import re
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import mplcursors
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QSpinBox, QDialogButtonBox, QMessageBox
)
from qgis.utils import iface

plt.close('all')

MIN_PS = 3


# ── Dialogo parametri ─────────────────────────────────────────────────────────

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

class AnomalieDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('InSAR TS – Parametri rilevamento anomalie')
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)

        lbl = QLabel(
            'Configura i criteri per il rilevamento delle acquisizioni anomale.\n'
            'Un\'acquisizione è segnalata se il suo residuo supera la soglia σ\n'
            'oppure se la variazione rispetto alla precedente supera la soglia Δ.'
        )
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        # Soglia correlazione
        row0 = QHBoxLayout()
        row0.addWidget(QLabel('Soglia correlazione PS:'))
        self.sp_soglia = QDoubleSpinBox()
        self.sp_soglia.setRange(0.0, 1.0)
        self.sp_soglia.setSingleStep(0.05)
        self.sp_soglia.setDecimals(2)
        self.sp_soglia.setValue(0.85)
        row0.addWidget(self.sp_soglia)
        layout.addLayout(row0)

        # Soglia residui
        row1 = QHBoxLayout()
        row1.addWidget(QLabel('Soglia residui (multiplo di σ):'))
        self.sp_nsigma = QDoubleSpinBox()
        self.sp_nsigma.setRange(1.0, 5.0)
        self.sp_nsigma.setSingleStep(0.5)
        self.sp_nsigma.setDecimals(1)
        self.sp_nsigma.setValue(3.0)
        row1.addWidget(self.sp_nsigma)
        layout.addLayout(row1)

        # Soglia variazione consecutiva
        row2 = QHBoxLayout()
        row2.addWidget(QLabel('Soglia variazione consecutiva (mm):'))
        self.sp_delta = QDoubleSpinBox()
        self.sp_delta.setRange(0.5, 50.0)
        self.sp_delta.setSingleStep(0.5)
        self.sp_delta.setDecimals(1)
        self.sp_delta.setValue(5.0)
        row2.addWidget(self.sp_delta)
        layout.addLayout(row2)

        btn = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn.accepted.connect(self.accept)
        btn.rejected.connect(self.reject)
        layout.addWidget(btn)


def corr_valid(x, y):
    mask = ~np.isnan(x) & ~np.isnan(y)
    if np.sum(mask) < 5:
        return np.nan
    return np.corrcoef(x[mask], y[mask])[0, 1]


# ── Classe principale ─────────────────────────────────────────────────────────
class AnomalieTemporali:
    def __init__(self):
        layer = iface.activeLayer()
        if not layer:
            QMessageBox.warning(None, 'InSAR TS – Layer non attivo',
                'Nessun layer PS attivo.\n\n'
                'Per attivarlo: clicca sul layer PS nel pannello Layer '
                '(evidenziato in blu), poi riavvia l\'analisi.')
            return

        feats = list(layer.selectedFeatures())
        if len(feats) == 0:
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
            QMessageBox.warning(None, 'InSAR TS – Campi data mancanti',
                'Nessun campo DYYYYMMDD trovato nel layer.\n\n'
                'I campi delle date devono avere formato DYYYYMMDD (es. D20170101).')
            return

        dlg = AnomalieDialog()
        if not dlg.exec_():
            return

        soglia_corr  = dlg.sp_soglia.value()
        soglia_sigma = dlg.sp_nsigma.value()
        soglia_delta = dlg.sp_delta.value()

        self.run(feats, campi_d, soglia_corr, soglia_sigma, soglia_delta)

    def run(self, feats, campi_d, soglia_corr, soglia_sigma, soglia_delta):
        records = [[f.id()] + [_qv(f[c]) for c in campi_d] for f in feats]
        df = pd.DataFrame(records, columns=['ID'] + campi_d)
        vals = df[campi_d].apply(pd.to_numeric, errors='coerce')
        n = len(df)

        # Filtro coerenza
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
            coerente = (np.nansum(corr_m >= soglia_corr, axis=1) >= n / 2)
            vals_coe = vals.loc[coerente].reset_index(drop=True)
        else:
            vals_coe = vals.copy()

        n_coe = len(vals_coe)
        n_tot = n
        if n_coe == 0:
            QMessageBox.warning(None, 'InSAR TS – Nessun PS coerente',
                'Nessun PS coerente trovato tra i punti selezionati.\n\n'
                'Prova ad abbassare la soglia di correlazione o a '
                'selezionare un\'area cinematicamente più omogenea.')
            return

        # Serie media
        arr = vals_coe.to_numpy(dtype=float)
        media = np.nanmean(arr, axis=0)
        sigma_ps = np.nanstd(arr, axis=0)

        date = pd.to_datetime([c[1:] for c in campi_d], format='%Y%m%d')
        t = np.array([(d - date[0]).days / 365.25 for d in date])

        # Modello lineare
        mask = ~np.isnan(media)
        a, b = np.polyfit(t[mask], media[mask], 1)
        trend = a * t + b
        residui = media - trend
        r2 = np.corrcoef(t[mask], media[mask])[0, 1] ** 2

        # Soglia residui
        std_res = np.nanstd(residui)
        soglia_res = soglia_sigma * std_res

        # Criterio 1: residuo > soglia_sigma * std
        anom_res = np.abs(residui) > soglia_res

        # Criterio 2: variazione consecutiva > soglia_delta
        delta = np.full(len(media), np.nan)
        delta[1:] = np.abs(np.diff(media))
        anom_delta = delta > soglia_delta

        # Unione criteri
        anomalie = anom_res | anom_delta

        n_anom = np.sum(anomalie)

        self.plot(date, t, media, sigma_ps, trend, residui,
                  anomalie, anom_res, anom_delta,
                  a, r2, std_res, soglia_res, soglia_delta,
                  n_tot, n_coe, n_anom, soglia_sigma)

    def plot(self, date, t, media, sigma_ps, trend, residui,
             anomalie, anom_res, anom_delta,
             a, r2, std_res, soglia_res, soglia_delta,
             n_tot, n_coe, n_anom, soglia_sigma):

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8),
                                        facecolor='white',
                                        gridspec_kw={'height_ratios': [3, 1]})
        fig.suptitle(
            f'Rilevamento anomalie temporali\n'
            f'PS selezionati: {n_tot}  |  PS coerenti: {n_coe}  |  '
            f'Acquisizioni anomale: {n_anom}',
            fontsize=12, y=0.98
        )

        # ── Grafico serie media ───────────────────────────────────────────────
        ax1.fill_between(date, media - sigma_ps, media + sigma_ps,
                         alpha=0.15, color='steelblue', label='±1σ PS coerenti')
        ax1.plot(date, media, color='steelblue', lw=1.5, label='Serie media')
        ax1.plot(date, trend, color='black', lw=1.2, ls='--',
                 label=f'Trend lineare  ({a:.2f} mm/a, R²={r2:.2f})')

        # Punti normali
        mask_ok = ~anomalie & ~np.isnan(media)
        ax1.scatter(date[mask_ok], media[mask_ok],
                    c='steelblue', s=25, zorder=3, alpha=0.8)

        # Punti anomali
        if n_anom > 0:
            mask_an = anomalie & ~np.isnan(media)
            ax1.scatter(date[mask_an], media[mask_an],
                        c='#e74c3c', s=60, marker='D', zorder=4,
                        label=f'Acquisizione anomala ({n_anom})')
            # Linee verticali per evidenziare
            for d in date[mask_an]:
                ax1.axvline(d, color='#e74c3c', lw=0.8, ls=':', alpha=0.5)

        ax1.axhline(0, color='gray', lw=0.6, ls=':', alpha=0.5)
        ax1.set_ylabel('Spostamento medio (mm)')
        ax1.legend(fontsize=8, loc='best')
        ax1.set_facecolor('#f5f5f5')
        ax1.spines['top'].set_visible(False)
        ax1.spines['right'].set_visible(False)
        ax1.spines['left'].set_color('#cccccc')
        ax1.spines['bottom'].set_color('#cccccc')
        ax1.grid(True, alpha=0.3, color='#cccccc')
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
        ax1.xaxis.set_major_locator(mdates.YearLocator())

        # ── Grafico residui ───────────────────────────────────────────────────
        ax2.bar(date, residui, color='steelblue', alpha=0.5, width=20)
        ax2.axhline(0, color='black', lw=0.8)
        ax2.axhline(soglia_res, color='#e74c3c', lw=1, ls='--',
                    label=f'+{soglia_sigma}σ = {soglia_res:.2f} mm')
        ax2.axhline(-soglia_res, color='#e74c3c', lw=1, ls='--',
                    label=f'-{soglia_sigma}σ = {-soglia_res:.2f} mm')
        if n_anom > 0:
            mask_an = anomalie & ~np.isnan(media)
            ax2.bar(date[mask_an], residui[mask_an],
                    color='#e74c3c', alpha=0.8, width=20)
        ax2.set_ylabel('Residuo (mm)')
        ax2.set_xlabel('Data', labelpad=6)
        # Legenda fuori dall'area del grafico (sotto il titolo asse X)
        ax2.legend(fontsize=7, loc='upper center',
                   bbox_to_anchor=(0.5, 1.18), ncol=2,
                   framealpha=0.85, edgecolor='#cccccc')
        ax2.set_facecolor('#f5f5f5')
        ax2.spines['top'].set_visible(False)
        ax2.spines['right'].set_visible(False)
        ax2.spines['left'].set_color('#cccccc')
        ax2.spines['bottom'].set_color('#cccccc')
        ax2.grid(True, alpha=0.3, color='#cccccc')
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
        ax2.xaxis.set_major_locator(mdates.YearLocator())

        fig.autofmt_xdate(rotation=30, ha='right')
        plt.tight_layout(rect=[0, 0.04, 1, 1])

        # ── Tooltip interattivo sulla serie media ─────────────────────────
        # Converte le date in numeri matplotlib per il tooltip
        date_num = mdates.date2num(date.to_pydatetime())

        # Linea serie media — tooltip con data e spostamento
        line_media = ax1.get_lines()[0]  # prima linea = serie media
        cursor1 = mplcursors.cursor(line_media, hover=True)
        active_ann = [None]

        @cursor1.connect('add')
        def on_add(sel):
            xv, yv = sel.target
            d_str = mdates.num2date(xv).strftime('%d-%m-%Y')
            # Verifica se la data è anomala
            idx = int(round(mdates.num2date(xv).toordinal() -
                            date[0].toordinal()))
            idx_near = int(np.argmin(np.abs(date_num - xv)))
            is_anom = anomalie[idx_near] if 0 <= idx_near < len(anomalie) else False
            tag = '  ⚠ ANOMALIA' if is_anom else ''
            ann_txt = '{}{}\n{:.2f} mm'.format(d_str, tag, yv)
            sel.annotation.set_text(ann_txt)
            fc = '#fdecea' if is_anom else 'white'
            sel.annotation.get_bbox_patch().set(fc=fc, alpha=0.9)
            sel.annotation.set_visible(True)
            active_ann[0] = sel.annotation
            fig.canvas.draw_idle()

        def on_move(event):
            if active_ann[0] is not None:
                if event.inaxes != ax1:
                    active_ann[0].set_visible(False)
                    fig.canvas.draw_idle()
        fig.canvas.mpl_connect('motion_notify_event', on_move)

        plt.show()

        # Ridimensiona la finestra matplotlib all'80% dello schermo disponibile
        try:
            from qgis.PyQt.QtWidgets import QApplication as _QApp
            _geo = _QApp.primaryScreen().availableGeometry()
            _mgr = plt.get_current_fig_manager()
            if hasattr(_mgr, "window"):
                _mgr.window.resize(int(_geo.width() * 0.80),
                                   int(_geo.height() * 0.80))
                _mgr.window.move(
                    int(_geo.left() + _geo.width()  * 0.10),
                    int(_geo.top()  + _geo.height() * 0.10)
                )
        except Exception:
            pass

        # Avviso in console se ci sono anomalie
        if n_anom > 0:
            date_anom = [str(d.date()) for d in pd.DatetimeIndex(date[anomalie])]
            print(f'\nInSAR TS – Anomalie temporali rilevate ({n_anom}):')
            for d, r in zip(date_anom, residui[anomalie]):
                crit = []
                if abs(r) > soglia_res:
                    crit.append(f'residuo={r:.2f} mm')
                print(f'  {d}  ({", ".join(crit)})')
            print(
                f'\n⚠  Verificare se i breakpoint identificati dalla\n'
                f'   analisi piecewise coincidono con queste date.'
            )


AnomalieTemporali()
