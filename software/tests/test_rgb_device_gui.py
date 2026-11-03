import json
import socket
from time import monotonic

import numpy as np
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
import pytest

from dermademo.device.rgb_protocol import gaussian_rgb
from dermademo.gui import MainWindow, SAMPLE_PATH, create_application
from rgb_udp_peer import RGBPeer


@pytest.fixture(scope="session")
def app():
    return create_application()


def drain(app):
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def wait(app, target, timeout=35):
    # configure_local permits a 30s transaction. A functional GUI wait must
    # outlast that bound plus delivery of the worker's completion signal.
    # Do not turn host scheduling speed into an implicit 15s wire requirement.
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
    if window.rgb_device_dialog is not None:
        window.rgb_device_dialog.cancel_transfer()
        wait(app, window.rgb_device_dialog)
        window.rgb_device_dialog.close()
        drain(app)
    wait(app, window)
    window.close()
    window.deleteLater()
    drain(app)


def open_rgb(app, window):
    QTest.mouseClick(window.rgb_device_button, Qt.MouseButton.LeftButton)
    drain(app)
    dialog = window.rgb_device_dialog
    assert dialog.isVisible() and dialog.isModal()
    return dialog


def configure_local(dialog, peer):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    dialog.bind_edit.setText("127.0.0.1")
    dialog.board_edit.setText("127.0.0.1")
    dialog.local_port.setValue(port)
    dialog.board_port.setValue(peer.port)
    # GUI tests verify behavior, not scheduling speed of two Python threads plus
    # Qt painting. Keep the delivered 1s retry timeout and the app's 30s cap.
    dialog.timeout_spin.setValue(1)
    dialog.deadline_spin.setValue(30)


def test_open_and_invalid_parameters_do_not_send(app, window, monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *_a, **_k: pytest.fail("Opened socket before valid run"))
    dialog = open_rgb(app, window)
    assert dialog.board_edit.text() == "192.168.0.2" and dialog.board_port.value() == 5000
    assert dialog.bind_edit.text() == "192.168.0.3" and dialog.local_port.value() == 6102
    assert not dialog.save_button.isEnabled() and dialog.outcome is None
    assert dialog.mode_combo.count() == 2  # No fictional Mask mode.
    dialog.board_edit.setText("not-an-ip")
    dialog.start_transfer()
    assert "参数无效" in dialog.status.text() and not dialog.busy
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    drain(app)
    assert window.rgb_device_dialog is None


def test_enter_export_pending_options_and_main_result_preserved(app, window, tmp_path, monkeypatch):
    window.load_path(SAMPLE_PATH, is_synthetic=True)
    wait(app, window)
    window.start_processing()
    wait(app, window)
    original_result, original_request = window.result, window.request
    assert "参考阈值算法" in window.notice.text()
    window.clip_spin.setValue(3)
    assert window.parameters_pending
    dialog = open_rgb(app, window)
    with RGBPeer() as peer:
        configure_local(dialog, peer)
        dialog.activateWindow()
        dialog.run_button.setFocus()
        QTest.keyClick(dialog, Qt.Key.Key_Return)
        assert dialog.busy and not dialog.board_edit.isEnabled() and dialog.cancel_button.isEnabled()
        wait(app, dialog)
    outcome = dialog.outcome
    assert outcome.complete and dialog.displayed_outcome is outcome
    assert "protocol_simulator" in dialog.detail.toPlainText()
    assert window.result is original_result and window.request is original_request and window.parameters_pending
    dialog.mode_combo.setCurrentIndex(1)
    dialog.source_combo.setCurrentIndex(1)
    assert dialog.outcome is outcome and not dialog.busy
    monkeypatch.setattr("dermademo.rgb_device_ui.QFileDialog.getExistingDirectory", lambda *_a, **_k: str(tmp_path))
    QTest.keyClick(dialog, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    wait(app, dialog)
    report = json.loads((dialog.last_export / "report.json").read_text(encoding="utf-8"))
    assert report["mode"] == "bypass" and report["input_source"] == "handoff-raw"
    assert report["model"] is None and not report["mask_available"]
    assert window.result is original_result and window.last_export is None


def test_snapshot_gaussian_and_never_populates_main_mask(app, window):
    window.load_path(SAMPLE_PATH, is_synthetic=True)
    wait(app, window)
    original = window.image.copy()
    dialog = open_rgb(app, window)
    dialog.source_combo.setCurrentIndex(1)
    dialog.geometry_combo.setCurrentIndex(1)
    dialog.mode_combo.setCurrentIndex(1)
    with RGBPeer(gaussian=gaussian_rgb) as peer:
        configure_local(dialog, peer)
        dialog.start_transfer()
        wait(app, dialog)
    assert dialog.outcome.complete, json.dumps(dialog.outcome.report, ensure_ascii=True)
    assert dialog.outcome.request.prepared.geometry == "letterbox"
    assert dialog.outcome.request.source == "workbench-snapshot"
    assert window.result is None and np.array_equal(window.image, original)


def test_cancel_and_failure_diagnostics_keep_success_images(app, window, tmp_path):
    dialog = open_rgb(app, window)
    with RGBPeer() as peer:
        configure_local(dialog, peer)
        dialog.start_transfer()
        wait(app, dialog)
    assert dialog.outcome is not None, dialog.status.text()
    assert dialog.outcome.complete, json.dumps(dialog.outcome.report, ensure_ascii=True)
    previous = dialog.displayed_outcome
    assert previous is not None, dialog.status.text()
    assert previous.complete, json.dumps(previous.report, ensure_ascii=True)
    with RGBPeer("delay") as peer:
        configure_local(dialog, peer)
        dialog.start_transfer()
        QTest.qWait(30)
        dialog.cancel_transfer()
        wait(app, dialog)
    assert not dialog.outcome.complete and dialog.outcome.report["status"] == "cancelled"
    assert dialog.displayed_outcome is previous and dialog.save_button.isEnabled()
    dialog.export_to(tmp_path)
    wait(app, dialog)
    report = json.loads((dialog.last_export / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "cancelled" and not report["complete"]
    assert not (dialog.last_export / "returned.png").exists()
    with RGBPeer("bad_out_crc") as peer:
        configure_local(dialog, peer)
        dialog.start_transfer()
        wait(app, dialog)
    assert dialog.outcome.report["status"] == "protocol_error" and dialog.displayed_outcome is previous


def test_parent_close_and_escape_clean_up_before_destruction(app, window):
    dialog = open_rgb(app, window)
    with RGBPeer("delay") as peer:
        configure_local(dialog, peer)
        dialog.start_transfer()
        QTest.qWait(20)
        assert not window.close()
        deadline = monotonic() + 3
        while window.rgb_device_dialog is not None and monotonic() < deadline:
            QTest.qWait(10)
            drain(app)
    assert window.rgb_device_dialog is None and window.isVisible()
    assert window.close()


@pytest.mark.parametrize("theme", ["light", "dark", "high-contrast"])
def test_footer_is_accessible_at_minimum_size_and_all_themes(app, window, theme):
    app.fluent_theme.set_mode(theme)
    dialog = open_rgb(app, window)
    dialog.resize(760, 600)
    drain(app)
    for button in (dialog.run_button, dialog.save_button, dialog.cancel_button, dialog.close_button):
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().topLeft()))
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        assert button.width() >= button.minimumSizeHint().width()
    assert not dialog.styleSheet() and dialog.detail.isReadOnly()
    dialog.run_button.setFocus()
    QTest.keyClick(dialog, Qt.Key.Key_Tab)
    drain(app)
    assert dialog.close_button.hasFocus()
    app.fluent_theme.set_mode("system")
