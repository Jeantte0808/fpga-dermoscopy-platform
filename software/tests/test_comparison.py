import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
import pytest

from test_gui import app, window, process_sample, wait_for_idle


def center(view):
    return view.mapToScene(view.viewport().rect().center())


def test_overlay_controls_change_scene_only_keep_big_view_zoom_and_export(app, window, tmp_path):
    process_sample(app, window)
    result, request = window.result, window.request
    pixels = result.overlay_bgr.copy()
    panel = window.panels[3]
    panel.open_viewer()
    viewer = panel.viewer
    viewer.view.actual_size()
    viewer.view.zoom(3)
    zoom = viewer.view.transform().m11()
    position = center(viewer.view)
    window.opacity_slider.setValue(35)
    window.overlay_background.setCurrentIndex(1)
    assert window.panels[3].viewer is viewer and viewer.view.transform().m11() == pytest.approx(zoom)
    assert center(viewer.view) == position
    for view in (panel.view, viewer.view):
        assert view._overlay_item.opacity() == pytest.approx(0.35)
        assert view._overlay_base.pixmap().toImage() == window.overlay_presentation.enhanced.toImage()
    window.overlay_checkbox.setChecked(False)
    assert not panel.view._overlay_item.isVisible() and not viewer.view._overlay_item.isVisible()
    window.overlay_checkbox.setChecked(True)
    window.opacity_slider.setValue(0)
    assert panel.view._overlay_item.opacity() == 0
    assert window.result is result and window.request is request
    np.testing.assert_array_equal(result.overlay_bgr, pixels)
    window.export_to(tmp_path)
    wait_for_idle(app, window)
    from dermademo.records import load_record
    np.testing.assert_array_equal(load_record(window.last_export).result.overlay_bgr, pixels)


def test_linked_zoom_pan_unlink_and_fit(app, window):
    process_sample(app, window)
    window.link_checkbox.setChecked(True)
    source = window.panels[0].view
    source.actual_size()
    source.zoom(3)
    assert all(p.view.transform().m11() == pytest.approx(3) for p in window.panels)
    viewport = source.viewport()
    pos = viewport.rect().center()
    before = center(window.panels[1].view)
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=pos)
    QTest.mouseMove(viewport, pos + QPoint(60, 30))
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=pos + QPoint(60, 30))
    app.processEvents()
    assert center(window.panels[1].view) != before
    for panel in window.panels[1:]:
        assert abs(center(panel.view).x() - center(source).x()) < 2
        assert abs(center(panel.view).y() - center(source).y()) < 2
    window.link_checkbox.setChecked(False)
    original = window.panels[1].view.transform().m11()
    source.zoom(2)
    assert window.panels[1].view.transform().m11() == original
    window.fit_all_button.click()
    assert all(p.view.fit_mode for p in window.panels)


def test_linking_big_viewer_navigation_and_replacement_has_no_stale_callbacks(app, window):
    process_sample(app, window)
    window.link_checkbox.setChecked(True)
    window.panels[3].open_viewer()
    viewer = window.panels[3].viewer
    viewer.view.actual_size()
    viewer.view.zoom(2)
    assert all(p.view.transform().m11() == pytest.approx(2) for p in window.panels)
    viewer.fit_button.click()
    assert all(p.view.fit_mode for p in window.panels)
    viewer.close()
    app.processEvents()
    window.panels[0].view.zoom(2)
    window.start_import(window.source_path)
    wait_for_idle(app, window)
    assert not any(p.viewer is not None for p in window.panels)
    assert window.panels[0].view.fit_mode and window.overlay_presentation is None
    assert not window.opacity_slider.isEnabled()
