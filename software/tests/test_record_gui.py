import json
import time

import numpy as np
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog
import pytest

import dermademo.gui as gui
from dermademo.records import load_record
from test_gui import app, window, process_sample, wait_for_idle
from test_records import saved, rewrite


def test_reopen_read_only_enter_and_ctrl_s_preserve_original_record(app, window, saved, tmp_path, monkeypatch):
    directory, request, result = saved
    window.clip_spin.setValue(4)
    monkeypatch.setattr(window.backend, "process", lambda _request: pytest.fail("Opening a record invoked the algorithm"))
    assert window.start_open_record(directory)
    wait_for_idle(app, window)
    assert window.record_view_only and window.result.timings_ms == result.timings_ms
    assert window.selected_config() == request.config
    assert not window.process_button.isEnabled() and not window.clip_spin.isEnabled()
    disabled = window.process_button.grab().toImage().pixelColor(5, window.process_button.height() // 2)
    assert not window.save_preset("只读不可保存预设")
    window.panels[0].view.setFocus()
    window.activateWindow()
    app.processEvents()
    QTest.keyClick(window.panels[0].view, Qt.Key.Key_Return)
    assert not window.busy
    window.open_timing()
    assert "记录" in window.timing_dialog.windowTitle()
    assert window.timing_dialog.result is window.result
    window.opacity_slider.setValue(30)
    window.overlay_background.setCurrentIndex(1)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(tmp_path))
    window.activateWindow()
    window.panels[0].view.setFocus()
    app.processEvents()
    QTest.keyClick(window.panels[0].view, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    wait_for_idle(app, window)
    copy = load_record(window.last_export)
    assert copy.request.config == request.config
    assert copy.result.timings_ms == result.timings_ms
    np.testing.assert_array_equal(copy.result.overlay_bgr, result.overlay_bgr)
    assert copy.document["restored_from"]["record_id"] == window.opened_record.document["record_id"]
    window.continue_button.click()
    app.processEvents()
    enabled = window.process_button.grab().toImage().pixelColor(5, window.process_button.height() // 2)
    assert disabled != enabled


def test_failed_record_and_display_preparation_keep_everything(app, window, saved, tmp_path, monkeypatch):
    process_sample(app, window)
    window.clip_spin.setValue(4)
    window.panels[3].open_viewer()
    window.open_timing()
    previous = (window.image, window.request, window.result, window.panels[3].viewer, window.timing_dialog)
    window.start_open_record(tmp_path / "missing")
    wait_for_idle(app, window)
    assert previous == (window.image, window.request, window.result, window.panels[3].viewer, window.timing_dialog)
    monkeypatch.setattr(gui.ImageView, "prepare_pixmap", lambda _image: (_ for _ in ()).throw(ValueError("display")))
    window.start_open_record(saved[0])
    wait_for_idle(app, window)
    assert previous == (window.image, window.request, window.result, window.panels[3].viewer, window.timing_dialog)
    assert window.parameters_pending and window.clip_spin.value() == 4


def test_record_cancel_and_slow_load_lock_commands(app, window, saved, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: "")
    window.choose_record()
    assert not window.busy and window.image is None
    original = gui.load_record
    def slow(path):
        time.sleep(0.2)
        return original(path)
    monkeypatch.setattr(gui, "load_record", slow)
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    window.start_open_record(saved[0])
    assert not window.open_record_button.isEnabled()
    assert not window.start_open_record(saved[0])
    window._handle_success("record", "stale", None)
    window._handle_failure("record", "stale", "old")
    window._thread_finished()
    assert window.busy
    assert not window.close()
    wait_for_idle(app, window)
    timer.stop()
    assert len(ticks) > 2 and window.record_view_only


def test_continue_is_explicit_and_only_enter_generates_a_new_result(app, window, saved):
    window.start_open_record(saved[0])
    wait_for_idle(app, window)
    old = window.result
    window.continue_button.click()
    assert not window.record_view_only and window.process_button.isEnabled()
    assert window.result is old
    window.clip_spin.setValue(3)
    assert window.parameters_pending and window.result is old
    window.activateWindow()
    window.panels[0].view.setFocus()
    app.processEvents()
    QTest.keyClick(window.panels[0].view, Qt.Key.Key_Return)
    wait_for_idle(app, window)
    assert window.result is not old and window.request.task_id != old.task_id
    assert window.opened_record is None and not window.record_label.isVisible()


def test_unknown_backend_can_be_viewed_without_adopting_its_parameters(app, window, saved):
    window.clip_spin.setValue(4)
    rewrite(saved[0], lambda d: d["backend"].update(key="other", name="历史模型", algorithm_version="test/1"))
    window.start_open_record(saved[0])
    wait_for_idle(app, window)
    assert "历史模型" in window.record_label.text()
    assert "历史模型" in window.version_label.text()
    window.continue_button.click()
    assert window.clip_spin.value() == 4
    assert not window.restore_button.isEnabled()


def test_import_after_read_only_record_restores_previous_editing_config(app, window, saved):
    window.clip_spin.setValue(4)
    window.start_open_record(saved[0])
    wait_for_idle(app, window)
    window.start_import(gui.SAMPLE_PATH)
    wait_for_idle(app, window)
    assert window.clip_spin.value() == 4 and not window.record_view_only
    assert window.result is None and window.process_button.isEnabled()


def test_record_view_does_not_overwrite_pending_preferences_on_close(app, window, saved):
    window.clip_spin.setValue(4)
    window.start_open_record(saved[0])
    wait_for_idle(app, window)
    window.close()
    assert window.preferences.config.clahe_clip == 4
