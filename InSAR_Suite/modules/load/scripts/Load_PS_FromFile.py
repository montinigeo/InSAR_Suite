import os
from qgis.PyQt.QtCore import QObject, pyqtSlot
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QFileDialog, QInputDialog
from qgis.core import QgsProject, QgsVectorLayer
from osgeo import ogr
from qgis.core import Qgis, QgsMessageLog


class _SelectionHandler(QObject):
    """Handler ancorato a Qt per evitare garbage collection da parte del GC Python."""
    def __init__(self, quadro_layer, campo_nome_layer, file_path, ext, iface, set_visible=True):
        super().__init__()
        self.quadro_layer = quadro_layer
        self.campo_nome_layer = campo_nome_layer
        self.file_path = file_path
        self.ext = ext
        self.iface = iface
        self.set_visible = set_visible

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
                # LAYER PUNTUALI ACCESI 🔥
                QgsProject.instance().layerTreeRoot().findLayer(lyr.id()).setItemVisibilityChecked(self.set_visible)
            else:
                self.iface.messageBar().pushWarning("Load_PS", f"Layer '{nome}' non trovato.")


class LoadPS_FromFile:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.action = None

        self.layer_selection_handlers = {}
        QgsProject.instance().layersWillBeRemoved.connect(self.on_layers_will_be_removed)

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, 'icons', 'Load_PS_FromFile.png')
        self.action = QAction(QIcon(icon_path), "Carica PS da File", self.iface.mainWindow())
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
                    QgsMessageLog.logMessage(f"InSAR Suite: eccezione ignorata: {_e}", "InSAR Suite", level=Qgis.MessageLevel.Info)
                    pass

        self.layer_selection_handlers.clear()

        try:
            QgsProject.instance().layersWillBeRemoved.disconnect(self.on_layers_will_be_removed)
        except Exception as _e:
            QgsMessageLog.logMessage(f"InSAR Suite: eccezione ignorata: {_e}", "InSAR Suite", level=Qgis.MessageLevel.Info)
            pass

    def on_layers_will_be_removed(self, layer_ids):
        for layer_id in layer_ids:
            if layer_id in self.layer_selection_handlers:
                layer = QgsProject.instance().mapLayer(layer_id)
                if layer:
                    try:
                        layer.selectionChanged.disconnect(self.layer_selection_handlers[layer_id].on_selection_changed)
                    except Exception as _e:
                        QgsMessageLog.logMessage(f"InSAR Suite: eccezione ignorata: {_e}", "InSAR Suite", level=Qgis.MessageLevel.Info)
                        pass
                del self.layer_selection_handlers[layer_id]

    def get_polygon_layers(self, file_path):
        polygon_layers = []
        ds = ogr.Open(file_path)
        if ds is None:
            return polygon_layers

        for i in range(ds.GetLayerCount()):
            layer = ds.GetLayerByIndex(i)
            geom = layer.GetGeomType()
            if geom in [ogr.wkbPolygon, ogr.wkbMultiPolygon,
                        ogr.wkbPolygon25D, ogr.wkbMultiPolygon25D]:
                polygon_layers.append(layer.GetName())
        return polygon_layers

    def run(self):
        try:
            tipo, ok = QInputDialog.getItem(
                None,
                "Scegli tipo di sorgente",
                "Seleziona il tipo di file da caricare:",
                ["GeoPackage (.gpkg)", "Shapefile (.shp)", "Geodatabase (.gdb)"],
                0, False
            )
            if not ok:
                return

            polygon_layers = []
            file_path = None
            ext = None

            if tipo == "Geodatabase (.gdb)":
                file_path = QFileDialog.getExistingDirectory(
                    None,
                    "Seleziona cartella Geodatabase (.gdb)"
                )
                if not file_path:
                    return
                ext = ".gdb"
                ds = ogr.Open(file_path)
                if ds is None:
                    self.iface.messageBar().pushWarning("Load_PS", "Errore apertura GDB.")
                    return

                for i in range(ds.GetLayerCount()):
                    layer = ds.GetLayerByIndex(i)
                    geom = layer.GetGeomType()
                    if geom in [ogr.wkbPolygon, ogr.wkbMultiPolygon,
                                ogr.wkbPolygon25D, ogr.wkbMultiPolygon25D]:
                        polygon_layers.append(layer.GetName())

            elif tipo == "GeoPackage (.gpkg)":
                file_path, _ = QFileDialog.getOpenFileName(
                    None,
                    "Seleziona file GeoPackage (.gpkg)",
                    "",
                    "GeoPackage (*.gpkg)"
                )
                if not file_path:
                    return
                ext = ".gpkg"
                polygon_layers = self.get_polygon_layers(file_path)

            elif tipo == "Shapefile (.shp)":
                file_path, _ = QFileDialog.getOpenFileName(
                    None,
                    "Seleziona file Shapefile (.shp)",
                    "",
                    "Shapefile (*.shp)"
                )
                if not file_path:
                    return
                ext = ".shp"
                layer = QgsVectorLayer(file_path, os.path.basename(file_path), "ogr")
                if not layer.isValid() or layer.geometryType() != 2:
                    self.iface.messageBar().pushWarning("Load_PS", "Lo shapefile non è un layer poligonale valido.")
                    return
                polygon_layers = [os.path.basename(file_path)]

            else:
                self.iface.messageBar().pushWarning("Load_PS", "Formato non supportato.")
                return

            if not polygon_layers:
                self.iface.messageBar().pushWarning("Load_PS", "Nessun layer poligonale trovato.")
                return

            if len(polygon_layers) == 1:
                quadro_name = polygon_layers[0]
            else:
                quadro_name, ok = QInputDialog.getItem(
                    None, "Seleziona il layer",
                    "Scegli il layer poligonale da caricare:",
                    polygon_layers, 0, False)
                if not ok:
                    return

            if ext in [".gpkg", ".gdb"]:
                quadro_uri = f"{file_path}|layername={quadro_name}"
            else:
                quadro_uri = file_path

            quadro_layer = QgsVectorLayer(quadro_uri, quadro_name, "ogr")
            if not quadro_layer.isValid():
                self.iface.messageBar().pushWarning("Load_PS", f"Errore nel caricamento di {quadro_name}")
                return

            QgsProject.instance().addMapLayer(quadro_layer)

            # QUADRO DI UNIONE SPENTO
            QgsProject.instance().layerTreeRoot().findLayer(quadro_layer.id()).setItemVisibilityChecked(False)

            self.activate_selection(quadro_layer, file_path, ext)

        except Exception as e:
            self.iface.messageBar().pushCritical("Load_PS", str(e))

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
            level=0, duration=8
        )

        layer_id = quadro_layer.id()

        # Disconnetti eventuale handler precedente
        if layer_id in self.layer_selection_handlers:
            try:
                quadro_layer.selectionChanged.disconnect(self.layer_selection_handlers[layer_id].on_selection_changed)
            except Exception as _e:
                QgsMessageLog.logMessage(f"InSAR Suite: eccezione ignorata: {_e}", "InSAR Suite", level=Qgis.MessageLevel.Info)
                pass

        # Crea handler come QObject persistente
        handler = _SelectionHandler(quadro_layer, campo_nome_layer, file_path, ext, self.iface, set_visible=True)
        self.layer_selection_handlers[layer_id] = handler
        quadro_layer.selectionChanged.connect(handler.on_selection_changed)
