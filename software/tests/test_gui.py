from dataclasses import replace
import json
import time

import numpy as np
from PIL import Image
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFontDatabase, QFontMetrics
from PySide6.QtWidgets import QFileDialog, QScrollArea
from PySide6.QtTest import QTest
import pytest

from dermademo.backends import DemoCPUBackend
from dermademo.gui import MainWindow, SAMPLE_PATH, create_application


@pytest.fixture(scope="session")
def app():
    return create_application()


@pytest.fixture
def window(app):
    window = MainWindow()
    window.show()
    app.processEvents()
    yield window
    wait_for_idle(app, window)
    window.close()
    window.deleteLater()
    app.processEvents()


def wait_for_idle(app, window, timeout=8):
    deadline = time.monotonic() + timeout
    while window.busy and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    app.processEvents()
    assert not window.busy, "background task did not finish"


def process_sample(app, window):
    assert window.load_path(SAMPLE_PATH, is_synthetic=True)
    wait_for_idle(app, window)
    QTest.mouseClick(window.process_button, Qt.MouseButton.LeftButton)
    wait_for_idle(app, window)
    assert window.result is not None


def test_real_click_process_export_and_clear_on_new_input(app, window, tmp_path):
    assert not window.process_button.isEnabled()
    process_sample(app, window)
    assert all(panel.view.has_image for panel in window.panels)
    assert window.save_button.isEnabled()
    assert "合成样例" in window.file_label.text()
    assert window.area_label.text() != "—"
    assert window.result.timings_ms["end_to_end"] >= window.result.timings_ms["backend_pipeline"]
    assert window.result.metadata["timing_scope"] == "application_submit_to_validated_result_excluding_display"
    window.export_to(tmp_path)
    wait_for_idle(app, window)
    assert "保存成功" in window.status_label.text()
    assert (window.last_export / "record.json").exists()
    assert window.load_path(SAMPLE_PATH, is_synthetic=True)
    wait_for_idle(app, window)
    assert window.result is None
    assert window.area_label.text() == "—"
    assert not window.save_button.isEnabled()
    assert not any(panel.view.has_image for panel in window.panels[1:])


def test_parameter_changes_keep_results_until_process_is_clicked(app, window):
    process_sample(app, window)
    old_result, old_request = window.result, window.request
    images = [panel.view.pixmap.toImage() for panel in window.panels]
    metrics = [label.text() for label in (window.area_label, window.diameter_label, window.time_label)]
    window.panels[1].open_viewer()
    viewer = window.panels[1].viewer
    window.clip_spin.setValue(3.0)
    assert window.result is old_result
    assert window.request is old_request
    assert window.save_button.isEnabled()
    assert "尚未应用" in window.status_label.text()
    assert not window.threshold_spin.isEnabled()
    assert not window.threshold_spin.isVisible()
    window.threshold_mode.setCurrentIndex(1)
    assert window.threshold_spin.isEnabled()
    assert window.threshold_spin.isVisible()
    window.threshold_spin.setValue(100)
    window.region_spin.setValue(128)
    assert window.parameters_pending
    assert window.result is old_result
    assert window.request is old_request
    assert [panel.view.pixmap.toImage() for panel in window.panels] == images
    assert [label.text() for label in (window.area_label, window.diameter_label, window.time_label)] == metrics
    assert window.panels[1].viewer is viewer
    QTest.mouseClick(window.process_button, Qt.MouseButton.LeftButton)
    assert window.request.config.threshold == 100
    assert window.request.config.clahe_clip == 3.0
    assert window.request.config.min_region_pixels == 128
    assert window.request.task_id != old_request.task_id
    wait_for_idle(app, window)
    assert window.result is not None and window.result is not old_result
    assert window.result.metadata["threshold_mode"] == "manual"
    assert window.result.metadata["effective_threshold"] == 100
    assert not window.parameters_pending


def test_switching_threshold_mode_back_matches_displayed_result(app, window):
    process_sample(app, window)
    old_result = window.result
    window.threshold_mode.setCurrentIndex(1)
    assert window.parameters_pending
    window.threshold_mode.setCurrentIndex(0)
    assert not window.parameters_pending
    assert window.result is old_result
    assert "参数与当前结果一致" in window.status_label.text()
    assert window.save_button.isEnabled()


def test_save_pending_changes_exports_original_result_parameters(app, window, tmp_path):
    process_sample(app, window)
    old_result = window.result
    window.threshold_mode.setCurrentIndex(1)
    window.threshold_spin.setValue(90)
    window.clip_spin.setValue(4.0)
    window.export_to(tmp_path)
    wait_for_idle(app, window)
    record = json.loads((window.last_export / "record.json").read_text(encoding="utf-8"))
    assert record["parameters"]["threshold"] is None
    assert record["parameters"]["clahe_clip"] == 2.0
    assert record["processing"]["threshold_mode"] == "otsu"
    assert record["measurements"]["area_px2"] == old_result.measurements.area_px2
    assert window.result is old_result
    assert window.parameters_pending
    assert "保存成功" in window.status_label.text()
    assert "新参数尚未应用" in window.status_label.text()


