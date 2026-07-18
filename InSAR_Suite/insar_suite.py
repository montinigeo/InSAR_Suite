"""
InSAR Suite — Plugin principale.

Crea una toolbar dedicata con un'icona per ciascuno strumento:
  [Load da File] [Ricarica quadro] | [EWUD] | [VIS] | [VN] [AA] [STS] [NL] [GEO]
                  LOAD                                        TS

Ordine da sinistra a destra: Load → EWUD → VIS → TS
"""

import os
from qgis.PyQt.QtWidgets import QAction, QToolBar, QMessageBox, QWidget
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt
from qgis.core import Qgis, QgsMessageLog

# ── Verifica dipendenze all'avvio ─────────────────────────────────────────────
_MISSING_DEPS = []
for _dep in ['pandas', 'numpy', 'matplotlib', 'scipy',
             'statsmodels', 'pyproj', 'pykrige', 'mplcursors', 'pwlf']:
    try:
        __import__(_dep)
    except ImportError:
        _MISSING_DEPS.append(_dep)

if _MISSING_DEPS:
    def _warn_missing():
        QMessageBox.warning(
            None,
            'InSAR Suite – Dipendenze mancanti',
            'Le seguenti librerie Python non sono installate:\n\n'
            + '\n'.join(f'  • {d}' for d in _MISSING_DEPS)
            + '\n\nIl modulo TS non funzionerà correttamente.\n\n'
            'Per installarle:\n'
            '• Windows: apri il terminale OSGeo4W e digita:\n'
            '  pip install ' + ' '.join(_MISSING_DEPS) + '\n\n'
            '• Linux/macOS: da terminale:\n'
            '  pip install ' + ' '.join(_MISSING_DEPS)
        )


