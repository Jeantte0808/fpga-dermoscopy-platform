from pathlib import Path
from dataclasses import replace
import time

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
import pytest

from dermademo.formal_results import replay_pair
from dermademo.resources import resource_path
from dermademo.records import load_record
from test_gui import app, window, wait_for_idle
from test_rgb_device_gui import drain, wait


def test_preview_no_network_no_result_refresh_invalid_image_preserves_result(app, window, tmp_path, monkeypatch):
    import dermademo.formal_device_ui as ui
    window.commit_formal_result(replay_pair())
    previous = window.result
    monkeypatch.setattr(ui, 'run_formal', lambda *_a, **_k: pytest.fail('preview connected to FPGA'))
    window.open_formal_device()
    dialog = window.formal_dialog
    dialog.mode_combo.setCurrentIndex(1)
    dialog.source_combo.setCurrentIndex(2)
    source = resource_path('full_image_root') / 'samples/03_ISIC_0000030/ISIC_0000030_derived_lossless.png'
    dialog.prepare_selected_image(source)
    assert dialog.busy and not dialog.run_button.isEnabled()
    wait(app, dialog)
    assert dialog.prepared_image is not None and dialog.preview.has_image
    assert window.result is previous
    dialog.clip_spin.setValue(4)
    assert window.result is previous
    invalid = tmp_path / 'alpha.png'
    Image.new('RGBA', (10, 8)).save(invalid)
    dialog.prepare_selected_image(invalid)
    wait(app, dialog)
    assert dialog.prepared_image is None and window.result is previous
    assert '失败' in dialog.status.text()
    dialog.start()
    assert '请先选择' in dialog.status.text() and not dialog.busy
    dialog.close(); drain(app)


def test_image_preview_enter_real_local_wire_ctrl_s_reopen(app, window, tmp_path, monkeypatch):
    from formal_mask_peer import FormalPeer, local_config
    import dermademo.formal_device_ui as ui
    source = tmp_path / '中文 自选.jpg'
    source.write_bytes((resource_path('full_image_root') / 'samples/01_ISIC_0000000/ISIC_0000000.jpg').read_bytes())
    with FormalPeer() as peer:
        monkeypatch.setattr(ui, 'FormalConfig', lambda **kwargs: replace(local_config(peer), **kwargs))
        monkeypatch.setattr(ui, 'default_output', lambda: tmp_path / 'captures')
        window.open_formal_device()
        dialog = window.formal_dialog
        dialog.mode_combo.setCurrentIndex(1)
        dialog.source_combo.setCurrentIndex(2)
        assert dialog.hardware_combo.isHidden()
        assert dialog.hardware_combo.currentData()=='p7-flash'
        dialog.prepare_selected_image(source)
        wait(app, dialog)
        assert not peer.requests
        snapshot = dialog.prepared_image
        source.unlink()  # Transmission and saving must use the previewed snapshot.
        dialog.activateWindow(); dialog.run_button.setFocus()
        drain(app)
        QTest.keyClick(dialog, Qt.Key.Key_Return)
        deadline = time.monotonic() + 20
        while dialog.busy and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.002)
        app.processEvents()
        assert not dialog.busy and dialog.outcome.report['complete'], dialog.status.text()
        assert bytes(peer.rgb) == snapshot.rgb
    result = window.result
    assert 'q ≥ 1' in window.threshold_info.text()
    assert 'V6' not in result.capabilities.algorithm_version
    assert 'formal_perf_p7_flash' in window.version_label.toolTip()
    assert '79,851' in window.resource_label.text()
    assert '本机协议模拟' in window.notice.text()
    assert '未核实' in window.technical_origin.text()
    assert not window.technical_details.isVisible()
    dialog.mode_combo.setCurrentIndex(0)
    assert window.result is result
    monkeypatch.setattr(ui.QFileDialog, 'getExistingDirectory', lambda *_a: str(tmp_path))
    QTest.keyClick(dialog, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    wait_for_idle(app, window); drain(app)
    saved = window.last_export
    assert load_record(saved).request.source_bytes == snapshot.source_bytes
    window.start_open_record(saved); wait_for_idle(app, window)
    assert '本次未连接 FPGA' in window.notice.text()
    assert 'frame ' not in window.record_label.text()
    assert 'frame ' in window.technical_origin.text()


@pytest.mark.parametrize('mode', ['light', 'dark', 'high-contrast'])
def test_new_input_ui_theme_and_old_hardware_unknown(app, window, mode):
    app.fluent_theme.set_mode(mode)
    window.commit_formal_result(replay_pair())
    assert '旧记录未记录' in window.version_label.text()
    assert 'q ≥ 1' in window.threshold_info.text()
    window.open_formal_device()
    dialog = window.formal_dialog
    dialog.mode_combo.setCurrentIndex(1)
    dialog.source_combo.setCurrentIndex(2)
    assert dialog.image_button.isEnabled()
    assert dialog.image_button.accessibleName()
    drain(app)
    assert not dialog.raw_button.isVisible() and not dialog.record_button.isVisible()
    assert dialog.preview.height() <= 180
    from PySide6.QtWidgets import QScrollArea
    sidebar = window.findChild(QScrollArea, 'sidebarScroll')
    assert sidebar.horizontalScrollBar().maximum() == 0
    dialog.close(); drain(app)
    window.clear_result()
    assert not window.version_label.toolTip()
    assert '正式模型入口：FPGA Mask' in window.version_label.text()
    assert window.panels[0].title == '01  原始影像'
    app.fluent_theme.set_mode('light')
