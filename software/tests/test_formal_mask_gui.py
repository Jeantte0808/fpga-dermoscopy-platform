from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
import pytest

from dermademo.formal_results import replay_pair
from dermademo.records import load_record
from test_gui import app, window, wait_for_idle
from test_rgb_device_gui import drain, wait


def test_offline_enter_four_views_pending_edits_ctrl_s_and_reopen(app, window, tmp_path, monkeypatch):
    window.open_formal_device()
    dialog = window.formal_dialog
    drain(app)
    assert dialog.mode_combo.currentData() == 'replay'
    assert not dialog.source_combo.isEnabled()
    dialog.activateWindow()
    dialog.run_button.setFocus()
    QTest.keyClick(dialog, Qt.Key.Key_Return)
    wait(app, dialog)
    assert window.result.capabilities.key == 'fpga_formal_v5'
    assert all(p.view.has_image for p in window.panels)
    assert '离线' in window.notice.text() and '本次未连接 FPGA' in window.notice.text()
    assert '619.29 ms' in window.fpga_time_label.text()
    assert window.result.measurements.area_px2 == 31174
    result = window.result
    dialog.clip_spin.setValue(4)
    dialog.mode_combo.setCurrentIndex(1)
    assert window.result is result and dialog.last_pair[1] is result
    assert dialog.source_combo.isEnabled()
    monkeypatch.setattr('dermademo.formal_device_ui.QFileDialog.getExistingDirectory', lambda *_a: str(tmp_path))
    QTest.keyClick(dialog, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    wait_for_idle(app, window)
    drain(app)
    assert window.formal_dialog is None
    saved = window.last_export
    assert saved and np.array_equal(load_record(saved).result.mask, result.mask)
    window.start_open_record(saved)
    wait_for_idle(app, window)
    assert window.result.metadata['display_mode'] == 'offline_replay'
    window.open_timing()
    assert window.timing_dialog.values['fpga_graph'].text() == '619.29 ms'
    assert window.timing_dialog.values['image_transport_e2e'].text() == '783.53 ms'
    window.continue_from_record()
    assert not window.record_view_only and window.process_button.isEnabled()


def test_failed_formal_replay_preserves_success_and_safe_dialog_close(app, window, tmp_path):
    window.commit_formal_result(replay_pair())
    old_result, old_request = window.result, window.request
    window.open_formal_device()
    dialog = window.formal_dialog
    dialog.record_directory = tmp_path/'missing'
    dialog.start()
    wait(app, dialog)
    assert window.result is old_result and window.request is old_request
    assert '失败' in dialog.status.text()
    dialog.close()
    drain(app)
    assert window.formal_dialog is None


@pytest.mark.parametrize('finish_confirmed', [False, True])
def test_local_save_failure_retains_four_views_and_explains_board_state(app, window, tmp_path, monkeypatch, finish_confirmed):
    from dermademo.device.formal_session import FormalOutcome
    import dermademo.formal_device_ui as ui
    window.commit_formal_result(replay_pair())
    old_request, old_result = window.request, window.result
    report = {'complete': False, 'error': 'LocalPersistenceError: disk failure',
              'hardware_finish_confirmed': finish_confirmed, 'manual_recovery_required': not finish_confirmed,
              'local_artifacts_complete': False, 'failure_domain': 'local_storage'}
    outcome = FormalOutcome(b'', b'', report, None, tmp_path)
    monkeypatch.setattr(ui, 'run_formal', lambda *_a, **_k: outcome)
    window.open_formal_device()
    dialog = window.formal_dialog
    dialog.mode_combo.setCurrentIndex(1)
    dialog.start()
    wait(app, dialog)
    assert window.request is old_request and window.result is old_result
    assert all(p.view.has_image for p in window.panels)
    assert ('板卡本帧已释放' if finish_confirmed else '板卡释放状态未确认') in dialog.status.text()
    dialog.close()
    drain(app)


def test_live_gui_worker_full_wire_success_then_failure_retains_last_mask(app, window, tmp_path, monkeypatch):
    import time
    from formal_mask_peer import FormalPeer, local_config
    import dermademo.formal_device_ui as ui
    def wait_wire():
        deadline = time.monotonic() + 20
        while dialog.busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)  # Release the GIL to both Python wire threads.
        app.processEvents()
        assert not dialog.busy, dialog.status.text()
    with FormalPeer() as peer:
        monkeypatch.setattr(ui, 'FormalConfig', lambda **_kwargs: local_config(peer, reuse=False))
        monkeypatch.setattr(ui, 'default_output', lambda: tmp_path/'captures')
        window.open_formal_device()
        dialog = window.formal_dialog
        dialog.mode_combo.setCurrentIndex(1)
        dialog.start()
        assert dialog.busy and not dialog.mode_combo.isEnabled()
        wait_wire()
        assert dialog.outcome.report['complete'], dialog.status.text()
        old_result, old_request = window.result, window.request
        assert old_result.metadata['source_kind'] == 'protocol_simulator'
        assert old_result.metadata['display_mode'] == 'live_result'
        assert '本机协议模拟' in window.notice.text()
        peer.bad_generation = True
        dialog.start()
        wait_wire()
        assert dialog.outcome.report['complete'] is False
        assert window.result is old_result and window.request is old_request
        assert '失败' in dialog.status.text()
        dialog.close()
        drain(app)


@pytest.mark.parametrize('mode', ['light', 'dark', 'high-contrast'])
def test_formal_theme_and_display_memory_owned(app, window, mode):
    app.fluent_theme.set_mode(mode)
    window.commit_formal_result(replay_pair())
    drain(app)
    assert window.panels[2].view.pixmap.size().width() == 256
    assert not window.result.mask.flags.writeable
    assert not window.generic_parameters.isVisible()
    window.technical_toggle.click()
    assert window.generic_parameters.isVisible()
    assert window.save_button.isEnabled()
    window.open_formal_device()
    drain(app)
    assert window.formal_dialog.run_button.isEnabled()
    window.formal_dialog.close()
    drain(app)
    app.fluent_theme.set_mode('light')
