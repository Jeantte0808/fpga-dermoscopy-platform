"""Qt graphics selection in original oriented pixel coordinates, independent of zoom."""
from math import floor, ceil

from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QEvent
from PySide6.QtGui import QPen
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsItem

from .gui import ImageView
from .roi import validate_rect


class ROISelector(ImageView):
    roi_changed = Signal(object)
    selection_committed = Signal()

    def __init__(self):
        super().__init__('打开方向校正后的原图，左键框选ROI')
        self.roi = None
        self.selection_enabled = True
        self._box, self._handles, self._gesture = None, [], None
        self.setAccessibleName('原图ROI框选；也可用旁边整数坐标输入框调整')

    def set_pixmap(self, pixmap):
        self._box, self._handles, self.roi, self._gesture = None, [], None, None
        super().set_pixmap(pixmap)

    def clear_image(self):
        self._box, self._handles, self.roi, self._gesture = None, [], None, None
        super().clear_image()

    def set_roi(self, rect):
        rect = validate_rect(rect, self.pixmap.width(), self.pixmap.height()) if rect is not None else None
        changed = rect != self.roi
        self.roi = rect
        if rect is None:
            if self._box:
                self._box.hide()
            for item in self._handles: item.hide()
        else:
            if self._box is None:
                self._box = QGraphicsRectItem()
                self._box.setZValue(10)
                self.scene.addItem(self._box)
                for _ in range(8):
                    handle = QGraphicsEllipseItem(-3, -3, 6, 6)
                    handle.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
                    handle.setZValue(11)
                    self.scene.addItem(handle)
                    self._handles.append(handle)
            x0, y0, x1, y1 = rect
            self._box.setRect(QRectF(x0, y0, x1-x0, y1-y0))
            self._box.show()
            for item, point in zip(self._handles, self._points(rect)):
                item.setPos(QPointF(*point)); item.show()
            self._recolor()
        if changed: self.roi_changed.emit(rect)

    @staticmethod
    def _points(r):
        x0,y0,x1,y1=r; xm,ym=(x0+x1)/2,(y0+y1)/2
        return [(x0,y0),(xm,y0),(x1,y0),(x1,ym),(x1,y1),(xm,y1),(x0,y1),(x0,ym)]

    def _recolor(self):
        if self._box is None: return
        color = self.palette().highlight().color()
        pen = QPen(color, 2); pen.setCosmetic(True)
        self._box.setPen(pen)
        for handle in self._handles:
            handle.setPen(pen); handle.setBrush(self.palette().base())

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, '_box'): self._recolor()

    def _point(self, event):
        p = self.mapToScene(event.position().toPoint())
        return QPointF(max(0., min(self.pixmap.width(), p.x())), max(0., min(self.pixmap.height(), p.y())))

    def mousePressEvent(self, event):
        if not self.selection_enabled or not self.has_image or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        if not self.scene.sceneRect().contains(self.mapToScene(event.position().toPoint())):
            event.accept(); return
        point = self._point(event)
        mode, handle = 'new', None
        if self.roi is not None:
            for i, p in enumerate(self._points(self.roi)):
                view_point = self.mapFromScene(QPointF(*p))
                if (view_point - event.position().toPoint()).manhattanLength() <= 12:
                    mode, handle = 'resize', i; break
            if mode == 'new':
                x0,y0,x1,y1=self.roi
                if x0 <= point.x() <= x1 and y0 <= point.y() <= y1: mode='move'
        self._gesture = mode, handle, point, self.roi
        if mode == 'new': self.set_roi(None)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._gesture is None:
            return super().mouseMoveEvent(event)
        mode, handle, anchor, original = self._gesture
        p = self._point(event)
        if mode == 'new':
            r = floor(min(anchor.x(),p.x())), floor(min(anchor.y(),p.y())), ceil(max(anchor.x(),p.x())), ceil(max(anchor.y(),p.y()))
        elif mode == 'move':
            x0,y0,x1,y1=original
            dx=max(-x0,min(self.pixmap.width()-x1,round(p.x()-anchor.x())))
            dy=max(-y0,min(self.pixmap.height()-y1,round(p.y()-anchor.y())))
            r=x0+dx,y0+dy,x1+dx,y1+dy
        else:
            x0,y0,x1,y1=original
            if handle in (0,6,7): x0=p.x()
            if handle in (2,3,4): x1=p.x()
            if handle in (0,1,2): y0=p.y()
            if handle in (4,5,6): y1=p.y()
            r=floor(min(x0,x1)),floor(min(y0,y1)),ceil(max(x0,x1)),ceil(max(y0,y1))
        if r[0] < r[2] and r[1] < r[3]: self.set_roi(r)
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._gesture is not None and event.button() == Qt.MouseButton.LeftButton:
            self.mouseMoveEvent(event)
            self._gesture = None
            self.selection_committed.emit()
            event.accept()
        else: super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.fit_image()
        event.accept()