class InSARSuite:

    MENU_NAME  = '&InSAR Suite'
    TOOLBAR_TITLE = 'InSAR Suite'

    def __init__(self, iface):
        self.iface       = iface
        self.toolbar     = None
        self._actions    = []          # tutte le QAction della toolbar
        self._load_mod   = None        # LoadModule (gestisce segnali Qt)
        self._ewud_dlg   = None        # EgmsDialog (instanziato a richiesta)
        self._vis_dlg    = None        # InSARVISDialog (instanziato a richiesta)
        self._ts_dlg     = None        # QDialog contenitore del tab TS

    # ──────────────────────────────────────────────────────────────────────────
    def initGui(self):
        plugin_dir = os.path.dirname(__file__)
        icons_dir  = os.path.join(plugin_dir, 'icons')

        def icon(name):
            p = os.path.join(icons_dir, name)
            return QIcon(p) if os.path.exists(p) else QIcon()

        # ── Pre-carica le librerie TS in background ───────────────────────────
        # Le librerie pesanti (pandas, matplotlib, ecc.) vengono importate in un
        # thread daemon così il primo utilizzo del modulo TS è immediato.
        import threading
        def _preload_ts_libs():
            try:
                import pandas, numpy, matplotlib, scipy, statsmodels, mplcursors
            except Exception as _e:
                QgsMessageLog.logMessage(f"InSAR Suite: eccezione ignorata: {_e}", "InSAR Suite", level=Qgis.MessageLevel.Info)
                pass
        threading.Thread(target=_preload_ts_libs, daemon=True).start()

        # ── Avviso dipendenze mancanti ────────────────────────────────────────
        if _MISSING_DEPS:
            from qgis.PyQt.QtCore import QTimer
            QTimer.singleShot(1000, _warn_missing)

        # ── Crea la toolbar dedicata ──────────────────────────────────────────
        self.toolbar = self.iface.mainWindow().addToolBar(self.TOOLBAR_TITLE)
        self.toolbar.setObjectName('InSARSuiteToolbar')

        # ── Inizializza modulo Load ────────────────────────────────────────────
        from .modules.load.load_module import LoadModule
        self._load_mod = LoadModule(self.iface)
        self._load_mod.init()

        # ── Definizione azioni ────────────────────────────────────────────────
        # ┌───────────────────────────────────────────────────────────────────┐
        # │  LOAD  │  EWUD  │  VIS  │  TS x6                │  POLYG          │
        # └───────────────────────────────────────────────────────────────────┘

        actions_def = [
            # --- LOAD ----------------------------------------------------------
            {
                'icon':    'Load_PS_FromFile.png',
                'text':    'Carica PS da File',
                'tooltip': 'Carica layer PS-InSAR da GeoPackage, Shapefile o GDB',
                'slot':    self._run_load_file,
                'section': 'LOAD',
            },
            {
                'icon':    'Load_PS_FromProject.png',
                'text':    'Ricarica Quadro di Unione',
                'tooltip': 'Riattiva la selezione su un quadro già presente nel progetto',
                'slot':    self._run_load_project,
                'section': 'LOAD',
            },
            # --- EWUD ----------------------------------------------------------
            {
                'icon':    'icon_ewud.png',
                'text':    'InSAR EWUD',
                'tooltip': 'Crea Griglia PS e Decomposizione East-West / Up-Down',
                'slot':    self._run_ewud,
                'section': 'EWUD',
            },
            # --- VIS -----------------------------------------------------------
            {
                'icon':    'icon_vis.png',
                'text':    'InSAR VIS',
                'tooltip': 'Calcolo percentuale di movimento rilevabile (pc_mov)',
                'slot':    self._run_vis,
                'section': 'VIS',
            },
            # --- TS ------------------------------------------------------------
            {
                'icon':    'normalita.png',
                'text':    'TS – Qualità del dato',
                'tooltip': 'Analisi qualità e omogeneità dei PS selezionati',
                'slot':    lambda: self._run_ts_script(0),
                'section': 'TS',
            },
            {
                'icon':    'auto.png',
                'text':    'TS – Analisi Automatica',
                'tooltip': 'Analisi serie storica — serie media + layer temporaneo QGIS',
                'slot':    lambda: self._run_ts_script(1),
                'section': 'TS',
            },
            {
                'icon':    'scomposizione.png',
                'text':    'TS – Scomposizione',
                'tooltip': 'Scomposizione serie storica in trend, stagionalità, residui',
                'slot':    lambda: self._run_ts_script(2),
                'section': 'TS',
            },
            {
                'icon':    'non_lineare.png',
                'text':    'TS – Analisi Non Lineare',
                'tooltip': 'Analisi di linearità della serie storica (piecewise)',
                'slot':    lambda: self._run_ts_script(3),
                'section': 'TS',
            },
            {
                'icon':    'anomalie.png',
                'text':    'TS – Anomalie temporali',
                'tooltip': 'Rilevamento acquisizioni anomale nella serie storica',
                'slot':    lambda: self._run_ts_script(4),
                'section': 'TS',
            },
            {
                'icon':    'confronto.png',
                'text':    'TS \u2013 Confronto zone',
                'tooltip': 'Confronto serie storiche medie tra zone diverse',
                'slot':    lambda: self._run_ts_script(5),
                'section': 'TS',
            },
            # --- POLYG ---------------------------------------------------------
            {
                'icon':    'icon_polyg.png',
                'text':    'InSAR Polygons',
                'tooltip': 'Delimitazione aree di deformazione PS',
                'slot':    self._run_polyg,
                'section': 'POLYG',
            },
        ]

        prev_section = None
        for ad in actions_def:
            # Separatore colorato tra sezioni diverse
            if prev_section is not None and ad['section'] != prev_section:
                sep = QWidget()
                sep.setFixedWidth(8)
                sep.setStyleSheet(
                    'QWidget {'
                    '  border-left: 2px solid #3498db;'
                    '  margin-top: 4px;'
                    '  margin-bottom: 4px;'
                    '}'
                )
                self.toolbar.addWidget(sep)

            action = QAction(icon(ad['icon']), ad['text'], self.iface.mainWindow())
            action.setToolTip(ad['tooltip'])
            action.triggered.connect(ad['slot'])

            self.toolbar.addAction(action)
            self.iface.addPluginToMenu(self.MENU_NAME, action)
            self._actions.append(action)

            prev_section = ad['section']

        # Separatore finale dopo POLYG
        sep_end = QWidget()
        sep_end.setFixedWidth(8)
        sep_end.setStyleSheet(
            'QWidget {'
            '  border-left: 2px solid #3498db;'
            '  margin-top: 4px;'
            '  margin-bottom: 4px;'
            '}'
        )
        self.toolbar.addWidget(sep_end)

        QgsMessageLog.logMessage(
            '✅ Plugin InSAR Suite caricato correttamente.',
            'InSAR Suite', Qgis.Info
        )

    # ──────────────────────────────────────────────────────────────────────────
    def unload(self):
        # Chiude tutte le finestre matplotlib prima che Qt le distrugga
        # per evitare crash "access violation" alla chiusura di QGIS
        try:
            import matplotlib.pyplot as plt
            plt.close('all')
        except Exception as _e:
            QgsMessageLog.logMessage(f"InSAR Suite: eccezione ignorata: {_e}", "InSAR Suite", level=Qgis.MessageLevel.Info)
            pass
        # Rimuove le azioni dal menu e dalla toolbar
        for action in self._actions:
            self.iface.removePluginMenu(self.MENU_NAME, action)

        if self.toolbar:
            self.toolbar.clear()
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar = None

        # Disconnette i segnali Qt del modulo Load
        if self._load_mod:
            self._load_mod.unload()

    # ──────────────────────────────────────────────────────────────────────────
    # Slot LOAD
    # ──────────────────────────────────────────────────────────────────────────
    def _run_load_file(self):
        self._load_mod.run_from_file()

    def _run_load_project(self):
        self._load_mod.run_from_project()

    # ──────────────────────────────────────────────────────────────────────────
    # Slot EWUD
    # ──────────────────────────────────────────────────────────────────────────
    def _run_ewud(self):
        from .modules.ewud.dialog import EgmsDialog
        dlg = EgmsDialog(self.iface)
        dlg.exec_()

    # ──────────────────────────────────────────────────────────────────────────
    # Slot VIS
    # ──────────────────────────────────────────────────────────────────────────
    def _run_vis(self):
        from .modules.vis.dialog import InSARVISDialog
        # show() non bloccante: l'utente può continuare a usare QGIS
        self._vis_dlg = InSARVISDialog(self.iface)
        self._vis_dlg.show()

    def _run_polyg(self):
        from .modules.polyg.dialog import InSARPolygonsDialog
        self._polyg_dlg = InSARPolygonsDialog(self.iface)
        self._polyg_dlg.show()

    # ──────────────────────────────────────────────────────────────────────────
    # Slot TS
    # ──────────────────────────────────────────────────────────────────────────
    def _run_ts_script(self, script_idx):
        """Esegue direttamente lo script TS richiesto.
        Tutti i controlli (layer attivo, selezione, campi data) sono gestiti
        internamente dagli script tramite QMessageBox."""
        import os, runpy
        from .modules.ts.ts_widget import SCRIPT_DEFS
        from qgis.PyQt.QtWidgets import QMessageBox

        sd = SCRIPT_DEFS[script_idx]
        scripts_dir = os.path.join(os.path.dirname(__file__), 'modules', 'ts', 'scripts')
        script_path = os.path.join(scripts_dir, sd['script'])

        if not os.path.exists(script_path):
            QMessageBox.critical(None, 'InSAR TS – Script non trovato', script_path)
            return

        try:
            runpy.run_path(script_path, init_globals={'iface': self.iface})
        except SystemExit:
            pass  # uscita pulita dopo QMessageBox nello script
        except Exception as e:
            QMessageBox.critical(None, 'InSAR TS – Errore', f'{sd["err"]}:\n{e}')