def test_corrupt_input_keeps_previous_results(app, window, tmp_path):
    process_sample(app, window)
    old_image, old_request, old_result = window.image, window.request, window.result
    images = [panel.view.pixmap.toImage() for panel in window.panels]
    window.panels[1].open_viewer()
    viewer = window.panels[1].viewer
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"broken")
    assert window.start_import(broken)
    wait_for_idle(app, window)
    assert window.image is old_image and window.request is old_request
    assert window.result is old_result
    assert [panel.view.pixmap.toImage() for panel in window.panels] == images
    assert window.panels[1].viewer is viewer
    assert window.save_button.isEnabled()
    assert "读取失败" in window.status_label.text()


def test_background_import_locks_commands_and_keeps_event_loop(app, window, monkeypatch):
    import dermademo.gui as gui
    process_sample(app, window)
    old_result = window.result
    original = gui.load_candidate

    def slow_load(*args):
        time.sleep(0.2)
        return original(*args)

    monkeypatch.setattr(gui, "load_candidate", slow_load)
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(10)
    assert window.start_import(SAMPLE_PATH)
    assert window.result is old_result
    assert not window.start_import(SAMPLE_PATH)
    assert not window.process_button.isEnabled()
    assert not window.save_button.isEnabled()
    window.start_processing()
    window.export_to(SAMPLE_PATH.parent)
    assert window.result is old_result
    window._handle_success("load", "stale", None)
    window._handle_failure("load", "stale", "outdated")
    window._thread_finished()  # A callback without the current thread sender is stale.
    assert window.result is old_result and window.busy
    assert not window.close()
    wait_for_idle(app, window)
    timer.stop()
    assert len(ticks) >= 5
    assert window.result is None and window.process_button.isEnabled()
    assert not window.image.flags.writeable


@pytest.mark.parametrize("invalid_candidate", [False, True])
def test_import_prepare_failure_preserves_state(app, window, monkeypatch, invalid_candidate):
    import dermademo.gui as gui
    process_sample(app, window)
    old_image, old_result = window.image, window.result
    if invalid_candidate:
        monkeypatch.setattr(gui, "load_candidate", lambda *args: gui.LoadedImage(
            SAMPLE_PATH, np.zeros((2, 2), np.uint8), False))
    else:
        def fail(*args):
            raise ValueError("模拟显示准备失败")
        monkeypatch.setattr(gui.ImageView, "prepare_pixmap", fail)
    window.start_import(SAMPLE_PATH)
    wait_for_idle(app, window)
    assert window.image is old_image and window.result is old_result
    assert window.save_button.isEnabled()
    assert "读取失败" in window.status_label.text()


