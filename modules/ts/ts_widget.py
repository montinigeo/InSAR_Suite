"""
Modulo TS (Time Series) per InSAR Suite.

Gestisce il tab delle analisi serie storiche. Poiché tutti gli script TS
lavorano sul layer attivo + feature selezionate, questo modulo:
  1. Mostra un pannello di selezione del layer PS da analizzare
  2. Attiva la selezione manuale punti sulla mappa
  3. Esegue lo script TS richiesto sul layer attivo con i punti selezionati
"""

import os
import runpy

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFrame, QMessageBox, QToolButton
)
from qgis.PyQt.QtGui import QIcon, QFont
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes
from qgis.gui import QgsMapLayerComboBox, QgsMapToolIdentifyFeature
try:
    from qgis.core import QgsMapLayerProxyModel
except ImportError:
    from qgis.gui import QgsMapLayerProxyModel


SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), 'scripts')

SCRIPT_DEFS = [
    {
        'label':   'Qualità del dato',
        'tooltip': 'Analisi qualità e omogeneità dei PS selezionati (velocità, outlier)',
        'icon':    'normalita.png',
        'script':  'qualita_dato.py',
        'err':     'Errore analisi qualità dato',
    },
    {
        'label':   'Analisi Automatica',
        'tooltip': 'Analisi serie storica — serie media + layer temporaneo QGIS',
        'icon':    'auto.png',
        'script':  'analisi_cinematica_qgis_auto.py',
        'err':     'Errore analisi automatica',
    },
    {
        'label':   'Scomposizione TS',
        'tooltip': 'Scomposizione serie storica in trend, stagionalità, residui',
        'icon':    'scomposizione.png',
        'script':  'analisi_cinematica_qgis_auto_scomposizione.py',
        'err':     'Errore scomposizione',
    },
    {
        'label':   'Analisi Non Lineare',
        'tooltip': 'Analisi di linearità della serie storica (piecewise)',
        'icon':    'non_lineare.png',
        'script':  'analisi_serie_non_lineare.py',
        'err':     'Errore analisi non lineare',
    },
    {
        'label':   'Anomalie temporali',
        'tooltip': 'Rilevamento acquisizioni anomale nella serie storica media',
        'icon':    'anomalie.png',
        'script':  'anomalie_temporali.py',
        'err':     'Errore rilevamento anomalie',
    },
    {
        'label':   'Confronto tra zone',
        'tooltip': 'Confronto serie storiche medie tra due o tre zone diverse',
        'icon':    'confronto.png',
        'script':  'confronto_zone.py',
        'err':     'Errore confronto zone',
    },
]


