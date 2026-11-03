import numpy as np
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
import pytest

from test_gui import wait_for_idle

from dermademo.gui import ImageViewer, MainWindow, SAMPLE_PATH, create_application


@pytest.fixture(scope="module")
def app():
    return create_application()


@pytest.fixture
def window(app):
    window = MainWindow()
    window.show()
    app.processEvents()
    yield window
    for panel in window.panels:
        panel.close_viewer()
    window.close()
    window.deleteLater()
    app.processEvents()


def double_click(app, panel):
    QTest.mouseDClick(panel.view.viewport(), Qt.MouseButton.LeftButton,
                     pos=panel.view.viewport().rect().center())
    app.processEvents()


@pytest.mark.parametrize("index", range(4))
def test_double_click_opens_correct_color_or_mask_and_reuses_viewer(app, window, index):
    image = np.zeros((90, 120), np.uint8) if index == 2 else np.zeros((90, 120, 3), np.uint8)
    image[20:70, 30:90] = 255 if index == 2 else (40, 110, 200)
    panel = window.panels[index]
    panel.view.set_image(image)
    double_click(app, panel)
    viewer = panel.viewer
    assert isinstance(viewer, ImageViewer)
    assert viewer.isVisible()
    assert panel.title in viewer.windowTitle()
    assert viewer.view.pixmap.toImage() == panel.view.pixmap.toImage()
    double_click(app, panel)
    assert panel.viewer is viewer
    QTest.keyClick(viewer, Qt.Key.Key_Escape)
    app.processEvents()
    assert panel.viewer is None


def test_empty_panel_does_not_open_viewer(app, window):
    double_click(app, window.panels[0])
    assert window.panels[0].viewer is None


def test_buttons_wheel_actual_size_fit_and_display_only_zoom(app, window):
    assert window.load_path(SAMPLE_PATH)
    wait_for_idle(app, window)
    original = window.image.copy()
    panel = window.panels[0]
    double_click(app, panel)
    viewer = panel.viewer
    QTest.mouseClick(viewer.actual_button, Qt.MouseButton.LeftButton)
    assert viewer.view.transform().m11() == pytest.approx(1.0)
    assert viewer.zoom_label.text() == "100.0%"
    QTest.mouseClick(viewer.zoom_in_button, Qt.MouseButton.LeftButton)
    assert viewer.view.transform().m11() == pytest.approx(1.2)
    QTest.mouseClick(viewer.zoom_out_button, Qt.MouseButton.LeftButton)
    assert viewer.view.transform().m11() == pytest.approx(1.0)

    viewport = viewer.view.viewport()
    position = viewport.rect().center()
    wheel = QWheelEvent(QPointF(position), QPointF(viewport.mapToGlobal(position)),
                        QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(viewport, wheel)
    assert viewer.view.transform().m11() == pytest.approx(1.2)
    assert viewer.zoom_label.text() == "120.0%"
    viewer.view.zoom(1e9)
    assert viewer.view.transform().m11() == pytest.approx(24)
    viewer.view.zoom(1e-9)
    assert viewer.view.transform().m11() == pytest.approx(0.01)
    QTest.mouseClick(viewer.fit_button, Qt.MouseButton.LeftButton)
    assert viewer.view.fit_mode
    viewer.view.zoom(2)
    QTest.mouseDClick(viewer.view.viewport(), Qt.MouseButton.LeftButton)
    assert viewer.view.fit_mode
    np.testing.assert_array_equal(window.image, original)


def test_drag_pan_changes_viewport_without_changing_image(app, window):
    window.load_path(SAMPLE_PATH)
    wait_for_idle(app, window)
    panel = window.panels[0]
    double_click(app, panel)
    viewer = panel.viewer
    viewer.view.zoom(4)
    app.processEvents()
    before = viewer.view.horizontalScrollBar().value()
    viewport = viewer.view.viewport()
    center = viewport.rect().center()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=center)
    QTest.mouseMove(viewport, center + QPoint(80, 0))
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=center + QPoint(80, 0))
    app.processEvents()
    assert viewer.view.horizontalScrollBar().value() != before


def test_replacing_or_clearing_images_closes_stale_viewers(app, window):
    window.load_path(SAMPLE_PATH)
    wait_for_idle(app, window)
    panel = window.panels[0]
    double_click(app, panel)
    window.load_path(SAMPLE_PATH)
    wait_for_idle(app, window)
    assert panel.viewer is None
    result_panel = window.panels[2]
    result_panel.view.set_image(np.full((80, 100), 255, np.uint8))
    double_click(app, result_panel)
    assert result_panel.viewer is not None
    window.clear_result()
    assert result_panel.viewer is None


def test_closing_main_window_closes_image_viewer(app, window):
    window.load_path(SAMPLE_PATH)
    wait_for_idle(app, window)
    double_click(app, window.panels[0])
    window.close()
    app.processEvents()
    assert window.panels[0].viewer is None
    assert not window.isVisible()
