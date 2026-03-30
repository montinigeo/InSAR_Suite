# ============================================================
# InSAR TS - Analisi cinematica PS selezionati
# Layer temporaneo in QGIS con serie media (salvabile dall'utente)
# ============================================================
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import re

from qgis.core import (


    QgsTask, QgsMessageLog, Qgis, QgsApplication,
    QgsVectorLayer, QgsField, QgsFeature,
    QgsProject
)
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QDoubleSpinBox, QDialogButtonBox, QMessageBox
)
from qgis.PyQt.QtCore import QVariant
import mplcursors

# Registro globale per prevenire garbage collection dei task attivi
_active_tasks = []

# ======= Dialog soglia di correlazione =======

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
        self.setWindowTitle("Soglia di correlazione:")
        layout = QVBoxLayout(self)
        self.spin = QDoubleSpinBox(self)
        self.spin.setRange(0.0, 1.0)
        self.spin.setSingleStep(0.05)
        self.spin.setDecimals(2)
        self.spin.setValue(default_value)
        layout.addWidget(self.spin)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def getValue(self):
        return self.spin.value()

# ================= FUNZIONE DI CORRELAZIONE =================
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

    campi_date = [f.name() for f in layer.fields() if re.match(r"^D\d{8}$", f.name())]
    if not campi_date:
        QMessageBox.warning(None, 'InSAR TS',
            'Nessun campo data trovato nel layer.\n'
            'I campi delle date devono avere formato DYYYYMMDD (es. D20170101).')
        return
    date = [pd.to_datetime(c[1:], format="%Y%m%d") for c in campi_date]

    if num_selected == 1:
        soglia_corr = soglia_corr_default
    else:
        dlg = SogliaDialog()
        if dlg.exec_() != QDialog.Accepted:
            return  # utente ha annullato
        soglia_corr = dlg.getValue()

    records = []
    for feat in selected_features:
        code = feat["CODE"] if "CODE" in feat.fields().names() else feat.id()
        values = [_qv(feat[c]) for c in campi_date]
        records.append([code] + values)
    df = pd.DataFrame(records, columns=["CODE"] + campi_date)

    task = AnalisiCinematicaTask(
        "InSAR TS - Analisi cinematica PS selezionati",
        df, date, soglia_corr, campi_date
    )
    _active_tasks.append(task)  # previene garbage collection
    QgsApplication.taskManager().addTask(task)


