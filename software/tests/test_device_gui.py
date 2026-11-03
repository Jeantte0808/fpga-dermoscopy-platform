import json
import socket
from time import monotonic

import numpy as np
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
import pytest

from dermademo.device.lab import LabFaults, LabMode, run_loopback
from dermademo.gui import MainWindow, SAMPLE_PATH, create_application


@pytest.fixture(scope="session")
def app():
    return create_application()


def drain(app):
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def wait(app, target, timeout=8):
    deadline = monotonic() + timeout
    while target.busy and monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert not target.busy
    drain(app)


@pytest.fixture
def window(app):
    window = MainWindow()
    window.show()
    drain(app)
    yield window
    if window.device_dialog is not None:
        window.device_dialog.cancel_test()
        wait(app, window.device_dialog)
        window.device_dialog.close()
        drain(app)
    wait(app, window)
    window.close()
    window.deleteLater()
    drain(app)


def open_lab(app, window):
    QTest.mouseClick(window.device_button, Qt.MouseButton.LeftButton)
    drain(app)
    dialog = window.device_dialog
    assert dialog.isVisible() and dialog.isModal()
    return dialog


def test_startup_and_open_lab_have_no_network_side_effects(app, window, monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *_a, **_k: pytest.fail("Opening a dialog created a socket"))
    dialog = open_lab(app, window)
    assert dialog.result is None and not dialog.busy
    assert not dialog.save_button.isEnabled()
    assert not dialog.source_combo.model().item(1).isEnabled()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    drain(app)
    assert window.device_dialog is None


def test_enter_ctrl_s_and_pending_options_preserve_main_result(app, window, tmp_path, monkeypatch):
    window.load_path(SAMPLE_PATH, is_synthetic=True)
    wait(app, window)
    window.start_processing()
    wait(app, window)
    main_result, request = window.result, window.request
    window.clip_spin.setValue(3)
    assert window.parameters_pending
    dialog = open_lab(app, window)
    dialog.activateWindow()
    dialog.run_button.setFocus()
    QTest.keyClick(dialog, Qt.Key.Key_Return)
    wait(app, dialog)
    result = dialog.result
    assert result is not None and result.request.mode == LabMode.RGB_BYPASS
    assert "非 FPGA" in dialog.detail.toPlainText()
    assert window.result is main_result and window.request is request and window.parameters_pending
    dialog.mode_combo.setCurrentIndex(2)
    assert dialog.result is result  # Options do not auto-run or clear the last preview.
    monkeypatch.setattr("dermademo.device_ui.QFileDialog.getExistingDirectory", lambda *_a, **_k: str(tmp_path))
    QTest.keyClick(dialog, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    wait(app, dialog)
    document = json.loads((dialog.last_export / "diagnostic.json").read_text(encoding="utf-8"))
    assert document["mode"] == "RGB_BYPASS"  # Exports completed snapshot, not pending mode.
    assert window.last_export is None and window.result is main_result
    QTest.keyClick(dialog, Qt.Key.Key_Enter)
    wait(app, dialog)
    assert dialog.result.request.mode == LabMode.MASK_FIXTURE and not dialog.output_view.smooth


def test_workbench_snapshot_mapping_and_no_enhancement(app, window):
    window.load_path(SAMPLE_PATH, is_synthetic=True)
    wait(app, window)
    original = window.image.copy()
    dialog = open_lab(app, window)
    dialog.source_combo.setCurrentIndex(1)
    dialog.geometry_combo.setCurrentIndex(1)
    dialog.mode_combo.setCurrentIndex(2)
    dialog.start_test()
    assert not dialog.run_button.isEnabled() and not dialog.mode_combo.isEnabled()
    assert dialog.cancel_button.isEnabled()
    wait(app, dialog)
    prepared = dialog.result.request.prepared
    assert prepared.geometry == "letterbox"
    assert (prepared.original_height, prepared.original_width) == original.shape[:2]
    assert np.array_equal(window.image, original) and window.result is None


def test_cancel_and_failed_export_preserve_previous_diagnostic(app, window, monkeypatch, tmp_path):
    dialog = open_lab(app, window)
    dialog.start_test()
    wait(app, dialog)
    previous = dialog.result
    monkeypatch.setattr("dermademo.device_ui.run_loopback", lambda request, cancel: run_loopback(
        request, cancel, faults=LabFaults(delay_seconds=2)))
    dialog.start_test()
    QTest.qWait(50)
    dialog.cancel_test()
    wait(app, dialog)
    assert dialog.result is previous and "已取消" in dialog.status.text()
    assert dialog.save_button.isEnabled()
    dialog.export_to(tmp_path / "missing")
    wait(app, dialog)
    assert "失败" in dialog.status.text() and dialog.result is previous
    assert dialog.last_export is None


def test_parent_close_cancels_worker_without_destroying_running_thread(app, window, monkeypatch):
    dialog = open_lab(app, window)
    monkeypatch.setattr("dermademo.device_ui.run_loopback", lambda request, cancel: run_loopback(
        request, cancel, faults=LabFaults(delay_seconds=2)))
    dialog.start_test()
    QTest.qWait(20)
    assert not window.close()
    deadline = monotonic() + 3
    while window.device_dialog is not None and monotonic() < deadline:
        QTest.qWait(10)
        drain(app)
    assert window.device_dialog is None and window.isVisible()
    assert window.close()


@pytest.mark.parametrize("theme", ["light", "dark", "high-contrast"])
def test_lab_theme_keyboard_focus_and_minimum_layout(app, window, theme):
    app.fluent_theme.set_mode(theme)
    dialog = open_lab(app, window)
    dialog.resize(680, 560)
    drain(app)
    assert not dialog.styleSheet()
    for widget in (dialog.source_combo, dialog.mode_combo, dialog.timeout_spin, dialog.run_button, dialog.close_button):
        assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().topLeft()))
        assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().bottomRight()))
        assert widget.width() >= widget.minimumSizeHint().width()
    dialog.run_button.setFocus()
    QTest.keyClick(dialog, Qt.Key.Key_Tab)
    drain(app)
    assert dialog.save_button.hasFocus() is False  # Disabled export is skipped.
    app.fluent_theme.set_mode("system")
