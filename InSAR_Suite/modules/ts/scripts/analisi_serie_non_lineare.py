import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import os
from qgis.core import QgsTask, QgsMessageLog, Qgis, QgsApplication
from PyQt5.QtWidgets import QFileDialog, QMessageBox, QInputDialog
import mplcursors
import pwlf
from scipy.stats import linregress
import re  # Import per regex

# Registro globale per prevenire garbage collection dei task attivi


_active_tasks = []

# ================= FUNZIONE DI CORRELAZIONE =================

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

def corr_valid(x, y):
    mask = ~np.isnan(x) & ~np.isnan(y)
    if np.sum(mask) < 5:
        return np.nan
    return np.corrcoef(x[mask], y[mask])[0, 1]


# ================= MAIN =================
def main():
    soglia_corr_default = 0.85

    layer = iface.activeLayer()
    if not layer:
        QMessageBox.warning(None, 'InSAR TS – Layer non attivo',
            'Nessun layer PS attivo.\n\n'
            'Per attivarlo: clicca sul layer PS nel pannello Layer '
            '(evidenziato in blu), poi riavvia l\'analisi.')
        return
    selected_features = layer.selectedFeatures()
    if not selected_features:
        QMessageBox.warning(None, 'InSAR TS – Nessun PS selezionato!',
            'Nessun punto PS selezionato nel layer attivo.\n\n'
            'Seleziona uno o più punti PS sulla mappa con gli strumenti di selezione di QGIS, '
            'poi avvia nuovamente l\'analisi.')
        return
    num_selected = len(selected_features)

    if num_selected == 1:
        soglia_corr = soglia_corr_default
    else:
        soglia_input, ok = QInputDialog.getDouble(
            None,
            "Soglia di correlazione",
            "Inserisci la soglia di correlazione (0\u20131):",
            soglia_corr_default, 0.0, 1.0, 2
        )
        if not ok:
            return  # utente ha annullato
        soglia_corr = soglia_input

    # Chiede il numero massimo di segmenti da testare con BIC
    seg_input, ok2 = QInputDialog.getInt(
        None,
        "Numero massimo di segmenti (BIC)",
        "Numero massimo di segmenti da testare (2-5).\n"
        "Il BIC scegliera automaticamente il numero ottimale\n"
        "tra 2 e il valore scelto.\n\n"
        "Suggerimento: scegli 3 se prevedi al massimo un breakpoint.",
        5, 2, 5, 1
    )
    if not ok2:
        return
    max_seg_utente = seg_input

    campi_date = [f.name() for f in layer.fields() if re.match(r"^D\d{8}$", f.name())]
    if not campi_date:
        QMessageBox.warning(None, 'InSAR TS',
            'Nessun campo data trovato nel layer.\n'
            'I campi delle date devono avere formato DYYYYMMDD (es. D20170101).')
        return
    date = [pd.to_datetime(c[1:], format="%Y%m%d") for c in campi_date]

    records = []
    for feat in selected_features:
        code = feat["CODE"] if "CODE" in feat.fields().names() else feat.id()
        values = [_qv(feat[c]) for c in campi_date]
        records.append([code] + values)
    df = pd.DataFrame(records, columns=["CODE"] + campi_date)

    task = AnalisiCinematicaTask(
        "InSAR TS - Analisi serie non lineare",
        df, date, soglia_corr, campi_date,
        salva_excel=False, percorso_excel=None,
        n_seg_utente=max_seg_utente
    )
    _active_tasks.append(task)  # previene garbage collection
    QgsApplication.taskManager().addTask(task)


