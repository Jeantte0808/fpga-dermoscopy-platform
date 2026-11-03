"""Cached graphics layers for presentation only; canonical result pixels stay intact."""

from dataclasses import dataclass

import numpy as np
from PySide6.QtGui import QImage, QPixmap

from .imaging import make_overlay


def _layer(background, overlay):
    pixels = np.empty((*background.shape[:2], 4), dtype=np.uint8)
    pixels[:, :, :3] = overlay[:, :, ::-1]
    pixels[:, :, 3] = np.where(np.any(overlay != background, axis=2), 255, 0)
    pixmap = QPixmap.fromImage(QImage(pixels.data, pixels.shape[1], pixels.shape[0],
        pixels.strides[0], QImage.Format.Format_RGBA8888).copy())
    if pixmap.isNull():
        raise ValueError("无法创建叠加显示资源")
    return pixmap


@dataclass
class OverlayPresentation:
    original: QPixmap
    enhanced: QPixmap
    original_layer: QPixmap
    enhanced_layer: QPixmap

    @classmethod
    def prepare(cls, request, result, original_pixmap, enhanced_pixmap):
        return cls(original_pixmap, enhanced_pixmap,
                   _layer(request.image_bgr, result.overlay_bgr),
                   _layer(result.enhanced_bgr, make_overlay(result.enhanced_bgr, result.mask)))

    def apply(self, view, enhanced: bool, visible: bool, opacity: float):
        view.show_overlay_layers(self.enhanced if enhanced else self.original,
                                 self.enhanced_layer if enhanced else self.original_layer,
                                 visible, opacity)
