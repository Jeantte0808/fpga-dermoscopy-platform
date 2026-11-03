"""Delivery UI keeps provenance available without obscuring the four images."""
from dataclasses import replace
import json

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QScrollArea

from dermademo.formal_results import replay_pair
from dermademo.gui import MainWindow
from dermademo.records import load_record
from test_gui import app, window, wait_for_idle, SAMPLE_PATH


@pytest.mark.parametrize('mode', ['light', 'dark', 'high-contrast'])
@pytest.mark.parametrize('size', [(1060, 740), (1400, 920)])
def test_delivery_layout_and_keyboard_details_preserve_result(app, window, mode, size):
    app.fluent_theme.set_mode(mode)
    window.resize(*size)
    window.commit_formal_result(replay_pair())
    app.processEvents()
    result = window.result
    views = [p.view.pixmap.toImage() for p in window.panels]
    assert 'Demo' not in window.windowTitle()
    assert window.findChild(QLabel, 'badge') is None
    assert all(p.detail_label.isHidden() and p.title_label.toolTip() for p in window.panels)
    assert '离线' in window.notice.text() and window.notice.isVisible()
    assert not window.technical_details.isVisible()
    assert not window.generic_parameters.isVisible()
    window.technical_toggle.setFocus()
    QTest.keyClick(window.technical_toggle, Qt.Key.Key_Space)
    app.processEvents()
    assert window.technical_details.isVisible()
    assert json.loads(window.generic_parameters.toPlainText()) == dict(window.request.config.values)
    assert 'frame ' in window.technical_origin.text()
    assert window.result is result
    assert [p.view.pixmap.toImage() for p in window.panels] == views
    assert window.findChild(QScrollArea, 'sidebarScroll').horizontalScrollBar().maximum() == 0
    window.technical_toggle.click()
    assert not window.technical_details.isVisible()
    app.fluent_theme.set_mode('light')


def test_live_presentation_save_reopen_and_new_input(app, window, tmp_path):
    request, archived = replay_pair()
    # Presentation-only fixture: never reported as a physical FPGA test.
    live = replace(archived, metadata={**archived.metadata, 'display_mode': 'live_result',
                                      'live_run_performed': True, 'source_kind': 'live_FPGA_session'})
    window.commit_formal_result((request, live))
    app.processEvents()
    assert window.notice.isHidden()
    assert window.record_label.text() == '本次实时 FPGA 结果'
    assert 'frame ' not in window.record_label.text()
    assert 'SHA256' not in window.file_label.text()
    assert not window.technical_details.isVisible()
    window.technical_toggle.click()
    window.export_to(tmp_path)
    wait_for_idle(app, window)
    saved = load_record(window.last_export)
    assert np.array_equal(saved.result.mask, live.mask)
    assert saved.request.config == request.config
    assert saved.result.metadata['input_sha256'] == live.metadata['input_sha256']
    window.start_open_record(window.last_export)
    wait_for_idle(app, window)
    assert not window.technical_details.isVisible()
    assert '本次未连接 FPGA' in window.notice.text()
    assert 'frame ' in window.technical_origin.text()
    window.continue_button.click()
    assert window.parameters_widget.isVisible() and window.process_button.isEnabled()
    window.load_path(SAMPLE_PATH)
    wait_for_idle(app, window)
    assert not window.technical_details.isVisible()
    assert not window.technical_origin.text()
    assert window.panels[0].title == '01  原始影像'


@pytest.fixture
def delivery_window(app):
    view = MainWindow(delivery_mode=True)
    view.show()
    app.processEvents()
    yield view
    wait_for_idle(app, view)
    view.close()
    view.deleteLater()
    app.processEvents()


def test_official_start_and_import_use_model_preview_without_network(app, delivery_window, monkeypatch):
    import dermademo.formal_device_ui as ui
    from dermademo.resources import resource_path
    view = delivery_window
    assert view.notice.isHidden()
    assert not any(w.isVisible() for w in (view.sample_button, view.device_button,
               view.backend_combo, view.rgb_device_button, view.parameters_widget))
    assert view.process_button.isEnabled() and 'FPGA' in view.process_button.text()
    assert '合成' not in view.status_label.text()
    monkeypatch.setattr(ui, 'run_formal', lambda *_a, **_k: pytest.fail('preview connected to FPGA'))
    source = resource_path('full_image_root') / 'samples/01_ISIC_0000000/ISIC_0000000.jpg'
    monkeypatch.setattr(ui.QFileDialog, 'getOpenFileName', lambda *_a, **_k: (str(source), ''))
    view.import_button.click()
    from test_rgb_device_gui import wait, drain
    wait(app, view.formal_dialog)
    dialog = view.formal_dialog
    assert dialog.mode_combo.currentData() == 'live'
    assert dialog.source_combo.currentData() == 'image'
    assert dialog.prepared_image is not None and dialog.preview.has_image
    assert view.result is None
    dialog.close()
    drain(app)


def test_maintenance_tools_are_explicit_and_can_return_to_workbench(app, delivery_window):
    from test_rgb_device_gui import drain
    view = delivery_window
    view.open_diagnostics()
    dialog = view.diagnostics_dialog
    assert dialog.maintenance_button.isVisible()
    actions = dialog.maintenance_button.menu().actions()
    assert len(actions) == 4
    actions[2].trigger()
    drain(app)
    assert view.diagnostics_dialog is None
    assert view.reference_mode and view.parameters_widget.isVisible()
    assert '参考阈值算法' in view.notice.text()
    assert view.return_to_fpga_button.isVisible()
    view.return_to_fpga_button.click()
    assert not view.reference_mode and not view.parameters_widget.isVisible()
    view.process_button.click()
    drain(app)
    assert view.formal_dialog is not None and view.formal_dialog.mode_combo.currentData() == 'live'
    view.formal_dialog.close()
    drain(app)
