from qgis.PyQt.QtWidgets import QInputDialog, QMessageBox
from qgis.core import QgsTask, QgsMessageLog, Qgis, QgsApplication
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from statsmodels.tsa.seasonal import seasonal_decompose
import re

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
    default_soglia = 0.85

    layer = iface.activeLayer()
    if not layer:
        QMessageBox.warning(None, 'InSAR TS – Layer non attivo',
            'Nessun layer PS attivo.\n\n'
            'Per attivarlo: clicca sul layer PS nel pannello Layer '
            '(evidenziato in blu), poi riavvia l\'analisi.')
        return
    from qgis.core import QgsVectorLayer
    if not isinstance(layer, QgsVectorLayer):
        QMessageBox.warning(None, 'InSAR TS – Layer non valido',
            'Il layer attivo non e un layer vettoriale PS.\n\n'
            'Seleziona un layer PS puntuale nel pannello Layer '
            '(clicca su di esso per renderlo attivo), poi riavvia l\'analisi.')
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
        soglia_corr = default_soglia
    else:
        soglia_corr, ok = QInputDialog.getDouble(
            iface.mainWindow(),
            "Soglia di correlazione",
            "Inserisci la soglia di correlazione (0-1):",
            value=default_soglia, min=0.0, max=1.0, decimals=2
        )
        if not ok:
            return  # utente ha annullato

    campi_date = [f.name() for f in layer.fields()
                  if re.match(r"^D\d{8}$", f.name()) or re.match(r"^\d{8}$", f.name())]
    campi_date = ["D" + c if re.match(r"^\d{8}$", c) else c for c in campi_date]
    if not campi_date:
        QMessageBox.warning(None, 'InSAR TS',
            'Nessun campo data trovato nel layer.\n'
            'I campi delle date devono avere formato DYYYYMMDD o YYYYMMDD.')
        return
    date = [pd.to_datetime(c[1:], format="%Y%m%d") for c in campi_date]

    records = []
    for feat in selected_features:
        code = feat["CODE"] if "CODE" in feat.fields().names() else feat.id()
        values = [_qv(feat[c]) for c in campi_date]
        records.append([code] + values)
    df = pd.DataFrame(records, columns=["CODE"] + campi_date)

    task = AnalisiCinematicaTask(
        "InSAR TS - Scomposizione serie storiche",
        df, date, soglia_corr, campi_date, layer
    )
    _active_tasks.append(task)  # previene garbage collection
    QgsApplication.taskManager().addTask(task)