# ================= TASK QGIS =================
# ================= TASK QGIS =================
class AnalisiCinematicaTask(QgsTask):
    def __init__(self, description, df, date, soglia_corr, campi_date,
                 salva_excel, percorso_excel, n_seg_utente=0):
        super().__init__(description, QgsTask.CanCancel)
        self.df = df.copy()
        self.date = date
        self.soglia_corr = soglia_corr
        self.campi_date = campi_date
        self.salva_excel = salva_excel
        self.percorso_excel = percorso_excel
        self.n_seg_utente = n_seg_utente
        self.result = None

    def run(self):
        try:
            valori = self.df[self.campi_date].apply(pd.to_numeric, errors='coerce')
            n = len(self.df)

            if n == 1:
                ps_coerenti = self.df.copy()
                corr_df = None
                msg_info = "ℹ️ Analisi di un singolo PS."
            else:
                corr_matrix = np.full((n, n), np.nan)
                for i in range(n):
                    serie_i = valori.iloc[i].values.astype(float)
                    for j in range(i, n):
                        serie_j = valori.iloc[j].values.astype(float)
                        c = corr_valid(serie_i, serie_j)
                        corr_matrix[i, j] = corr_matrix[j, i] = c

                corr_df = pd.DataFrame(corr_matrix, columns=self.df["CODE"], index=self.df["CODE"])
                mask_valid = (corr_df >= self.soglia_corr)
                coerenti = mask_valid.sum(axis=1) >= (n / 2)
                ps_coerenti = self.df.loc[coerenti.values].reset_index(drop=True)

                if len(ps_coerenti) == 0:
                    msg_info = f"⚠️ Nessun PS coerente trovato tra {n} punti selezionati."
                    self.result = (None, None, None, None, None, None, msg_info, Qgis.Warning, False)
                    return True
                elif len(ps_coerenti) == 1:
                    msg_info = f"ℹ️ Solo 1 PS coerente trovato su {n} selezionati."
                else:
                    msg_info = f"✅ Analisi completata: trovati {len(ps_coerenti)} PS coerenti su {n} selezionati."

            serie_coerenti = ps_coerenti[self.campi_date].to_numpy(dtype=float)
            serie_media = np.nanmean(serie_coerenti, axis=0)
            df_media = pd.DataFrame({
                "data": self.date,
                "deformazione_media": serie_media
            }).dropna().reset_index(drop=True)

            # --- FIT PIECEWISE LINEARE AUTOMATICO ---
            x = mdates.date2num(df_media["data"].values)
            y = df_media["deformazione_media"].values

            pwlf_model = pwlf.PiecewiseLinFit(x, y)
            # BIC su range 2..n_seg_utente — trova il numero ottimale di segmenti
            max_segments = max(2, self.n_seg_utente)
            QgsMessageLog.logMessage(
                f"InSAR TS – Piecewise: test BIC su {max_segments} segmenti max "
                f"(n_seg_utente={self.n_seg_utente})",
                "InSAR TS", Qgis.Info
            )
            res_bic = []
            for i in range(2, max_segments + 1):
                try:
                    pwlf_model.fit(i)
                    rss = pwlf_model.rss
                    n_points = len(x)
                    k = 2 * i
                    bic = n_points * np.log(rss / n_points) + k * np.log(n_points)
                    res_bic.append((i, bic))
                except Exception:
                    res_bic.append((i, np.inf))
            best_segments = min(res_bic, key=lambda t: t[1])[0] if res_bic else 2
            QgsMessageLog.logMessage(
                f"InSAR TS – Piecewise: BIC ha scelto {best_segments} segmenti "
                f"su {max_segments} testati. BIC scores: {[(s, round(b,1)) for s,b in res_bic]}",
                "InSAR TS", Qgis.Info
            )
            pwlf_model.fit(best_segments)
            breaks = pwlf_model.fit_breaks
            slopes = pwlf_model.slopes
            intercepts = pwlf_model.intercepts

            segmenti = []
            for i in range(best_segments):
                start_date = mdates.num2date(breaks[i]).strftime("%Y-%m-%d")
                end_date = mdates.num2date(breaks[i + 1]).strftime("%Y-%m-%d")
                segmenti.append({
                    "segmento": i + 1,
                    "data_inizio": start_date,
                    "data_fine": end_date,
                    "pendenza": slopes[i],
                    "intercetta": intercepts[i]
                })
            df_segmenti = pd.DataFrame(segmenti)

            # Salvataggio in Excel disabilitato
            self.salva_excel = False
            self.percorso_excel = None

            self.result = (ps_coerenti, df_media, df_segmenti, breaks, pwlf_model,
                           self.percorso_excel, msg_info, Qgis.Info, True)
            return True

        except Exception as e:
            QgsMessageLog.logMessage(f"Errore task: {str(e)}", "Cinematica", Qgis.Critical)
            return False

    def finished(self, result):
        if not result or self.result is None:
            QgsMessageLog.logMessage("❌ Task fallito", "Cinematica", Qgis.Critical)
            QMessageBox.critical(None, 'InSAR TS – Errore',
                'Elaborazione non completata. Controlla il log di QGIS per i dettagli.')
            return

        ps_coerenti, df_media, df_segmenti, breaks, pwlf_model, percorso_excel, msg_info, msg_level, do_plot = self.result

        QgsMessageLog.logMessage(msg_info, "Cinematica", msg_level)

        if percorso_excel:
            QgsMessageLog.logMessage(f"📁 File Excel salvato in: {percorso_excel}", "Cinematica", Qgis.Info)

        if not do_plot or df_media is None:
            QMessageBox.warning(None, 'InSAR TS – Nessun PS coerente trovato!',
                'Nessun PS coerente trovato tra i punti selezionati.\n\n'
                'Prova ad abbassare la soglia di correlazione oppure a selezionare '
                'un\'area con PS cinematicamente più omogenei.')
            return
        # ── PLOT ──────────────────────────────────────────────────────────────
        import matplotlib.pyplot as plt
        import matplotlib.gridspec as gridspec
        plt.close('all')

        n_sel = len(self.df)
        n_coe = len(ps_coerenti)

        x_num = mdates.date2num(df_media["data"])
        slope_tot, intercept_tot, r_value, p_value, std_err = linregress(
            x_num, df_media["deformazione_media"]
        )
        vel_tot = round(slope_tot * 365.25, 2)
        r2_tot  = round(r_value ** 2, 3)

        x_pred    = np.linspace(x_num.min(), x_num.max(), 500)
        dates_pred = mdates.num2date(x_pred)
        y_tot     = slope_tot * x_pred + intercept_tot
        y_pw      = pwlf_model.predict(x_pred)

        # Layout: grafico in alto (80%) + tabella in basso (20%)
        fig = plt.figure(figsize=(12, 8))
        fig.patch.set_facecolor('white')
        gs  = gridspec.GridSpec(2, 1, figure=fig,
                                height_ratios=[4, 1],
                                hspace=0.08,
                                left=0.07, right=0.97,
                                top=0.91, bottom=0.04)

        ax  = fig.add_subplot(gs[0])
        ax.set_facecolor('#f5f5f5')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#cccccc')
        ax.spines['bottom'].set_color('#cccccc')
        ax.tick_params(colors='#444444', labelsize=9)
        ax.yaxis.label.set_color('#444444')
        ax.xaxis.label.set_color('#444444')
        ax.title.set_color('#222222')

        # Serie media — punti piccoli
        ax.scatter(df_media["data"], df_media["deformazione_media"],
                   s=18, color='#3498db', alpha=0.8, zorder=3,
                   edgecolors='white', linewidths=0.3,
                   label=f'Serie media  (PS sel: {n_sel} | coe: {n_coe})')

        # Retta complessiva — grigio tratteggiato
        ax.plot(dates_pred, y_tot, color='#888888', lw=1.2, ls='--',
                label=f'Trend complessivo  v={vel_tot:.2f} mm/a, R²={r2_tot:.3f}',
                zorder=2)

        # Curva piecewise — rosso
        ax.plot(dates_pred, y_pw, color='#e74c3c', lw=2,
                label=f'Analisi piecewise  ({len(breaks)-1} segmenti)', zorder=4)

        # Breakpoints — linee verticali sottili con data
        ylim   = ax.get_ylim()
        y_ann  = ylim[1] - (ylim[1] - ylim[0]) * 0.04
        inner  = breaks[1:-1]  # esclude estremi
        for bp in inner:
            bp_date = mdates.num2date(bp)
            ax.axvline(bp_date, color='#f39c12', lw=1.2, ls=':', alpha=0.85, zorder=3)
            ax.text(bp_date, y_ann, bp_date.strftime('%d/%m/%Y'),
                    rotation=90, va='top', ha='right',
                    fontsize=7.5, color='#f39c12')

        ax.axhline(0, color='#cccccc', lw=0.7, ls=':')
        ax.set_ylabel('Deformazione (mm)', fontsize=9)
        ax.set_title(
            f'Analisi di linearità — PS selezionati: {n_sel}  |  PS coerenti: {n_coe}',
            fontsize=11, pad=8)
        ax.legend(fontsize=8, loc='best',
                  framealpha=0.9, edgecolor='#cccccc')
        ax.grid(True, alpha=0.3, color='#cccccc')
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
        ax.xaxis.set_major_locator(mdates.YearLocator())
        fig.autofmt_xdate(rotation=0, ha='center')

        # ── Tabella riepilogativa in basso ─────────────────────────────────────
        ax_tab = fig.add_subplot(gs[1])
        ax_tab.axis('off')
        ax_tab.set_facecolor('white')

        # Costruisce intestazioni e righe
        col_labels = ['Segmento', 'Periodo', 'Velocità (mm/a)', 'R²']
        rows = []
        # Riga trend complessivo
        d0 = df_media["data"].min().strftime('%d/%m/%Y')
        d1 = df_media["data"].max().strftime('%d/%m/%Y')
        rows.append(['Trend complessivo',
                     f'{d0} — {d1}',
                     f'{vel_tot:+.2f}',
                     f'{r2_tot:.3f}'])

        # Righe segmenti
        for i, row in df_segmenti.iterrows():
            s_num = mdates.date2num(pd.to_datetime(row['data_inizio']))
            e_num = mdates.date2num(pd.to_datetime(row['data_fine']))
            mask_s = (x_num >= s_num) & (x_num <= e_num)
            if np.sum(mask_s) > 1:
                sl_s, ic_s, r_s, *_ = linregress(
                    x_num[mask_s],
                    df_media["deformazione_media"].values[mask_s])
                r2_s = round(r_s**2, 3)
            else:
                r2_s = float('nan')
            vel_s = round(row['pendenza'] * 365.25, 2)
            r2_str = f'{r2_s:.3f}' if not np.isnan(r2_s) else 'N/A'
            rows.append([f'Segmento {row["segmento"]}',
                         f'{row["data_inizio"]} — {row["data_fine"]}',
                         f'{vel_s:+.2f}',
                         r2_str])

        tbl = ax_tab.table(
            cellText=rows,
            colLabels=col_labels,
            cellLoc='center',
            loc='center',
            bbox=[0, 0, 1, 1]
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8.5)

        # Stile intestazioni
        for j in range(len(col_labels)):
            cell = tbl[0, j]
            cell.set_facecolor('#2c3e50')
            cell.set_text_props(color='white', fontweight='bold')

        # Stile riga trend complessivo
        for j in range(len(col_labels)):
            tbl[1, j].set_facecolor('#eaf0fb')
            tbl[1, j].set_text_props(color='#555555')

        # Stile righe segmenti — alternato
        for i in range(2, len(rows) + 1):
            fc = '#fff8f8' if i % 2 == 0 else 'white'
            for j in range(len(col_labels)):
                tbl[i, j].set_facecolor(fc)
                tbl[i, j].set_text_props(color='#e74c3c')

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
                    int(_geo.top()  + _geo.height() * 0.10))
        except Exception:
            pass



main()