def test_cancel_import_and_empty_failure(app, window, tmp_path, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    window.choose_image()
    assert not window.busy and window.image is None
    window.start_import(tmp_path / "missing.png")
    wait_for_idle(app, window)
    assert window.image is None and not window.process_button.isEnabled()


def test_restore_parameters_keeps_result_and_clears_pending(app, window):
    from dermademo.contracts import ProcessingConfig
    assert not window.restore_button.isEnabled()
    process_sample(app, window)
    old_result = window.result
    images = [panel.view.pixmap.toImage() for panel in window.panels]
    window.threshold_mode.setCurrentIndex(1)
    window.threshold_spin.setValue(90)
    window.clip_spin.setValue(4)
    window.defaults_button.click()
    assert window.selected_config() == ProcessingConfig()
    assert window.threshold_spin.value() == 110
    assert not window.parameters_pending
    window.clip_spin.setValue(3)
    assert "参数待应用" in window.pending_label.text()
    assert window.pending_label.isVisible()
    window.restore_button.click()
    assert window.selected_config() == window.request.config
    assert not window.parameters_pending and window.result is old_result
    assert [panel.view.pixmap.toImage() for panel in window.panels] == images


def test_preset_selection_save_delete_preserves_results(app, window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    process_sample(app, window)
    old_result = window.result
    window.preset_combo.setCurrentIndex(2)
    assert window.selected_config().threshold == 110 and window.parameters_pending
    assert not window.delete_preset_button.isEnabled()
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("演示参数", True))
    window.save_preset_button.click()
    assert len(window.preferences.presets) == 1
    assert window.delete_preset_button.isEnabled()
    config = window.selected_config()
    window.delete_preset_button.click()
    assert not window.preferences.presets
    assert window.result is old_result and window.selected_config() == config
    assert not window.save_preset("  ")
    assert not window.save_preset("默认自动")
    assert not window.busy


def test_restarting_window_restores_settings_without_image(app, tmp_path):
    from PySide6.QtCore import QSettings
    from dermademo.preferences import Preferences
    path = tmp_path / "restart.ini"
    def new_preferences():
        return Preferences(QSettings(str(path), QSettings.Format.IniFormat))
    first = MainWindow(preferences=new_preferences())
    first.show()
    first.resize(1100, 800)
    first.clip_spin.setValue(3)
    first.threshold_spin.setValue(95)
    assert first.save_preset("自动参数")
    first.close()
    app.processEvents()
    second = MainWindow(preferences=new_preferences())
    second.show()
    app.processEvents()
    available = second.screen().availableGeometry()
    assert second.width() <= max(1060, available.width())
    assert second.height() <= max(740, available.height())
    assert second.preferences.geometry
    assert second.frameGeometry().intersects(available)
    assert second.selected_config().clahe_clip == 3
    assert second.selected_config().threshold is None and second.threshold_spin.value() == 95
    assert not second.busy and second.image is None and second.result is None
    assert len(second.preferences.presets) == 1
    second.close()
    first.deleteLater()
    second.deleteLater()


def test_preset_enter_and_ctrl_s_save_actual_applied_config(app, window, tmp_path, monkeypatch):
    process_sample(app, window)
    window.preset_combo.setCurrentIndex(2)
    window.threshold_spin.setValue(100)
    assert window.save_preset("快捷键预设")
    window.activateWindow()
    window.panels[0].view.setFocus()
    app.processEvents()
    QTest.keyClick(window.panels[0].view, Qt.Key.Key_Return)
    wait_for_idle(app, window)
    assert window.request.config.threshold == 100
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(tmp_path))
    QTest.keyClick(window.panels[0].view, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    wait_for_idle(app, window)
    record = json.loads((window.last_export / "record.json").read_text(encoding="utf-8"))
    assert record["parameters"]["threshold"] == 100
    assert record["schema_version"] == "1.0"


def test_gui_timer_runs_during_processing_and_input_is_locked(app):
    class SlowBackend(DemoCPUBackend):
        def process(self, request):
            time.sleep(0.25)
            return super().process(request)

    window = MainWindow(SlowBackend())
    window.show()
    assert window.load_path(SAMPLE_PATH)
    wait_for_idle(app, window)
    ticks = []
    timer = QTimer()
    timer.setInterval(15)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    window.start_processing()
    assert window.busy
    assert not window.import_button.isEnabled()
    assert not window.clip_spin.isEnabled()
    assert not window.load_path(SAMPLE_PATH)
    assert not window.close()  # Cannot destroy a running QThread.
    wait_for_idle(app, window)
    timer.stop()
    assert len(ticks) >= 5
    assert window.result is not None
    assert window.import_button.isEnabled()
    window.close()


def test_processing_error_recovers_without_false_results(app):
    class BrokenBackend(DemoCPUBackend):
        def process(self, request):
            raise RuntimeError("模拟推理错误")

    window = MainWindow(BrokenBackend())
    window.show()
    window.load_path(SAMPLE_PATH)
    wait_for_idle(app, window)
    window.start_processing()
    wait_for_idle(app, window)
    assert window.result is None
    assert not window.save_button.isEnabled()
    assert window.process_button.isEnabled()
    assert "模拟推理错误" in window.status_label.text()
    window.backend = DemoCPUBackend()
    window.start_processing()
    wait_for_idle(app, window)
    assert window.result is not None
    window.close()


def test_stale_task_result_cannot_overwrite_current_view(app, window):
    process_sample(app, window)
    current = window.result
    window._handle_success("process", "expired-task", replace(current, task_id="expired-task"))
    assert window.result is current
    window.load_path(SAMPLE_PATH)
    wait_for_idle(app, window)
    window._handle_success("process", current.task_id, current)
    assert window.result is None
    assert window.area_label.text() == "—"


def test_empty_mask_and_save_failure_are_explicit(app, window, tmp_path):
    source = tmp_path / "flat.png"
    Image.fromarray(np.full((60, 80, 3), 200, np.uint8)).save(source)
    window.load_path(source)
    wait_for_idle(app, window)
    window.start_processing()
    wait_for_idle(app, window)
    assert window.area_label.text() == "0"
    assert window.diameter_label.text() == "不适用"
    assert "未检测到" in window.status_label.text()
    invalid_parent = tmp_path / "file"
    invalid_parent.write_text("occupied")
    window.export_to(invalid_parent)
    wait_for_idle(app, window)
    assert window.last_export is None
    assert "保存失败" in window.status_label.text()
    assert window.save_button.isEnabled()


def test_windows_chinese_fonts_and_small_window_sidebar(app, window):
    import os
    if os.name == "nt":
        assert QFontDatabase.families()
        assert QFontMetrics(app.font()).inFontUcs4(ord("皮"))
    window.resize(1060, 740)
    app.processEvents()
    scroll = window.findChild(QScrollArea)
    assert scroll is not None
    assert window.height() == 740
    assert window.process_button.isVisible()
    assert window.status_label.isVisible()
    assert scroll.verticalScrollBar().maximum() > 0


def test_importing_bundled_sample_keeps_synthetic_label(app, window):
    assert window.load_path(SAMPLE_PATH)
    wait_for_idle(app, window)
    assert window.is_synthetic
    assert "合成样例" in window.file_label.text()


def test_invalid_backend_result_does_not_display_partial_output(app):
    class InvalidBackend(DemoCPUBackend):
        def process(self, request):
            result = super().process(request)
            return replace(result, mask=result.mask[:10])

    window = MainWindow(InvalidBackend())
    window.show()
    window.load_path(SAMPLE_PATH)
    wait_for_idle(app, window)
    window.start_processing()
    wait_for_idle(app, window)
    assert window.result is None
    assert window.area_label.text() == "—"
    assert not any(panel.view.has_image for panel in window.panels[1:])
    assert "处理失败" in window.status_label.text()
    assert window.process_button.isEnabled()
    window.close()


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_enter_shortcut_processes_loaded_image(app, window, key):
    window.load_path(SAMPLE_PATH)
    wait_for_idle(app, window)
    window.activateWindow()
    window.panels[0].view.setFocus()
    app.processEvents()
    QTest.keyClick(window.panels[0].view, key)
    wait_for_idle(app, window)
    assert window.result is not None


def test_enter_in_parameter_input_commits_typed_value_and_processes(app, window):
    window.load_path(SAMPLE_PATH)
    wait_for_idle(app, window)
    window.threshold_mode.setCurrentIndex(1)
    window.activateWindow()
    edit = window.threshold_spin.lineEdit()
    edit.setFocus()
    edit.setText("阈值：95")
    app.processEvents()
    QTest.keyClick(edit, Qt.Key.Key_Return)
    wait_for_idle(app, window)
    assert window.result is not None
    assert window.request.config.threshold == 95
    assert window.result.metadata["effective_threshold"] == 95


@pytest.mark.parametrize("large_view", [False, True])
def test_ctrl_s_shortcut_opens_save_and_exports(app, window, tmp_path, monkeypatch, large_view):
    process_sample(app, window)
    calls = []

    def select_folder(*args):
        calls.append(args)
        return str(tmp_path)

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", select_folder)
    target = window
    if large_view:
        window.panels[3].open_viewer()
        target = window.panels[3].viewer
        target.view.setFocus()
    target.activateWindow()
    app.processEvents()
    QTest.keyClick(target, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    wait_for_idle(app, window)
    assert len(calls) == 1
    assert (window.last_export / "record.json").exists()


def test_enter_from_large_view_starts_new_processing(app, window):
    process_sample(app, window)
    previous_task = window.result.task_id
    window.panels[0].open_viewer()
    viewer = window.panels[0].viewer
    viewer.activateWindow()
    viewer.view.setFocus()
    app.processEvents()
    QTest.keyClick(viewer.view, Qt.Key.Key_Return)
    wait_for_idle(app, window)
    assert window.result is not None
    assert window.result.task_id != previous_task


def test_shortcuts_without_input_or_result_do_nothing(app, window, monkeypatch):
    def unexpected_dialog(*args):
        pytest.fail("Save dialog opened without a result")

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", unexpected_dialog)
    window.activateWindow()
    app.processEvents()
    QTest.keyClick(window, Qt.Key.Key_Return)
    QTest.keyClick(window, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert not window.busy
    assert window.request is None
    assert window.result is None


def test_shortcuts_during_processing_do_not_start_another_task(app, window, monkeypatch):
    class SlowBackend(DemoCPUBackend):
        def process(self, request):
            time.sleep(0.2)
            return super().process(request)

    window.backend = SlowBackend()
    window.load_path(SAMPLE_PATH)
    wait_for_idle(app, window)
    window.activateWindow()
    app.processEvents()
    QTest.keyClick(window, Qt.Key.Key_Return)
    current_task = window.request.task_id
    assert window.busy

    def unexpected_dialog(*args):
        pytest.fail("Save dialog opened during processing")

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", unexpected_dialog)
    QTest.keyClick(window, Qt.Key.Key_Return)
    QTest.keyClick(window, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert window.request.task_id == current_task
    wait_for_idle(app, window)
    assert window.result.task_id == current_task