class TSWidget(QWidget):
    """Widget principale del tab TS."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._icons_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'icons')
        self._build_ui()

    # ──────────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(10)

        # ── Titolo ────────────────────────────────────────────────────────────
        title = QLabel('InSAR TS  ·  Analisi Serie Storiche')
        title.setAlignment(Qt.AlignCenter)
        f = QFont(); f.setPointSize(12); f.setBold(True)
        title.setFont(f)
        title.setStyleSheet('color: #2980b9; padding: 4px 0 6px 0;')
        main.addWidget(title)

        # ── Selezione layer ───────────────────────────────────────────────────
        grp_sel = QGroupBox('1 · Layer PS da analizzare')
        grp_sel.setStyleSheet(self._group_style())
        v_sel = QVBoxLayout(grp_sel)

        lbl_hint = QLabel(
            'Seleziona il layer PS puntuale e scegli i punti sulla mappa,\n'
            'poi avvia l\'analisi desiderata.'
        )
        lbl_hint.setStyleSheet('color:#bdc3c7; font-size:11px;')
        lbl_hint.setWordWrap(True)
        v_sel.addWidget(lbl_hint)

        h_layer = QHBoxLayout()
        self.cb_layer = QgsMapLayerComboBox()
        self.cb_layer.setFilters(QgsMapLayerProxyModel.PointLayer)
        self.cb_layer.setAllowEmptyLayer(True)
        self.cb_layer.setCurrentIndex(0)
        self.cb_layer.setStyleSheet(self._combo_style())
        h_layer.addWidget(self.cb_layer, 1)

        self.btn_set_active = QPushButton('Imposta come layer attivo')
        self.btn_set_active.setToolTip(
            'Rende questo layer attivo in QGIS e abilita la selezione dei punti sulla mappa'
        )
        self.btn_set_active.clicked.connect(self._set_active_layer)
        h_layer.addWidget(self.btn_set_active)
        v_sel.addLayout(h_layer)

        # Info layer attivo corrente
        self.lbl_active = QLabel()
        self.lbl_active.setStyleSheet(
            'color:#f39c12; font-size:11px; padding: 2px 0;'
        )
        self.lbl_active.setWordWrap(True)
        v_sel.addWidget(self.lbl_active)
        self._update_active_label()

        # Aggiorna label quando cambia il layer attivo in QGIS
        self.iface.currentLayerChanged.connect(self._update_active_label)

        main.addWidget(grp_sel)

        # ── Info selezione ────────────────────────────────────────────────────
        grp_info = QGroupBox('2 · Selezione punti sulla mappa')
        grp_info.setStyleSheet(self._group_style())
        v_info = QVBoxLayout(grp_info)

        info_txt = QLabel(
            'Per selezionare i punti PS usa gli strumenti di selezione standard di QGIS:\n'
            '  •  Selezione rettangolo  (Shift+drag)\n'
            '  •  Selezione a mano libera  (Ctrl+Shift+drag)\n'
            '  •  Selezione singola  (clic sul punto con layer attivo)\n\n'
            'Puoi anche usare "Seleziona per espressione" o "Seleziona per attributo".\n'
            'Il numero di punti selezionati è mostrato nella barra di stato di QGIS.'
        )
        info_txt.setStyleSheet('color:#bdc3c7; font-size:11px;')
        info_txt.setWordWrap(True)
        v_info.addWidget(info_txt)

        self.lbl_nsel = QLabel('Punti selezionati: —')
        self.lbl_nsel.setStyleSheet(
            'color:#2ecc71; font-weight:bold; font-size:12px; padding:4px 0;'
        )
        v_info.addWidget(self.lbl_nsel)

        btn_refresh = QPushButton('🔄  Aggiorna conteggio selezione')
        btn_refresh.setToolTip('Aggiorna il numero di punti selezionati nel layer attivo')
        btn_refresh.clicked.connect(self._refresh_selection_count)
        v_info.addWidget(btn_refresh)

        main.addWidget(grp_info)

        # ── Analisi disponibili ───────────────────────────────────────────────
        grp_ana = QGroupBox('3 · Avvia analisi')
        grp_ana.setStyleSheet(self._group_style())
        v_ana = QVBoxLayout(grp_ana)

        for sd in SCRIPT_DEFS:
            btn = self._make_script_button(sd)
            v_ana.addWidget(btn)

        main.addWidget(grp_ana)
        main.addStretch()

    # ──────────────────────────────────────────────────────────────────────────
    def _make_script_button(self, sd):
        icon_path = os.path.join(self._icons_dir, sd['icon'])
        btn = QPushButton(sd['label'])
        if os.path.exists(icon_path):
            btn.setIcon(QIcon(icon_path))
        btn.setToolTip(sd['tooltip'])
        btn.setMinimumHeight(32)
        btn.setStyleSheet("""
            QPushButton {
                background-color: #2e4057;
                color: #ecf0f1;
                border: 1px solid #3a6ea5;
                border-radius: 4px;
                padding: 6px 12px;
                text-align: left;
                font-size: 12px;
            }
            QPushButton:hover   { background-color: #3a6ea5; }
            QPushButton:pressed { background-color: #1a5276; }
        """)
        # Cattura sd in una closure
        def _clicked(checked=False, _sd=sd):
            self._run_script(_sd)
        btn.clicked.connect(_clicked)
        return btn

    # ──────────────────────────────────────────────────────────────────────────
    def _set_active_layer(self):
        layer = self.cb_layer.currentLayer()
        if not layer:
            QMessageBox.warning(self, 'Nessun layer', 'Seleziona un layer PS puntuale.')
            return
        self.iface.setActiveLayer(layer)
        self._update_active_label()
        self._refresh_selection_count()
        self.iface.messageBar().pushMessage(
            'InSAR Suite',
            f'Layer attivo impostato: {layer.name()}. '
            'Ora puoi selezionare i punti sulla mappa.',
            level=0, duration=6
        )

    def _update_active_label(self, *args):
        layer = self.iface.activeLayer()
        if layer and isinstance(layer, QgsVectorLayer) and \
                layer.geometryType() == QgsWkbTypes.PointGeometry:
            self.lbl_active.setText(f'Layer attivo corrente: {layer.name()}')
            self.lbl_active.setStyleSheet(
                'color:#2ecc71; font-size:11px; padding: 2px 0;'
            )
        elif layer:
            self.lbl_active.setText(
                f'Layer attivo corrente: {layer.name()} (non puntuale — seleziona un layer PS)'
            )
            self.lbl_active.setStyleSheet(
                'color:#f39c12; font-size:11px; padding: 2px 0;'
            )
        else:
            self.lbl_active.setText('Nessun layer attivo.')
            self.lbl_active.setStyleSheet(
                'color:#e74c3c; font-size:11px; padding: 2px 0;'
            )
        self._refresh_selection_count()

    def _refresh_selection_count(self):
        layer = self.iface.activeLayer()
        if layer and isinstance(layer, QgsVectorLayer):
            n = layer.selectedFeatureCount()
            self.lbl_nsel.setText(f'Punti selezionati: {n}')
            color = '#2ecc71' if n > 0 else '#e74c3c'
            self.lbl_nsel.setStyleSheet(
                f'color:{color}; font-weight:bold; font-size:12px; padding:4px 0;'
            )
        else:
            self.lbl_nsel.setText('Punti selezionati: —')
            self.lbl_nsel.setStyleSheet(
                'color:#7f8c8d; font-weight:bold; font-size:12px; padding:4px 0;'
            )

    # ──────────────────────────────────────────────────────────────────────────
    def _run_script(self, sd):
        """Verifica prerequisiti ed esegue lo script TS richiesto."""
        layer = self.iface.activeLayer()

        # ── Controllo layer attivo ────────────────────────────────────────────
        if not layer:
            QMessageBox.warning(
                self, 'Layer non attivo',
                'Nessun layer attivo.\n'
                'Usa il pulsante "Imposta come layer attivo" nel riquadro ① '
                'per selezionare il layer PS da analizzare.'
            )
            return

        if not isinstance(layer, QgsVectorLayer) or \
                layer.geometryType() != QgsWkbTypes.PointGeometry:
            QMessageBox.warning(
                self, 'Layer non valido',
                f'Il layer attivo "{layer.name()}" non è un layer puntuale PS.\n'
                'Seleziona un layer PS puntuale nel riquadro ① e imposta come attivo.'
            )
            return

        # ── Controllo selezione ───────────────────────────────────────────────
        n_sel = layer.selectedFeatureCount()
        if n_sel == 0:
            QMessageBox.warning(
                self, 'Nessun punto selezionato',
                f'Nessuna feature selezionata nel layer "{layer.name()}".\n\n'
                'Seleziona almeno un punto PS sulla mappa prima di avviare l\'analisi.\n'
                '(Usa gli strumenti di selezione di QGIS nella toolbar principale)'
            )
            return

        # ── Esecuzione script ─────────────────────────────────────────────────
        script_path = os.path.join(SCRIPTS_DIR, sd['script'])
        if not os.path.exists(script_path):
            QMessageBox.critical(
                self, 'Script non trovato',
                f'Script non trovato:\n{script_path}'
            )
            return

        try:
            runpy.run_path(script_path, init_globals={'iface': self.iface})
        except SystemExit:
            pass  # uscita pulita richiesta dallo script (validazione fallita con QMessageBox)
        except Exception as e:
            QMessageBox.critical(self, 'Errore', f'{sd["err"]}:\n{e}')

    # ──────────────────────────────────────────────────────────────────────────
    # Stili
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _group_style():
        return """
            QGroupBox {
                font-weight: bold;
                color: #ecf0f1;
                border: 1px solid #3a6ea5;
                border-radius: 5px;
                margin-top: 8px;
                padding-top: 6px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                color: #2980b9;
            }
        """

    @staticmethod
    def _combo_style():
        return """
            QgsMapLayerComboBox, QComboBox {
                background-color: #2c3e50;
                color: #ecf0f1;
                border: 1px solid #3a6ea5;
                border-radius: 3px;
                padding: 3px 6px;
                min-height: 22px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #2c3e50;
                color: #ecf0f1;
                selection-background-color: #2980b9;
            }
        """