# ================= QGIS TASK =================
# ================= QGIS TASK =================
class AnalisiCinematicaTask(QgsTask):
    def __init__(self, description, df, date, soglia_corr, campi_date):
        super().__init__(description, QgsTask.CanCancel)
        self.df = df.copy()
        self.date = date
        self.soglia_corr = soglia_corr
        self.campi_date = campi_date
        self.result = None

    def run(self):
        try:
            valori = self.df[self.campi_date].apply(pd.to_numeric, errors='coerce')
            n = len(self.df)
            msg_info = ""
            msg_level = Qgis.Info
            do_plot = True

            if n == 1:
                ps_coerenti = self.df.copy()
                corr_df = None
                msg_info = "ℹ️ Analisi di un singolo PS."
            else:
                # Matrice di correlazione vettorizzata — O(n*t) invece di O(n²*t)
                arr_c = valori.to_numpy(dtype=float)
                arr_c = np.where(np.isnan(arr_c), 0.0, arr_c)
                std_r = np.std(arr_c, axis=1, ddof=1)
                valid_r = std_r > 0
                if np.sum(valid_r) > 1:
                    corr_matrix = np.corrcoef(arr_c)
                    corr_matrix[~valid_r, :] = np.nan
                    corr_matrix[:, ~valid_r] = np.nan
                else:
                    corr_matrix = np.full((n, n), np.nan)
                corr_df = pd.DataFrame(corr_matrix, columns=self.df["CODE"], index=self.df["CODE"])
                mask_valid = (corr_df >= self.soglia_corr)
                coerenti = mask_valid.sum(axis=1) >= (n / 2)
                ps_coerenti = self.df.loc[coerenti.values].reset_index(drop=True)

                if len(ps_coerenti) == 0:
                    msg_info = f"⚠️ Nessun PS coerente trovato tra {n} punti selezionati."
                    msg_level = Qgis.Warning
                    do_plot = False
                elif len(ps_coerenti) == 1:
                    msg_info = f"ℹ️ Solo 1 PS coerente trovato su {n} selezionati."
                else:
                    msg_info = f"✅ Analisi completata: trovati {len(ps_coerenti)} PS coerenti su {n} selezionati."

            if do_plot:
                serie_coerenti = ps_coerenti[self.campi_date].to_numpy(dtype=float)
                serie_media = np.nanmean(serie_coerenti, axis=0)
                serie_std = np.nanstd(serie_coerenti, axis=0)
                df_media = pd.DataFrame({
                    "data": self.date,
                    "deformazione_media": serie_media,
                    "dev_standard": serie_std
                }).dropna().reset_index(drop=True)
            else:
                df_media = None

            self.result = (ps_coerenti, df_media, msg_info, msg_level, do_plot, n)
            return True

        except Exception as e:
            QgsMessageLog.logMessage(f"Errore task: {str(e)}", "InSAR TS", Qgis.Critical)
            return False

    def finished(self, result):
        if not result or self.result is None:
            QgsMessageLog.logMessage("❌ Task fallito", "InSAR TS", Qgis.Critical)
            QMessageBox.critical(None, 'InSAR TS – Errore',
                'Elaborazione non completata. Controlla il log di QGIS per i dettagli.')
            return

        ps_coerenti, df_media, msg_info, msg_level, do_plot, n_tot = self.result
        n_coer = len(ps_coerenti)

        QgsMessageLog.logMessage(msg_info, "InSAR TS", msg_level)

        if not do_plot or df_media is None:
            QMessageBox.warning(None, 'InSAR TS – Nessun PS coerente trovato!',
                f'Nessun PS coerente trovato tra i {n_tot} punti selezionati.\n\n'
                'Prova ad abbassare la soglia di correlazione oppure a selezionare '
                'un\'area con PS cinematicamente più omogenei.')
            return

        # ======== GRAFICO (il layer è caricabile dall'utente dal pulsante) ========
        self._mostra_grafico(df_media, n_tot, n_coer)

    def _carica_layer_temporaneo(self, df_media, n_tot, n_coer):
        """Crea un layer vettoriale tabellare (NoGeometry) con la serie media
        e lo carica direttamente nel progetto QGIS come layer temporaneo.
        L'utente può salvarlo permanentemente tramite:
        tasto destro sul layer > Esporta > Salva elementi come..."""
        try:
            vl = QgsVectorLayer("NoGeometry", "Serie_media_PS_coerenti", "memory")
            pr = vl.dataProvider()

            pr.addAttributes([
                QgsField("data",                  QVariant.String),
                QgsField("deformazione_media_mm", QVariant.Double),
                QgsField("dev_standard_mm",       QVariant.Double),
                QgsField("n_ps_selezionati",      QVariant.Int),
                QgsField("n_ps_coerenti",         QVariant.Int),
                QgsField("soglia_correlazione",   QVariant.Double),
            ])
            vl.updateFields()

            feats = []
            for _, row in df_media.iterrows():
                f = QgsFeature()
                f.setAttributes([
                    row["data"].strftime("%Y-%m-%d"),
                    float(round(row["deformazione_media"], 4)),
                    float(round(row["dev_standard"], 4)),
                    int(n_tot),
                    int(n_coer),
                    float(self.soglia_corr),
                ])
                feats.append(f)

            pr.addFeatures(feats)
            vl.updateExtents()
            QgsProject.instance().addMapLayer(vl)

            iface.messageBar().pushMessage(
                "InSAR TS",
                f"Layer temporaneo 'Serie_media_PS_coerenti' caricato ({len(df_media)} date). "
                "Tasto destro > Esporta per salvarlo su disco.",
                level=Qgis.Info, duration=10
            )
            QgsMessageLog.logMessage(
                f"✅ Layer temporaneo caricato: {len(df_media)} record, "
                f"{n_coer} PS coerenti su {n_tot} selezionati.",
                "InSAR TS", Qgis.Info
            )

        except Exception as e:
            QgsMessageLog.logMessage(
                f"⚠️ Impossibile creare il layer temporaneo: {str(e)}",
                "InSAR TS", Qgis.Warning
            )

    def _mostra_grafico(self, df_media, n_tot, n_coer):
        plt.close('all')
        fig, ax = plt.subplots(figsize=(10, 6))
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

        std_media_const = np.nanmean(df_media["dev_standard"].values)

        label_media = "Serie media coerente"
        line_media, = ax.plot(
            df_media["data"], df_media["deformazione_media"],
            color='firebrick', linewidth=1.5, label=label_media
        )

        x = (df_media["data"] - df_media["data"].iloc[0]).dt.days / 365.25
        y = df_media["deformazione_media"].values
        r2_text = ""
        line_fit_media = None

        if len(x) >= 2:
            a_media, b_media = np.polyfit(x, y, 1)
            y_fit_media = np.polyval([a_media, b_media], x)
            line_fit_media = ax.plot(
                df_media["data"], y_fit_media, 'steelblue', linewidth=1.2,
                label=f"Trend lineare (V = {a_media:.2f} mm/anno)"
            )[0]
            residui = y - y_fit_media
            ss_res = np.sum(residui ** 2)
            ss_tot = np.sum((y - np.mean(y)) ** 2)
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
            r2_text = f" - R² = {r2:.2f}"

        if n_coer > 1:
            upper_std = df_media["deformazione_media"] + df_media["dev_standard"]
            lower_std = df_media["deformazione_media"] - df_media["dev_standard"]
            line_std_upper = ax.plot(df_media["data"], upper_std, color='firebrick', linestyle='--', linewidth=0.8)
            line_std_lower = ax.plot(df_media["data"], lower_std, color='firebrick', linestyle='--', linewidth=0.8)

        titolo_base = (
            "Serie storica del PS selezionato" if n_coer == 1
            else f"Serie media PS coerenti (soglia={self.soglia_corr:.2f})"
        )
        titolo = titolo_base + r2_text

        label_std_spost = f"± 1σ spostamenti (media={std_media_const:.2f} mm)"
        if len(x) >= 2 and line_fit_media is not None:
            if n_coer > 1:
                ax.legend(
                    [line_media, line_std_upper[0], line_fit_media],
                    [label_media, label_std_spost, line_fit_media.get_label()]
                )
            else:
                ax.legend([line_media, line_fit_media], [label_media, line_fit_media.get_label()])
        else:
            ax.legend([line_media], [label_media])

        ax.set_xlabel("Data")
        ax.set_ylabel("Deformazione (mm)")
        ax.set_title(titolo)
        ax.grid(True)

        # Tooltip interattivo
        cursor = mplcursors.cursor(line_media, hover=True)
        active_annotation = [None]

        @cursor.connect("add")
        def on_add(sel):
            x_val, y_val = sel.target
            date_str = mdates.num2date(x_val).strftime("%d-%m-%Y")
            sel.annotation.set_text(f"{date_str}\n{y_val:.2f} mm")
            sel.annotation.get_bbox_patch().set(fc="white", alpha=0.85)
            sel.annotation.set_visible(True)
            active_annotation[0] = sel.annotation
            fig.canvas.draw_idle()

        def on_move(event):
            if event.inaxes != ax:
                if active_annotation[0] is not None and active_annotation[0].get_visible():
                    active_annotation[0].set_visible(False)
                    fig.canvas.draw_idle()
            else:
                contains, _ = line_media.contains(event)
                if not contains:
                    if active_annotation[0] is not None and active_annotation[0].get_visible():
                        active_annotation[0].set_visible(False)
                        fig.canvas.draw_idle()

        fig.canvas.mpl_connect("motion_notify_event", on_move)

        plt.figtext(
            0.5, 0.02,
            f"PS selezionati: {n_tot} | PS coerenti: {n_coer}",
            ha="center", fontsize=10, color="dimgray"
        )
        plt.tight_layout(rect=[0, 0.05, 1, 1])

        # ── Pulsante "Carica tabella in QGIS" nella toolbar matplotlib ────────
        # Nota: il callback matplotlib gira in un thread separato rispetto a Qt.
        # Usiamo QTimer.singleShot(0, ...) per rimandare l'esecuzione al thread
        # principale Qt, dove le API QGIS possono operare in modo sicuro.
        from matplotlib.widgets import Button as MplButton
        from qgis.PyQt.QtCore import QTimer
        ax_btn = fig.add_axes([0.78, 0.01, 0.20, 0.04])
        btn_layer = MplButton(ax_btn, 'Carica tabella in QGIS',
                              color='#2980b9', hovercolor='#3498db')
        btn_layer.label.set_color('white')
        btn_layer.label.set_fontsize(8)

        def on_carica(event):
            QTimer.singleShot(0, lambda: self._carica_layer_temporaneo(
                df_media, n_tot, n_coer))
        btn_layer.on_clicked(on_carica)
        # Mantiene riferimento per evitare garbage collection
        self._btn_layer = btn_layer

        plt.show()

        # Ridimensiona all'80% dello schermo disponibile
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
