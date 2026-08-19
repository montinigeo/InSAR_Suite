# -*- coding: utf-8 -*-
"""Finestra dei risultati: replica il layout della scheda riepilogativa
PSInSAR e permette l'esportazione in PNG o Excel."""
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QFileDialog, QMessageBox
)
from qgis.PyQt.QtCore import Qt


def _fmt(v, decimals=2, suffix=''):
    if v is None:
        return 'N/D'
    try:
        return f'{v:.{decimals}f}{suffix}'
    except (TypeError, ValueError):
        return 'N/D'


def _fmt_pm(mean, disp, decimals=2, suffix='', disp_na_text='N/D'):
    """Formatta 'media ± dispersione'. Se la media manca: 'N/D'.
    Se la dispersione manca (es. un solo valore, o R troppo basso per
    l'angolo EWUD): 'media ± N/D'."""
    if mean is None:
        return 'N/D'
    mean_txt = f'{mean:.{decimals}f}{suffix}'
    disp_txt = f'{disp:.{decimals}f}{suffix}' if disp is not None else disp_na_text
    return f'{mean_txt} ± {disp_txt}'


class SchedaResultsDialog(QDialog):
    def __init__(self, results, parent=None):
        super().__init__(parent)
        self.results = results
        self.setWindowTitle('Analisi PSInSAR')
        self.setMinimumWidth(680)
        self._build_ui()

    def _field(self, value_text):
        e = QLineEdit(value_text)
        e.setReadOnly(True)
        e.setMinimumWidth(150)
        e.setAlignment(Qt.AlignRight)
        e.setStyleSheet(
            'QLineEdit { background:#ffffff; color:#2c3e50; '
            'border:1px solid #b0c4d8; border-radius:3px; padding:3px 6px; }'
        )
        return e

    def _dataset_group(self, title, dati):
        grp = QGroupBox(title)
        form = QFormLayout(grp)
        if dati is None:
            form.addRow(QLabel('Layer non impostato.'))
            return grp
        form.addRow('N° PS:', self._field(_fmt(dati['n_ps'], 0)))
        form.addRow('Densità PS (PS/kmq):', self._field(_fmt(dati['densita'], 2)))
        form.addRow('Copertura areale:', self._field(_fmt(dati['copertura_pct'], 1, ' %')))
        coe_txt = (f"{_fmt(dati['coerenza_pct'], 1, '%')} (soglia {dati['coerenza_soglia']:.2f})"
                   if dati['coerenza_pct'] is not None else 'N/D')
        form.addRow('Coerenza cinematica:', self._field(coe_txt))
        form.addRow('Visibilità media (%):',
                     self._field(_fmt_pm(dati['visibilita_media'], dati.get('visibilita_std'), 1)))
        form.addRow('Velocità media tot (mm/y):',
                     self._field(_fmt_pm(dati['vel_media_tot'], dati.get('vel_std_tot'), 2)))
        form.addRow('Velocità media coe (mm/y):',
                     self._field(_fmt_pm(dati['vel_media_coe'], dati.get('vel_std_coe'), 2)))
        return grp

    def _build_ui(self):
        root = QVBoxLayout(self)

        title = QLabel('Analisi PSInSAR')
        title.setStyleSheet('font-weight:bold; font-size:13px;')
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        area_row = QHBoxLayout()
        area_row.addWidget(QLabel('Superficie di analisi (mq):'))
        area_row.addWidget(self._field(_fmt(self.results.get('area_mq'), 1)))
        root.addLayout(area_row)

        cols = QHBoxLayout()
        cols.addWidget(self._dataset_group('DATASET ASCENDENTE', self.results.get('asc')))
        cols.addWidget(self._dataset_group('DATASET DISCENDENTE', self.results.get('desc')))
        root.addLayout(cols)

        ewud = self.results.get('ewud')
        grp_ewud = QGroupBox('Vettore EWUD (media su area di studio)')
        ewud_row = QHBoxLayout(grp_ewud)
        if ewud is None:
            ewud_row.addWidget(QLabel('Layer EWUD non impostato.'))
        else:
            for label, key, disp_key, dec, suf in [
                ('Angolo EWUD (°)', 'ang2', 'ang2_std', 1, '°'),
                ('Velocità EWUD (mm/y)', 'vel', 'vel_std', 2, ''),
                ('Velocità E (mm/y)', 'E', 'E_std', 2, ''),
                ('Velocità U (mm/y)', 'U', 'U_std', 2, ''),
            ]:
                col = QVBoxLayout()
                col.addWidget(QLabel(label))
                if disp_key is not None:
                    txt = _fmt_pm(ewud.get(key), ewud.get(disp_key), dec, suf)
                else:
                    txt = _fmt(ewud.get(key), dec, suf)
                col.addWidget(self._field(txt))
                if key == 'ang2' and ewud.get('ang2_R') is not None:
                    r_val = ewud['ang2_R']
                    if r_val >= 0.7:
                        r_desc = 'direzione coerente'
                    elif r_val < 0.4:
                        r_desc = 'direzione dispersa'
                    else:
                        r_desc = 'direzione moderatamente coerente'
                    col.itemAt(1).widget().setToolTip(
                        f'Lunghezza risultante R = {r_val:.2f} ({r_desc})')
                ewud_row.addLayout(col)
        root.addWidget(grp_ewud)

        btn_row = QHBoxLayout()
        btn_png = QPushButton('Esporta PNG')
        btn_png.clicked.connect(self._export_png)
        btn_csv = QPushButton('Esporta CSV')
        btn_csv.clicked.connect(self._export_csv)
        btn_close = QPushButton('Chiudi')
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_png)
        btn_row.addWidget(btn_csv)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        root.addLayout(btn_row)

    # ── Export ───────────────────────────────────────────────────────────────
    def _export_png(self):
        path, _ = QFileDialog.getSaveFileName(
            self, 'Salva scheda come immagine', '', 'Immagine PNG (*.png)')
        if not path:
            return
        if not path.lower().endswith('.png'):
            path += '.png'
        pixmap = self.grab()
        if pixmap.save(path, 'PNG'):
            QMessageBox.information(self, 'Esportazione completata',
                                     f'Scheda salvata in:\n{path}')
        else:
            QMessageBox.warning(self, 'Errore', 'Impossibile salvare il file PNG.')

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, 'Salva scheda come CSV', '', 'File CSV (*.csv)')
        if not path:
            return
        if not path.lower().endswith('.csv'):
            path += '.csv'

        import csv

        asc = self.results.get('asc') or {}
        desc = self.results.get('desc') or {}
        ewud = self.results.get('ewud') or {}

        def _v(d, key):
            return d.get(key) if d else ''

        try:
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                w = csv.writer(f, delimiter=';')
                w.writerow(['Analisi PSInSAR'])
                w.writerow([])
                w.writerow(['Superficie di analisi (mq)', self.results.get('area_mq')])
                w.writerow([])
                w.writerow(['Campo', 'Ascendente', 'Discendente'])

                campi = [
                    ('N° PS', 'n_ps'),
                    ('Densità PS (PS/kmq)', 'densita'),
                    ('Copertura areale (%)', 'copertura_pct'),
                    ('Coerenza cinematica (%)', 'coerenza_pct'),
                    ('Soglia coerenza usata', 'coerenza_soglia'),
                    ('Visibilità media (%)', 'visibilita_media'),
                    ('Dev. std. visibilità (%)', 'visibilita_std'),
                    ('Velocità media tot (mm/y)', 'vel_media_tot'),
                    ('Dev. std. velocità tot (mm/y)', 'vel_std_tot'),
                    ('Velocità media coe (mm/y)', 'vel_media_coe'),
                    ('Dev. std. velocità coe (mm/y)', 'vel_std_coe'),
                ]
                for label, key in campi:
                    w.writerow([label, _v(asc, key), _v(desc, key)])

                w.writerow([])
                w.writerow(['Vettore EWUD (media su area di studio)'])
                for label, key in [
                    ('Angolo EWUD (°)', 'ang2'),
                    ('Dev. std. circolare angolo (°)', 'ang2_std'),
                    ('Lunghezza risultante R (0-1)', 'ang2_R'),
                    ('Velocità EWUD (mm/y)', 'vel'),
                    ('Dev. std. velocità EWUD (mm/y)', 'vel_std'),
                    ('Velocità E (mm/y)', 'E'),
                    ('Dev. std. velocità E (mm/y)', 'E_std'),
                    ('Velocità U (mm/y)', 'U'),
                    ('Dev. std. velocità U (mm/y)', 'U_std'),
                ]:
                    w.writerow([label, ewud.get(key, '')])

            QMessageBox.information(self, 'Esportazione completata',
                                     f'Scheda salvata in:\n{path}')
        except Exception as e:
            QMessageBox.warning(self, 'Errore', f'Impossibile salvare il file:\n{e}')
