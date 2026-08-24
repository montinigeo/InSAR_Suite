# -*- coding: utf-8 -*-
"""
Strumento mappa per disegnare a mano il poligono dell'area di studio.

Uso: clic sinistro aggiunge un vertice, clic destro (o doppio clic
sinistro) chiude il poligono ed emette il segnale 'finished' con la
QgsGeometry risultante (nel CRS del progetto). Tasto Esc annulla.
"""
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.core import QgsWkbTypes, QgsGeometry, QgsPointXY


class PolygonDrawTool(QgsMapTool):
    finished = pyqtSignal(object)   # QgsGeometry
    cancelled = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self._points = []
        self._rb = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        self._rb.setColor(QColor(41, 128, 185, 90))
        self._rb.setStrokeColor(QColor(41, 128, 185, 220))
        self._rb.setWidth(2)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _reset(self):
        self._points = []
        self._rb.reset(QgsWkbTypes.GeometryType.PolygonGeometry)

    def canvasPressEvent(self, event):
        pt = self.toMapCoordinates(event.pos())
        if event.button() == Qt.MouseButton.LeftButton:
            self._points.append(QgsPointXY(pt))
            self._rb.addPoint(pt, True)
        elif event.button() == Qt.MouseButton.RightButton:
            self._finish()

    def canvasDoubleClickEvent(self, event):
        self._finish()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._reset()
            self.cancelled.emit()

    def _finish(self):
        if len(self._points) < 3:
            self._reset()
            self.cancelled.emit()
            return
        geom = QgsGeometry.fromPolygonXY([self._points])
        self._reset()
        self.finished.emit(geom)

    def deactivate(self):
        self._reset()
        super().deactivate()
