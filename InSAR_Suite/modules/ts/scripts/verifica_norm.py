from qgis.PyQt.QtGui import QAction  # QGIS 4 / Qt6
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QDoubleSpinBox, QDialogButtonBox, QMessageBox
from qgis.utils import iface
from qgis.core import QgsProject
import numpy as np
import pandas as pd
import re
from scipy.stats import skew, kurtosis
import matplotlib.pyplot as plt
plt.close('all')

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
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def getValue(self):
        return self.spin.value()

def corr_valid(x, y):
    mask = ~np.isnan(x) & ~np.isnan(y)
    if np.sum(mask) < 5:
        return np.nan
    return np.corrcoef(x[mask], y[mask])[0, 1]

class DistribuzioneSpostamentiVelocita:
    def __init__(self):
        self.layer = iface.activeLayer()
        if not self.layer:
            QMessageBox.warning(None, 'InSAR TS', 'Nessun layer PS attivo.\nSeleziona un layer PS puntuale nel pannello Layer prima di avviare l\'analisi.')
            return
        from qgis.core import QgsVectorLayer
        if not isinstance(self.layer, QgsVectorLayer):
            QMessageBox.warning(None, 'InSAR TS – Layer non valido',
                'Il layer attivo non e un layer vettoriale PS.\n\n'
                'Seleziona un layer PS puntuale nel pannello Layer '
                '(clicca su di esso per renderlo attivo), poi riavvia l\'analisi.')
            return
        self.selected_features = self.layer.selectedFeatures()
        if not self.selected_features:
            QMessageBox.warning(None, 'InSAR TS – Nessun PS selezionato!',
                'Nessun punto PS selezionato nel layer attivo.\n\n'
                'Seleziona uno o più punti PS sulla mappa con gli strumenti di selezione di QGIS, '
                'poi avvia nuovamente l\'analisi.')
            return
        self.campi_date = [f.name() for f in self.layer.fields() if re.match(r"^D\d{8}$", f.name()) or re.match(r"^\d{8}$", f.name())]
        self.campi_date = ['D' + c if re.match(r"^\d{8}$", c) else c for c in self.campi_date]
        if not self.campi_date:
            QMessageBox.warning(None, 'InSAR TS', 'Nessun campo data trovato nel layer.\nI campi delle date devono avere formato DYYYYMMDD o YYYYMMDD (es. D20170101 o 20170101).')
            return

        # Chiedi soglia correlazione se più di 1 PS selezionato
        if len(self.selected_features) > 1:
            dlg = SogliaDialog()
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.soglia_corr = dlg.getValue()
            else:
                self.soglia_corr = 0.85
        else:
            self.soglia_corr = 0.85
        self.run()

    def run(self):
        # Crea DataFrame con dati selezionati
        records = []
        for feat in self.selected_features:
            code = feat["CODE"] if "CODE" in feat.fields().names() else feat.id()
            values = [feat[c] for c in self.campi_date]
            records.append([code] + values)
        df = pd.DataFrame(records, columns=["CODE"] + self.campi_date)
        valori = df[self.campi_date].apply(pd.to_numeric, errors='coerce')

        n = len(df)
        if n == 1:
            ps_coerenti = df.copy()
        else:
            corr_matrix = np.full((n, n), np.nan)
            for i in range(n):
                serie_i = valori.iloc[i].values.astype(float)
                for j in range(i, n):
                    serie_j = valori.iloc[j].values.astype(float)
                    c = corr_valid(serie_i, serie_j)
                    corr_matrix[i, j] = corr_matrix[j, i] = c
            corr_df = pd.DataFrame(corr_matrix, columns=df["CODE"], index=df["CODE"])
            mask_valid = (corr_df >= self.soglia_corr) if self.soglia_corr > 0 else (corr_df.notna())
            coerenti = mask_valid.sum(axis=1) >= (n / 2)
            ps_coerenti = df.loc[coerenti.values].reset_index(drop=True)

        if len(ps_coerenti) == 0:
            QMessageBox.warning(None, 'InSAR TS – Nessun PS coerente trovato!',
                'Nessun PS coerente trovato tra i punti selezionati.\n\n'
                'Prova ad abbassare la soglia di correlazione oppure a selezionare '
                'un\'area con PS cinematicamente più omogenei.')
            return

        # Calcola spostamenti e velocità
        serie_coerenti = ps_coerenti[self.campi_date].to_numpy(dtype=float)
        x_time = np.array([(pd.to_datetime(c[1:], format="%Y%m%d") - pd.to_datetime(self.campi_date[0][1:], format="%Y%m%d")).days / 365.25 for c in self.campi_date])

        velocita_ps = []
        for idx, row in ps_coerenti.iterrows():
            y = row[self.campi_date].values.astype(float)
            mask = ~np.isnan(y)
            if np.sum(mask) >= 2:
                a, b = np.polyfit(x_time[mask], y[mask], 1)
                velocita_ps.append(a)
            else:
                velocita_ps.append(np.nan)
        velocita_ps = np.array(velocita_ps, dtype=float)

        self.plot_distribuzioni(serie_coerenti, velocita_ps)

    def plot_distribuzioni(self, serie_coerenti, velocita_ps):
        spostamenti = serie_coerenti.flatten()
        spostamenti = spostamenti[~np.isnan(spostamenti)]

        velocita_valide = velocita_ps[~np.isnan(velocita_ps)]

        # Calcolo statistiche per spostamenti
        mean_spost = np.mean(spostamenti)
        std_spost = np.std(spostamenti)          # ddof=0 per consistenza con scipy
        sk_spost = skew(spostamenti)
        ku_spost = kurtosis(spostamenti)

        # Calcolo statistiche per velocità
        mean_vel = np.mean(velocita_valide)
        std_vel = np.std(velocita_valide)
        sk_vel = skew(velocita_valide)
        ku_vel = kurtosis(velocita_valide)

        plt.figure(figsize=(12, 6))

        # Istogramma spostamenti
        plt.subplot(1, 2, 1)
        plt.hist(spostamenti, bins=30, color='steelblue', edgecolor='black', alpha=0.7, density=True)
        plt.title(f'Distribuzione spostamenti\n'
                  f'Media={mean_spost:.2f} mm, Std={std_spost:.2f} mm\n'
                  f'Skewness={sk_spost:.2f}, Kurtosis={ku_spost:.2f}')
        plt.xlabel('Spostamento (mm)')
        plt.ylabel('Densità')

        # Istogramma velocità
        plt.subplot(1, 2, 2)
        plt.hist(velocita_valide, bins=15, color='tomato', edgecolor='black', alpha=0.7, density=True)
        plt.title(f'Distribuzione velocità medie\n'
                  f'Media={mean_vel:.2f} mm/anno, Std={std_vel:.2f} mm/anno\n'
                  f'Skewness={sk_vel:.2f}, Kurtosis={ku_vel:.2f}')
        plt.xlabel('Velocità media (mm/anno)')
        plt.ylabel('Densità')

        plt.tight_layout()
        plt.show()

# Per eseguire la funzione
DistribuzioneSpostamentiVelocita()