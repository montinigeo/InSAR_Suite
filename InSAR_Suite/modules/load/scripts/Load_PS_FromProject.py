# Load_PS_FromProject.py

import os
from qgis.PyQt.QtCore import QObject, pyqtSlot
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtGui import QAction  # QGIS 4 / Qt6
from qgis.PyQt.QtWidgets import QInputDialog
from qgis.core import Qgis, QgsProject, QgsVectorLayer
from qgis.core import QgsMessageLog


class _SelectionHandler(QObject):
    """Handler ancorato a Qt per evitare garbage collection da parte del GC Python."""
    def __init__(self, quadro_layer, campo_nome_layer, file_path, ext, iface):
        super().__init__()
        self.quadro_layer = quadro_layer
        self.campo_nome_layer = campo_nome_layer
        self.file_path = file_path
        self.ext = ext
        self.iface = iface

    @pyqtSlot()
    def on_selection_changed(self):
        selected = self.quadro_layer.selectedFeatures()
        if not selected:
            return

        nomi = [f[self.campo_nome_layer] for f in selected]

        for nome in nomi:
            if self.ext in [".gpkg", ".gdb"]:
                uri = f"{self.file_path}|layername={nome}"
            else:
                folder = os.path.dirname(self.file_path)
                uri = os.path.join(folder, f"{nome}.shp")

            lyr = QgsVectorLayer(uri, nome, "ogr")
            if lyr.isValid():
                QgsProject.instance().addMapLayer(lyr)
            else:
                self.iface.messageBar().pushWarning(
                    "Load_PS", f"Layer '{nome}' non trovato.")


class LoadPS_FromProject:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.action = None

        self.layer_selection_handlers = {}

        QgsProject.instance().layersWillBeRemoved.connect(self.on_layers_will_be_removed)

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, 'icons', 'Load_PS_FromProject.png')
        self.action = QAction(QIcon(icon_path), "Ricarica quadro di unione", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("InSAR Load", self.action)

    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        self.iface.removePluginMenu("InSAR Load", self.action)

        for layer_id, handler in self.layer_selection_handlers.items():
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer:
                try:
                    layer.selectionChanged.disconnect(handler.on_selection_changed)
                except Exception as _e:
                    QgsMessageLog.logMessage(f"InSAR Suite: eccezione ignorata: {_e}", "InSAR Suite", level=Qgis.MessageLevel.Warning)
                    pass

        self.layer_selection_handlers.clear()

        try:
            QgsProject.instance().layersWillBeRemoved.disconnect(self.on_layers_will_be_removed)
        except Exception as _e:
            QgsMessageLog.logMessage(f"InSAR Suite: eccezione ignorata: {_e}", "InSAR Suite", level=Qgis.MessageLevel.Warning)
            pass

    def on_layers_will_be_removed(self, layer_ids):
        for layer_id in layer_ids:
            if layer_id in self.layer_selection_handlers:
                layer = QgsProject.instance().mapLayer(layer_id)
                if layer:
                    try:
                        layer.selectionChanged.disconnect(self.layer_selection_handlers[layer_id].on_selection_changed)
                    except Exception as _e:
                        QgsMessageLog.logMessage(f"InSAR Suite: eccezione ignorata: {_e}", "InSAR Suite", level=Qgis.MessageLevel.Warning)
                        pass
                del self.layer_selection_handlers[layer_id]

    # ------------------------------------------------------------------

    def run(self):
        try:
            # Trova tutti i layer poligonali del progetto
            layers = [
                lyr for lyr in QgsProject.instance().mapLayers().values()
                if isinstance(lyr, QgsVectorLayer) and lyr.geometryType() == 2
            ]

            if not layers:
                self.iface.messageBar().pushWarning("Load_PS", "Nessun layer poligonale nel progetto.")
                return

            nomi = [lyr.name() for lyr in layers]

            sel, ok = QInputDialog.getItem(
                None, "Quadro di Unione",
                "Seleziona il layer poligonale già nel progetto:",
                nomi, 0, False)
            if not ok:
                return

            quadro_layer = layers[nomi.index(sel)]

            # Recupero URI per capire il formato
            uri = quadro_layer.dataProvider().dataSourceUri()
            if "|layername=" in uri:
                file_path = uri.split("|")[0]
                ext = os.path.splitext(file_path)[1].lower()
            else:
                file_path = uri
                ext = os.path.splitext(uri)[1].lower()

            self.activate_selection(quadro_layer, file_path, ext)

        except Exception as e:
            self.iface.messageBar().pushCritical("Load_PS", str(e))

    # ------------------------------------------------------------------

    def activate_selection(self, quadro_layer, file_path, ext):

        campi = [f.name() for f in quadro_layer.fields()]
        campo_nome_layer, ok = QInputDialog.getItem(
            None, "Campo nomi layer puntuali",
            "Scegli il campo con i nomi dei layer:",
            campi, 0, False)
        if not ok:
            return

        self.iface.messageBar().pushMessage(
            "Load_PS",
            "Seleziona i poligoni per caricare i layer.",
            level=Qgis.MessageLevel.Info, duration=8
        )

        layer_id = quadro_layer.id()

        # Disconnetti eventuale handler precedente
        if layer_id in self.layer_selection_handlers:
            try:
                quadro_layer.selectionChanged.disconnect(self.layer_selection_handlers[layer_id].on_selection_changed)
            except Exception as _e:
                QgsMessageLog.logMessage(f"InSAR Suite: eccezione ignorata: {_e}", "InSAR Suite", level=Qgis.MessageLevel.Warning)
                pass

        # Crea handler come QObject persistente
        handler = _SelectionHandler(quadro_layer, campo_nome_layer, file_path, ext, self.iface)
        self.layer_selection_handlers[layer_id] = handler
        quadro_layer.selectionChanged.connect(handler.on_selection_changed)
