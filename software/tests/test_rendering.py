import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QGraphicsPixmapItem
import pytest

from dermademo.gui import ImageView, SAMPLE_PATH
from test_gui import app, window, process_sample, wait_for_idle


def edge_pixels(app, smooth):
    view = ImageView("", smooth=smooth)
    view.resize(240, 240)
    view.show()
    image = np.zeros((16, 16, 3), np.uint8)
    image[:, 8:] = 255
    view.set_image(image)
    view.actual_size()
    view.zoom(10)
    app.processEvents()
    screenshot = view.viewport().grab().toImage()
    ratio = screenshot.devicePixelRatio()
    values = []
    for x in range(60, 180):
        # Samples are well inside the image, away from frame/background edges.
        pos = view.mapFromScene(QPointF(8, 8))
        px = round((pos.x() + x - 120) * ratio)
        py = round(pos.y() * ratio)
        if 0 <= px < screenshot.width() and 0 <= py < screenshot.height():
            values.append(screenshot.pixelColor(px, py).red())
    view.close()
    view.deleteLater()
    app.processEvents()
    return values


def test_actual_rendering_smooths_color_edges_but_keeps_mask_pixels(app):
    smooth = edge_pixels(app, True)
    nearest = edge_pixels(app, False)
    assert any(0 < value < 255 for value in smooth)
    assert set(nearest) == {0, 255}


def assert_modes(window):
    for index, panel in enumerate(window.panels):
        expected = Qt.TransformationMode.FastTransformation if index == 2 else Qt.TransformationMode.SmoothTransformation
        views = [panel.view] + ([panel.viewer.view] if panel.viewer is not None else [])
        for view in views:
            items = [item for item in view.scene.items() if isinstance(item, QGraphicsPixmapItem)]
            assert items and all(item.transformationMode() == expected for item in items)


def test_all_import_result_record_and_big_view_paths_use_correct_rendering(app, window, tmp_path):
    window.start_import(SAMPLE_PATH)
    wait_for_idle(app, window)
    assert window.panels[0].view.scene.items()[0].transformationMode() == Qt.TransformationMode.SmoothTransformation
    window.start_processing()
    wait_for_idle(app, window)
    original, result = window.image.copy(), window.result
    for panel in window.panels:
        panel.open_viewer()
    assert_modes(window)
    window.overlay_background.setCurrentIndex(1)
    window.opacity_slider.setValue(45)
    assert_modes(window)
    window.export_to(tmp_path)
    wait_for_idle(app, window)
    window.start_open_record(window.last_export)
    wait_for_idle(app, window)
    assert_modes(window)
    for panel in window.panels:
        panel.open_viewer()
    assert_modes(window)
    np.testing.assert_array_equal(window.image, original)
    np.testing.assert_array_equal(window.result.mask, result.mask)
    np.testing.assert_array_equal(window.result.overlay_bgr, result.overlay_bgr)