# ================= QGIS TASK =================
# ================= QGIS TASK =================
class AnalisiCinematicaTask(QgsTask):
    def __init__(self, description, df, date, soglia_corr, campi_date, layer=None):
        super().__init__(description, QgsTask.CanCancel if hasattr(QgsTask, "CanCancel") else QgsTask.Flag.CanCancel)
        self.layer = layer
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
                msg_info = "ℹ️ Analisi di un singolo PS: eseguita regressione lineare e calcolo velocità media."
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
                mask_valid = (corr_df >= self.soglia_corr) if self.soglia_corr > 0 else (corr_df.notna())
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

            # Serie media coerente
            if do_plot:
                serie_coerenti = ps_coerenti[self.campi_date].to_numpy(dtype=float)
                serie_media = np.nanmean(serie_coerenti, axis=0)
                df_media = pd.DataFrame({"data": self.date, "deformazione_media": serie_media}).dropna().reset_index(drop=True)
            else:
                df_media = None

            self.result = (ps_coerenti, df_media, n, msg_info, msg_level, do_plot)
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

        ps_coerenti, df_media, n, msg_info, msg_level, do_plot = self.result
        QgsMessageLog.logMessage(msg_info, "Cinematica", msg_level)

        if not do_plot or df_media is None:
            QMessageBox.warning(None, 'InSAR TS – Nessun PS coerente trovato!',
                f'Nessun PS coerente trovato tra i {n} punti selezionati.\n\n'
                'Prova ad abbassare la soglia di correlazione oppure a selezionare '
                'un\'area con PS cinematicamente più omogenei.')
            return

        # ======== SCOMPOSIZIONE SERIE STORICA (solo trend, stagionalità, residui) ========
        try:
            period = 12  # ciclo annuale

            decomp = seasonal_decompose(
                df_media["deformazione_media"],
                period=period,
                model='additive',
                extrapolate_trend='freq'
            )

            plt.close('all')
            fig, axes = plt.subplots(3, 1, figsize=(9, 6), sharex=True)
            fig.patch.set_facecolor('white')
            for ax in axes:
                ax.set_facecolor('#f5f5f5')
                ax.spines['top'].set_visible(False)
                ax.spines['right'].set_visible(False)
                ax.spines['left'].set_color('#cccccc')
                ax.spines['bottom'].set_color('#cccccc')
                ax.tick_params(colors='#444444')
            ax_trend, ax_seasonal, ax_resid = axes

            # TREND
            ax_trend.plot(df_media["data"], decomp.trend, color='steelblue', linewidth=1.3)
            ax_trend.set_ylabel("Trend (mm)")
            ax_trend.grid(True)
            ax_trend.set_title("Componente di Trend")

            # STAGIONALITÀ
            ax_seasonal.plot(df_media["data"], decomp.seasonal, color='darkolivegreen', linewidth=0.9)
            ax_seasonal.set_ylabel("Stagionalità (mm)")
            ax_seasonal.grid(True)
            ax_seasonal.set_title("Componente Stagionale")

            # RESIDUI
            ax_resid.plot(df_media["data"], decomp.resid, color='firebrick', linewidth=0.9)
            ax_resid.set_ylabel("Residui (mm)")
            ax_resid.grid(True)
            ax_resid.set_title("Residui (rumore)")

            # Asse X con mesi abbreviati
            mesi = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
            tick_dates = df_media["data"][::max(1, len(df_media)//12)]
            ax_resid.set_xticks(tick_dates)
            ax_resid.set_xticklabels([mesi[d.month-1] + str(d.year)[-2:] for d in tick_dates], rotation=45)

            # Titolo dinamico in base al numero di PS coerenti
            if len(ps_coerenti) == 1:
                titolo = "Scomposizione serie storica del PS selezionato"
            else:
                titolo = f"Scomposizione serie coerente ({len(ps_coerenti)} PS utilizzati su {n} selezionati) con soglia di correlazione={self.soglia_corr}"

            plt.suptitle(titolo, fontsize=12, y=0.98)

            # Layout ottimizzato per aumentare altezza grafici, con margine inferiore piccolo e spaziatura verticale aumentata
            plt.tight_layout(rect=[0, 0.05, 1, 0.93], h_pad=3.0)
            # Pulsante carica PS coerenti in QGIS
            from matplotlib.widgets import Button as _MplBtn
            from qgis.PyQt.QtCore import QTimer as _QTimer
            _ax_ps = fig.add_axes([0.01, 0.01, 0.22, 0.04])
            _btn_ps = _MplBtn(_ax_ps, "Carica PS coerenti in QGIS",
                              color="#27ae60", hovercolor="#2ecc71")
            _btn_ps.label.set_color("white")
            _btn_ps.label.set_fontsize(8)
            _ps_snap = ps_coerenti.copy()
            _layer_snap = self.layer
            _nc_snap = len(ps_coerenti)
            _nt_snap = n
            def _on_carica_ps(event, _ps=_ps_snap, _lyr=_layer_snap, _nc=_nc_snap, _nt=_nt_snap):
                def _load():
                    try:
                        from qgis.core import QgsVectorLayer, QgsProject, QgsFeature
                        _ps_lyr = _lyr if _lyr is not None else iface.activeLayer()
                        if _ps_lyr is None: return
                        _hl = QgsVectorLayer("Point?crs=" + _ps_lyr.crs().authid(),
                            "PS_coerenti (" + str(_nc) + "/" + str(_nt) + ")", "memory")
                        _dp = _hl.dataProvider()
                        _dp.addAttributes(_ps_lyr.fields().toList())
                        _hl.updateFields()
                        _codes = set(_ps["CODE"].tolist()) if "CODE" in _ps.columns else set()
                        _ids = set(_ps["ID"].tolist()) if "ID" in _ps.columns else set()
                        _feats = []
                        for _f in _ps_lyr.selectedFeatures():
                            _c = _f["CODE"] if "CODE" in _f.fields().names() else None
                            if (_c is not None and _c in _codes) or _f.id() in _ids:
                                _nf = QgsFeature(_hl.fields())
                                _nf.setGeometry(_f.geometry())
                                _nf.setAttributes(_f.attributes())
                                _feats.append(_nf)
                        _dp.addFeatures(_feats); _hl.updateExtents()
                        QgsProject.instance().addMapLayer(_hl)
                        iface.mapCanvas().refresh()
                    except Exception: pass
                _QTimer.singleShot(0, _load)
            _btn_ps.on_clicked(_on_carica_ps)
            self._btn_ps = _btn_ps
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

        except Exception as e:
            QgsMessageLog.logMessage(f"⚠️ Impossibile scomporre la serie storica: {str(e)}", "Cinematica", Qgis.Warning)


main()
