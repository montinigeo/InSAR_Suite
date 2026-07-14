"""
Modulo Load per InSAR Suite.
Espone le due azioni di caricamento PS (da file e da progetto)
come semplici metodi callable, senza registrare toolbar/menu propri
(ci pensa il plugin principale).
"""

import os
from .scripts.Load_PS_FromFile import LoadPS_FromFile
from .scripts.Load_PS_FromProject import LoadPS_FromProject
from qgis.core import Qgis, QgsMessageLog


class LoadModule:
    """
    Wrapper leggero: istanzia i due gestori di caricamento e
    li inizializza senza aggiungere toolbar/menu (già gestiti dalla Suite).
    """

    def __init__(self, iface):
        self.iface = iface
        self._from_file    = LoadPS_FromFile(iface)
        self._from_project = LoadPS_FromProject(iface)

    def init(self):
        """Inizializza i gestori interni (senza creare toolbar/menu)."""
        # I gestori si collegano a QgsProject internamente nel loro __init__
        pass

    def unload(self):
        """Disconnette i signal handler Qt per evitare memory leak."""
        # Disconnette manualmente i segnali senza toccare toolbar/menu
        for h_dict in [
            self._from_file.layer_selection_handlers,
            self._from_project.layer_selection_handlers,
        ]:
            for layer_id, handler in h_dict.items():
                from qgis.core import QgsProject
                layer = QgsProject.instance().mapLayer(layer_id)
                if layer:
                    try:
                        layer.selectionChanged.disconnect(handler.on_selection_changed)
                    except Exception as _e:
                        QgsMessageLog.logMessage(f"InSAR Suite: eccezione ignorata: {_e}", "InSAR Suite", level=Qgis.MessageLevel.Warning)
                        pass
            h_dict.clear()

        # Disconnette i segnali globali del progetto
        from qgis.core import QgsProject
        for obj in [self._from_file, self._from_project]:
            try:
                QgsProject.instance().layersWillBeRemoved.disconnect(
                    obj.on_layers_will_be_removed
                )
            except Exception as _e:
                QgsMessageLog.logMessage(f"InSAR Suite: eccezione ignorata: {_e}", "InSAR Suite", level=Qgis.MessageLevel.Warning)
                pass

    def run_from_file(self):
        self._from_file.run()

    def run_from_project(self):
        self._from_project.run()
