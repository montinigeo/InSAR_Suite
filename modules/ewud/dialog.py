"""
Dialogo principale del plugin InSAR_EWUD.

Struttura:
  Tab 1 – Crea Griglia PS      (create_grid_asc_desc)
  Tab 2 – Decomposizione EWUD    (egms_ewud_def)

Ogni tab ha:
  • Sezioni collassabili per raggruppare i parametri
  • Validazione live con messaggi inline
  • Barra di avanzamento + log in tempo reale
  • Pulsanti Esegui / Chiudi
"""

import os, traceback
from qgis.PyQt.QtWidgets import (
    QRadioButton, QButtonGroup,
    QDialog, QDialogButtonBox, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QFormLayout, QGroupBox, QLabel, QLineEdit,
    QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox, QPushButton,
    QProgressBar, QTextEdit, QFileDialog, QSizePolicy, QFrame,
    QScrollArea, QMessageBox
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QObject
from qgis.PyQt.QtGui import QColor, QFont, QIcon
from qgis.core import (
    QgsProject, QgsVectorLayer,
    QgsProcessingFeedback, QgsApplication, QgsProcessingContext,
    QgsWkbTypes
)
from qgis.gui import QgsMapLayerComboBox, QgsFieldComboBox, QgsExtentWidget

# QgsMapLayerProxyModel e QgsFieldProxyModel sono stati spostati da qgis.core
# a qgis.gui in QGIS 3.30+. Questo try/except garantisce compatibilità con
# tutte le versioni QGIS 3.x.
try:
    from qgis.core import QgsMapLayerProxyModel, QgsFieldProxyModel
except ImportError:
    from qgis.gui import QgsMapLayerProxyModel, QgsFieldProxyModel
import processing


# ══════════════════════════════════════════════════════════════════════════════
# Worker: esegue l'algoritmo in un thread separato
# ══════════════════════════════════════════════════════════════════════════════
class AlgorithmWorker(QObject):
    progress  = pyqtSignal(int)
    log       = pyqtSignal(str)
    finished  = pyqtSignal(dict)
    error     = pyqtSignal(str)

    def __init__(self, algorithm_id, params):
        super().__init__()
        self.algorithm_id = algorithm_id
        self.params       = params
        self._cancelled   = False

    def run(self):
        try:
            feedback = _SignalFeedback(self.progress, self.log)
            context  = QgsProcessingContext()
            result   = processing.run(
                self.algorithm_id,
                self.params,
                feedback=feedback,
                context=context
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(traceback.format_exc())


class _SignalFeedback(QgsProcessingFeedback):
    """Feedback che emette segnali Qt invece di scrivere su console."""
    def __init__(self, progress_signal, log_signal):
        super().__init__()
        self._prog = progress_signal
        self._log  = log_signal

    def setProgress(self, progress):
        self._prog.emit(int(progress))

    def pushInfo(self, info):
        self._log.emit(f'<span style="color:#2ecc71">ℹ {info}</span>')

    def pushWarning(self, warning):
        self._log.emit(f'<span style="color:#f39c12">⚠ {warning}</span>')

    def reportError(self, error, fatalError=False):
        self._log.emit(f'<span style="color:#e74c3c">✖ {error}</span>')


# ══════════════════════════════════════════════════════════════════════════════
# Helpers UI
# ══════════════════════════════════════════════════════════════════════════════
def _group(title, layout):
    """Restituisce un QGroupBox con il layout dato."""
    box = QGroupBox(title)
    box.setLayout(layout)
    box.setStyleSheet("""
        QGroupBox {
            font-weight: bold;
            border: 1px solid #c8d6e5;
            border-radius: 5px;
            margin-top: 8px;
            padding-top: 6px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            color: #2980b9;
        }
    """)
    return box


def _separator():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


def _layer_combo(layer_type):
    cb = QgsMapLayerComboBox()
    cb.setFilters(layer_type)
    cb.setAllowEmptyLayer(True)
    cb.setCurrentIndex(0)
    cb.setStyleSheet("""
        QgsMapLayerComboBox, QComboBox {
            background-color: #ffffff;
            color: #2c3e50;
            border: 1px solid #b0c4d8;
            border-radius: 3px;
            padding: 3px 6px;
            min-height: 22px;
        }
        QComboBox::drop-down { border: none; }
        QComboBox QAbstractItemView {
            background-color: #ffffff;
            color: #2c3e50;
            selection-background-color: #2980b9;
        }
    """)
    return cb


def _output_row(label_text):
    """Riga con QLineEdit + bottone '...' per scegliere il file di output."""
    from qgis.PyQt.QtGui import QPalette
    row   = QHBoxLayout()
    edit  = QLineEdit()
    edit.setPlaceholderText('Output temporaneo (lascia vuoto)')
    # Forza il colore del testo placeholder a grigio chiaro leggibile
    palette = edit.palette()
    palette.setColor(QPalette.PlaceholderText, QColor('#7f8c8d'))
    edit.setPalette(palette)
    btn   = QPushButton('…')
    btn.setFixedWidth(28)
    row.addWidget(edit)
    row.addWidget(btn)

    def _browse():
        path, _ = QFileDialog.getSaveFileName(
            None, f'Salva {label_text}', '', 'GeoPackage (*.gpkg);;Shapefile (*.shp)'
        )
        if path:
            edit.setText(path)
    btn.clicked.connect(_browse)
    return row, edit


# ══════════════════════════════════════════════════════════════════════════════
# Dialogo principale
# ══════════════════════════════════════════════════════════════════════════════
class EgmsDialog(QDialog):

    STYLE = """
        QDialog {
            background-color: #f5f6fa;
            color: #1a1a2e;
        }
        QTabWidget::pane {
            border: 1px solid #c8d6e5;
            background-color: #ffffff;
            border-radius: 4px;
        }
        QTabBar::tab {
            background: #dfe6ed;
            color: #34495e;
            padding: 7px 18px;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
            margin-right: 2px;
        }
        QTabBar::tab:selected {
            background: #2980b9;
            color: white;
            font-weight: bold;
        }
        QGroupBox {
            font-weight: bold;
            color: #2c3e50;
            border: 1px solid #c8d6e5;
            border-radius: 5px;
            margin-top: 8px;
            padding-top: 6px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            color: #2980b9;
        }
        QLabel {
            color: #2c3e50;
            background-color: transparent;
        }
        QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {
            background-color: #ffffff;
            color: #2c3e50;
            border: 1px solid #b0c4d8;
            border-radius: 3px;
            padding: 3px 6px;
            min-height: 22px;
        }
        QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {
            border: 1px solid #2980b9;
        }
        QCheckBox {
            color: #2c3e50;
        }
        QPushButton {
            background-color: #2980b9;
            color: white;
            border: none;
            border-radius: 4px;
            padding: 6px 16px;
            font-weight: bold;
        }
        QPushButton:hover   { background-color: #3498db; }
        QPushButton:pressed { background-color: #1a6ea5; }
        QPushButton#btnClose {
            background-color: #7f8c8d;
        }
        QPushButton#btnClose:hover { background-color: #95a5a6; }
        QProgressBar {
            border: 1px solid #b0c4d8;
            border-radius: 3px;
            background: #ecf0f1;
            text-align: center;
            color: #2c3e50;
        }
        QProgressBar::chunk { background-color: #2980b9; border-radius: 3px; }
        QTextEdit {
            background-color: #f0f4f8;
            color: #2c3e50;
            border: 1px solid #c8d6e5;
            font-family: monospace;
            font-size: 11px;
        }
        QScrollArea { border: none; }
        QScrollBar:vertical {
            background: #ecf0f1;
            width: 10px;
        }
        QScrollBar::handle:vertical {
            background: #b0c4d8;
            border-radius: 5px;
        }
    """

    def __init__(self, iface, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface   = iface
        self.thread  = None
        self.worker  = None

        self.setWindowTitle('InSAR_EWUD')
        _icon_path = os.path.join(os.path.dirname(__file__), '..', '..', 'icons', 'icon_ewud.png')
        self.setWindowIcon(QIcon(_icon_path))
        self.setMinimumSize(520, 380)
        self.setStyleSheet(self.STYLE)
        self.setSizeGripEnabled(True)   # maniglia di ridimensionamento angolo

        self._build_ui()
        self._fit_to_screen()

    # ──────────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # Logo/titolo
        title = QLabel('InSAR_EWUD  ·  East-West / Up-Down Decomposition')
        title.setAlignment(Qt.AlignCenter)
        f = QFont(); f.setPointSize(13); f.setBold(True)
        title.setFont(f)
        title.setStyleSheet('color: #2980b9; padding: 4px 0 8px 0;')
        main_layout.addWidget(title)

        # Schede
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_tab_grid(), '① Crea Griglia PS')
        self.tabs.addTab(self._build_tab_ewud(), '② Decomposizione EWUD')
        main_layout.addWidget(self.tabs, stretch=1)

        main_layout.addWidget(_separator())

        # Barra avanzamento
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(18)
        main_layout.addWidget(self.progress_bar)

        # Log
        log_label = QLabel('Log di esecuzione:')
        log_label.setStyleSheet('color:#7f8c8d; font-size:11px;')
        main_layout.addWidget(log_label)
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(60)
        self.log_box.setMaximumHeight(120)
        self.log_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        main_layout.addWidget(self.log_box)

        # Pulsanti
        btn_layout = QHBoxLayout()
        self.btn_run   = QPushButton('▶  Esegui')
        self.btn_run.setFixedHeight(34)
        self.btn_close = QPushButton('Chiudi')
        self.btn_close.setObjectName('btnClose')
        self.btn_close.setFixedHeight(34)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_run)
        btn_layout.addWidget(self.btn_close)
        main_layout.addLayout(btn_layout)

        self.btn_run.clicked.connect(self._run)
        self.btn_close.clicked.connect(self.reject)

    def _fit_to_screen(self):
        """Ridimensiona e centra la finestra per adattarsi allo schermo disponibile."""
        from qgis.PyQt.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()  # area schermo escludendo taskbar
        # Dimensione desiderata
        want_w = min(700, available.width()  - 40)
        want_h = min(740, available.height() - 80)
        # Non scendere sotto il minimo
        w = max(want_w, self.minimumWidth())
        h = max(want_h, self.minimumHeight())
        self.resize(w, h)
        # Centra la finestra nell'area disponibile
        x = available.x() + (available.width()  - w) // 2
        y = available.y() + (available.height() - h) // 2
        self.move(x, y)

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 1 – Crea Griglia
    # ══════════════════════════════════════════════════════════════════════════
    def _build_tab_grid(self):
        widget = QWidget()
        outer  = QVBoxLayout(widget)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner  = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(10)

        # ── Estensione ────────────────────────────────────────────────────────
        ext_form = QFormLayout()
        self.g_extent = QgsExtentWidget()
        self.g_extent.setMapCanvas(self.iface.mapCanvas())
        ext_form.addRow('Estensione griglia:', self.g_extent)

        spin_form = QFormLayout()
        self.g_cell = QSpinBox()
        self.g_cell.setRange(1, 100000)
        self.g_cell.setValue(50)
        self.g_cell.setSuffix(' m')
        spin_form.addRow('Lato cella:', self.g_cell)

        # Tipo cella
        cell_type_layout = QHBoxLayout()
        self.rb_square = QRadioButton('Quadrata')
        self.rb_hex    = QRadioButton('Esagonale')
        self.rb_square.setChecked(True)
        bg_cell = QButtonGroup(self)
        bg_cell.addButton(self.rb_square)
        bg_cell.addButton(self.rb_hex)
        cell_type_layout.addWidget(self.rb_square)
        cell_type_layout.addWidget(self.rb_hex)
        cell_type_layout.addStretch()
        spin_form.addRow('Tipo cella:', cell_type_layout)

        extent_group_layout = QVBoxLayout()
        extent_group_layout.addLayout(ext_form)
        extent_group_layout.addLayout(spin_form)
        layout.addWidget(_group('Griglia', extent_group_layout))

        # ── PS Ascending ──────────────────────────────────────────────────────
        asc_form = QFormLayout()
        self.g_ps_asc = _layer_combo(QgsMapLayerProxyModel.PointLayer)
        asc_form.addRow('PS Ascendenti:', self.g_ps_asc)
        layout.addWidget(_group('PS Ascending', asc_form))

        # ── PS Descending ─────────────────────────────────────────────────────
        desc_form = QFormLayout()
        self.g_ps_desc = _layer_combo(QgsMapLayerProxyModel.PointLayer)
        desc_form.addRow('PS Discendenti:', self.g_ps_desc)
        layout.addWidget(_group('PS Descending', desc_form))

        # ── Output ────────────────────────────────────────────────────────────
        out_form = QVBoxLayout()
        row, self.g_out = _output_row('PS Grid')
        lbl = QLabel('PS Grid (poligoni):')
        out_form.addWidget(lbl)
        out_form.addLayout(row)
        layout.addWidget(_group('Output', out_form))

        layout.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        return widget

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 2 – Decomposizione EWUD
    # ══════════════════════════════════════════════════════════════════════════
    def _build_tab_ewud(self):
        widget = QWidget()
        outer  = QVBoxLayout(widget)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner  = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(10)

        # ── Griglia ───────────────────────────────────────────────────────────
        grid_form = QFormLayout()
        self.e_griglia = _layer_combo(QgsMapLayerProxyModel.PolygonLayer)
        self.e_griglia.layerChanged.connect(self._on_grid_layer_changed)
        grid_form.addRow('Griglia ricampionamento:', self.e_griglia)

        self.e_id_griglia = QgsFieldComboBox()
        self.e_id_griglia.setLayer(self.e_griglia.currentLayer())
        self.e_id_griglia.setStyleSheet('background:#ffffff;color:#2c3e50;border:1px solid #b0c4d8;border-radius:3px;padding:3px 6px;min-height:22px;')
        grid_form.addRow('Campo ID griglia:', self.e_id_griglia)

        layout.addWidget(_group('Griglia di ricampionamento', grid_form))

        # ── Preset satellite ──────────────────────────────────────────────────
        # Selezionando un satellite si precompilano automaticamente tutti
        # gli angoli. L'utente può poi modificarli manualmente.
        PRESETS = {
            'Sentinel-1 (EGMS - Italia centro-settentrionale)': (349.0,    42.0,    191.0,    38.0,    True),
            'Sentinel-1 (generico)':   (348.0,    33.0,    192.0,    33.0,    True),
            'ERS / Envisat':           (347.0,    23.0,    193.0,    23.0,    True),
            'ALOS / ALOS-2':           (350.0,    34.0,    190.0,    34.0,    True),
            'RADARSAT-2':           (350.0,    35.0,    190.0,    35.0,    True),
            'COSMO-SkyMed':            (345.0,    30.0,    195.0,    30.0,    True),
            'TerraSAR-X / TanDEM-X':   (350.0,    35.0,    190.0,    35.0,    True),
            'Personalizzato…':          None,
        }
        self._presets = PRESETS

        preset_form = QFormLayout()
        self.e_preset = QComboBox()
        self.e_preset.blockSignals(True)   # evita trigger durante addItems
        self.e_preset.addItems(list(PRESETS.keys()))
        self.e_preset.blockSignals(False)
        self.e_preset.setStyleSheet(
            'background:#ffffff;color:#2c3e50;border:1px solid #b0c4d8;'
            'border-radius:3px;padding:3px 6px;min-height:22px;')
        preset_form.addRow('Satellite / missione:', self.e_preset)
        layout.addWidget(_group('Preset satellitare', preset_form))

        # ── PS Ascending ──────────────────────────────────────────────────────
        asc_form = QFormLayout()
        self.e_ps_asc = _layer_combo(QgsMapLayerProxyModel.PointLayer)
        self.e_ps_asc.layerChanged.connect(self._on_asc_layer_changed)
        asc_form.addRow('Layer PS:', self.e_ps_asc)

        self.e_vel_asc = QgsFieldComboBox()
        self.e_vel_asc.setFilters(QgsFieldProxyModel.Numeric)
        self.e_vel_asc.setLayer(self.e_ps_asc.currentLayer())
        self.e_vel_asc.setStyleSheet('background:#ffffff;color:#2c3e50;border:1px solid #b0c4d8;border-radius:3px;padding:3px 6px;min-height:22px;')
        asc_form.addRow('Campo velocità LOS:', self.e_vel_asc)

        self.e_az_asc = QDoubleSpinBox()
        self.e_az_asc.setRange(-360, 360); self.e_az_asc.setDecimals(4)
        self.e_az_asc.setValue(349.0); self.e_az_asc.setSuffix(' °')
        self.e_az_asc.setToolTip(
            'Heading del satellite: direzione di volo misurata da Nord in senso orario.\n'
            'Ascending tipico: circa −10° ÷ −15° (volo verso NNW).\n'
            'Descending tipico: circa 190° ÷ 195° (volo verso SSE).')
        asc_form.addRow('Azimut (track angle):', self.e_az_asc)

        self.e_on_asc = QDoubleSpinBox()
        self.e_on_asc.setRange(0, 90); self.e_on_asc.setDecimals(4)
        self.e_on_asc.setValue(42.0); self.e_on_asc.setSuffix(' °')
        self.e_on_asc.setToolTip(
            'Angolo off-nadir (incidenza): angolo tra la verticale locale e la LOS.\n'
            'Valore tipico Sentinel-1: 20°–46° a seconda del beam.')
        asc_form.addRow('Off-nadir (incidence angle):', self.e_on_asc)

        layout.addWidget(_group('Geometria Ascending', asc_form))

        # ── PS Descending ─────────────────────────────────────────────────────
        desc_form = QFormLayout()
        self.e_ps_desc = _layer_combo(QgsMapLayerProxyModel.PointLayer)
        self.e_ps_desc.layerChanged.connect(self._on_desc_layer_changed)
        desc_form.addRow('Layer PS:', self.e_ps_desc)

        self.e_vel_desc = QgsFieldComboBox()
        self.e_vel_desc.setFilters(QgsFieldProxyModel.Numeric)
        self.e_vel_desc.setLayer(self.e_ps_desc.currentLayer())
        self.e_vel_desc.setStyleSheet('background:#ffffff;color:#2c3e50;border:1px solid #b0c4d8;border-radius:3px;padding:3px 6px;min-height:22px;')
        desc_form.addRow('Campo velocità LOS:', self.e_vel_desc)

        self.e_az_desc = QDoubleSpinBox()
        self.e_az_desc.setRange(-360, 360); self.e_az_desc.setDecimals(4)
        self.e_az_desc.setValue(191.0); self.e_az_desc.setSuffix(' °')
        self.e_az_desc.setToolTip('Heading descending: tipicamente 180° + heading ascending.')
        desc_form.addRow('Azimut (track angle):', self.e_az_desc)

        self.e_on_desc = QDoubleSpinBox()
        self.e_on_desc.setRange(0, 90); self.e_on_desc.setDecimals(4)
        self.e_on_desc.setValue(38.0); self.e_on_desc.setSuffix(' °')
        self.e_on_desc.setToolTip('Angolo off-nadir descending.')
        desc_form.addRow('Off-nadir (incidence angle):', self.e_on_desc)

        layout.addWidget(_group('Geometria Descending', desc_form))

        # ── Tipo di looking ───────────────────────────────────────────────────
        look_form = QFormLayout()
        self.e_right_looking = QCheckBox('Right-looking (standard)')
        self.e_right_looking.setChecked(True)
        self.e_right_looking.setStyleSheet('color:#2c3e50;')
        self.e_right_looking.setToolTip(
            'Quasi tutti i satelliti SAR sono right-looking (guardano a destra\n'
            'rispetto alla direzione di volo). Deselezionare solo per sensori\n'
            'left-looking (es. alcune modalità COSMO-SkyMed in configurazione speciale).')
        look_form.addRow('Geometria sensore:', self.e_right_looking)
        layout.addWidget(_group('Tipo di acquisizione', look_form))

        # Connette il preset alla compilazione automatica degli angoli
        self.e_preset.currentTextChanged.connect(self._on_preset_changed)

        # ── Output ────────────────────────────────────────────────────────────
        out_layout = QVBoxLayout()
        for label, attr in [
            ('Centroidi EWUD (punti):',         'e_out_centr'),
            ('Poligoni EWUD (opzionale):',       'e_out_poly'),
        ]:
            out_layout.addWidget(QLabel(label))
            row, edit = _output_row(label)
            setattr(self, attr, edit)
            out_layout.addLayout(row)

        layout.addWidget(_group('Output', out_layout))

        layout.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        return widget

    # ──────────────────────────────────────────────────────────────────────────
    # Slot per aggiornare i FieldComboBox al cambio layer
    # ──────────────────────────────────────────────────────────────────────────
    def _on_grid_layer_changed(self, layer):
        self.e_id_griglia.setLayer(layer)

    def _on_asc_layer_changed(self, layer):
        self.e_vel_asc.setLayer(layer)

    def _on_desc_layer_changed(self, layer):
        self.e_vel_desc.setLayer(layer)

    def _on_preset_changed(self, name):
        """Precompila gli angoli quando si seleziona un satellite dal menu preset."""
        # Difesa: i widget angolari potrebbero non essere ancora stati creati
        # se il segnale viene emesso durante la costruzione del dialogo.
        if not hasattr(self, 'e_az_asc'):
            return
        val = self._presets.get(name)
        if val is None:
            return   # 'Personalizzato...' → nessuna modifica
        az_asc, on_asc, az_desc, on_desc, right = val
        for w in (self.e_az_asc, self.e_on_asc, self.e_az_desc, self.e_on_desc):
            w.blockSignals(True)
        self.e_az_asc.setValue(az_asc)
        self.e_on_asc.setValue(on_asc)
        self.e_az_desc.setValue(az_desc)
        self.e_on_desc.setValue(on_desc)
        self.e_right_looking.setChecked(right)
        for w in (self.e_az_asc, self.e_on_asc, self.e_az_desc, self.e_on_desc):
            w.blockSignals(False)



    # ══════════════════════════════════════════════════════════════════════════
    # Validazione
    # ══════════════════════════════════════════════════════════════════════════
    def _validate_grid_tab(self):
        if self.g_extent.outputExtent().isNull():
            return False, "Specifica l'estensione della griglia."
        if self.g_ps_asc.currentLayer() is None:
            return False, 'Seleziona il layer PS Ascendenti.'
        if self.g_ps_desc.currentLayer() is None:
            return False, 'Seleziona il layer PS Discendenti.'
        return True, ''

    def _validate_ewud_tab(self):
        if self.e_griglia.currentLayer() is None:
            return False, 'Seleziona la griglia di ricampionamento.'
        if not self.e_id_griglia.currentField():
            return False, 'Seleziona il campo ID della griglia.'
        if self.e_ps_asc.currentLayer() is None:
            return False, 'Seleziona il layer PS Ascending.'
        if not self.e_vel_asc.currentField():
            return False, 'Seleziona il campo velocità ascending.'
        if self.e_ps_desc.currentLayer() is None:
            return False, 'Seleziona il layer PS Descending.'
        if not self.e_vel_desc.currentField():
            return False, 'Seleziona il campo velocità descending.'
        return True, ''

    # ══════════════════════════════════════════════════════════════════════════
    # Esecuzione
    # ══════════════════════════════════════════════════════════════════════════
    def _run(self):
        tab = self.tabs.currentIndex()
        if tab == 0:
            ok, msg = self._validate_grid_tab()
            if not ok:
                QMessageBox.warning(self, 'Parametri mancanti', msg)
                return
            params      = self._collect_grid_params()
            algo_id     = 'native:creategrid'   # verrà orchestrato internamente
            self._run_create_grid(params)
        else:
            ok, msg = self._validate_ewud_tab()
            if not ok:
                QMessageBox.warning(self, 'Parametri mancanti', msg)
                return
            self._run_ewud()

    # ──────────────────────────────────────────────────────────────────────────
    # Raccolta parametri
    # ──────────────────────────────────────────────────────────────────────────
    def _collect_grid_params(self):
        return {
            'estensione_griglia': self.g_extent.outputExtent(),
            'lato_cella':         self.g_cell.value(),
            'tipo_cella':         'hex' if self.rb_hex.isChecked() else 'square',
            'ps_ascendenti':      self.g_ps_asc.currentLayer(),
            'ps_discendenti':     self.g_ps_desc.currentLayer(),
            'Egms_grid':          self.g_out.text() or 'TEMPORARY_OUTPUT',
        }

    def _collect_ewud_params(self, griglia_layer, id_field):
        return {
            'griglia_ricamp': griglia_layer,
            'id_griglia':     id_field,
            'ps_asc':         self.e_ps_asc.currentLayer(),
            'vel_asc':        self.e_vel_asc.currentField(),
            'azimut_asc':     self.e_az_asc.value(),
            'offnadir_asc':   self.e_on_asc.value(),
            'ps_desc':        self.e_ps_desc.currentLayer(),
            'vel_desc':       self.e_vel_desc.currentField(),
            'azimut_desc':    self.e_az_desc.value(),
            'offnadir_desc':  self.e_on_desc.value(),
            'right_looking':  self.e_right_looking.isChecked(),
            'Centroidi_ewud': self.e_out_centr.text() or 'TEMPORARY_OUTPUT',
            'Poligoni_ewud':  self.e_out_poly.text()  or 'TEMPORARY_OUTPUT',
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Orchestrazione algoritmi
    # ──────────────────────────────────────────────────────────────────────────
    def _run_create_grid(self, params):
        self._log_clear()
        self._log('<b>▶ Avvio: Crea Griglia PS</b>')
        self._set_running(True)

        from .runner_grid import GridRunner
        self.runner = GridRunner(params)
        self.runner.progress.connect(self.progress_bar.setValue)
        self.runner.log.connect(self._log)
        self.runner.finished.connect(self._on_grid_finished)
        self.runner.error.connect(self._on_error)
        self.runner.start()

    def _run_ewud(self):
        self._log_clear()
        self._log('<b>▶ Avvio: Decomposizione EWUD</b>')
        self._set_running(True)

        from .runner_ewud import EwudRunner
        griglia_layer = self.e_griglia.currentLayer()
        id_field      = self.e_id_griglia.currentField()
        self.runner   = EwudRunner(self._collect_ewud_params(griglia_layer, id_field))
        self.runner.progress.connect(self.progress_bar.setValue)
        self.runner.log.connect(self._log)
        self.runner.finished.connect(self._on_ewud_finished)
        self.runner.error.connect(self._on_error)
        self.runner.start()



    # ──────────────────────────────────────────────────────────────────────────
    # Callbacks completamento
    # ──────────────────────────────────────────────────────────────────────────
    def _on_grid_finished(self, result, grid_layer):
        self._set_running(False)
        self._log('<span style="color:#2ecc71; font-weight:bold">✔ Griglia PS creata con successo.</span>')
        self.progress_bar.setValue(100)
        if grid_layer:
            QgsProject.instance().addMapLayer(grid_layer)
            self._log(f'Layer aggiunto alla mappa: <b>{grid_layer.name()}</b>')

    def _on_ewud_finished(self, result, layers):
        self._set_running(False)
        self._log('<span style="color:#2ecc71; font-weight:bold">✔ Decomposizione EWUD completata.</span>')
        self.progress_bar.setValue(100)

        import os
        plugin_dir = os.path.dirname(__file__)

        # Mappa nome layer → file QML da applicare
        QML_MAP = {
            'Centroidi_EWUD': os.path.join(plugin_dir, 'insar_ewud_centroidi.qml'),
            'Poligoni_EWUD':  os.path.join(plugin_dir, 'insar_ewud_poligoni.qml'),
        }

        # Raccoglie i layer validi indicizzati per nome (chiave interna,
        # usata anche per la mappa QML sopra)
        valid = {}
        for name, lyr in layers:
            if lyr and lyr.isValid():
                # Se il layer proviene da un file permanente (non da memoria),
                # usa il nome del file come nome visualizzato, per distinguere
                # facilmente run diversi salvati con nomi diversi.
                src = lyr.source() or ''
                gpkg_path = src.split('|')[0]  # rimuove eventuale '|layername=...'
                if gpkg_path and os.path.isfile(gpkg_path):
                    display_name = os.path.splitext(os.path.basename(gpkg_path))[0] or name
                else:
                    display_name = name
                lyr.setName(display_name)
                valid[name] = lyr

        if not valid:
            self._log('<span style="color:#f39c12">⚠ Nessun layer aggiunto (output temporanei).</span>')
            return

        # Applica le legende QML prima di aggiungere alla mappa
        for name, lyr in valid.items():
            qml_path = QML_MAP.get(name)
            if qml_path and os.path.exists(qml_path):
                msg, ok = lyr.loadNamedStyle(qml_path)
                if ok:
                    self._log(f'Legenda applicata: <b>{name}</b>')
                else:
                    self._log(f'<span style="color:#f39c12">⚠ Legenda non caricata per {name}: {msg}</span>')

        # Aggiunge i layer alla mappa: Poligoni (in basso), Centroidi (in alto)
        root = QgsProject.instance().layerTreeRoot()
        for name in ('Poligoni_EWUD', 'Centroidi_EWUD'):
            lyr = valid.get(name)
            if lyr is None:
                continue
            QgsProject.instance().addMapLayer(lyr, False)
            root.insertLayer(0, lyr)
            self._log(f'Layer aggiunto: <b>{name}</b>')

    def _on_error(self, tb):
        self._set_running(False)
        self._log(f'<span style="color:#e74c3c; font-weight:bold">✖ ERRORE:</span><br>'
                  f'<span style="color:#e74c3c; font-size:10px">{tb.replace(chr(10),"<br>")}</span>')
        QMessageBox.critical(self, 'Errore durante l\'esecuzione',
                             'Si è verificato un errore. Controlla il log per i dettagli.')

    # ──────────────────────────────────────────────────────────────────────────
    # Utilità UI
    # ──────────────────────────────────────────────────────────────────────────
    def _set_running(self, running):
        self.btn_run.setEnabled(not running)
        self.btn_run.setText('⏳ Elaborazione…' if running else '▶  Esegui')
        if not running:
            self.progress_bar.setValue(0)

    def _log(self, html):
        self.log_box.append(html)
        self.log_box.verticalScrollBar().setValue(
            self.log_box.verticalScrollBar().maximum()
        )

    def _log_clear(self):
        self.log_box.clear()
        self.progress_bar.setValue(0)
