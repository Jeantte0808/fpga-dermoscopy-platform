from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from dermademo.formal_results import replay_pair
from dermademo.hardware import hardware_declaration
from dermademo.roi_ui import ROIDialog
from dermademo.records import load_record
from formal_mask_peer import FormalPeer,local_config
from test_gui import app,window,wait_for_idle
from test_rgb_device_gui import drain,wait


@pytest.mark.parametrize('theme',['light','dark','high-contrast'])
def test_manual_selection_no_refresh_or_relabel_and_historical_card(app,window,theme):
    app.fluent_theme.set_mode(theme)
    pair=replay_pair();window.commit_formal_result(pair)
    previous=window.result
    window.open_formal_device();dialog=window.formal_dialog
    assert dialog.hardware_combo.currentData()=='p7-flash' and not dialog.hardware_combo.isEnabled()
    dialog.mode_combo.setCurrentIndex(1)
    assert dialog.hardware_combo.isHidden()
    assert '79,851' in dialog.resource_label.text() and 'Flash固化版' in dialog.resource_label.text()
    assert window.result is previous and '79,851' not in window.resource_label.text()
    dialog.mode_combo.setCurrentIndex(0)
    assert '原记录' in dialog.resource_label.text() and not dialog.hardware_combo.isEnabled()
    dialog.close();drain(app)
    for profile,number in [('p7','79,851'),('v6-r1-flash','78,427'),('unspecified','未记录')]:
        pair=replay_pair();pair[1].metadata['hardware_declaration']=hardware_declaration(profile)
        window.commit_formal_result(pair)
        assert number in window.resource_label.text()
    roi=ROIDialog(window)
    assert roi.hardware_combo.currentData()=='p7-flash' and roi.hardware_combo.accessibleName()
    assert roi.hardware_combo.isHidden()
    assert window.result.metadata['hardware_declaration'] is None
    roi.close();drain(app)
    app.fluent_theme.set_mode('light')


def test_enter_sends_selected_p7_ctrl_s_and_reopen_keep_snapshot(app,window,tmp_path,monkeypatch):
    import dermademo.formal_device_ui as ui
    with FormalPeer(busy_once=False) as peer:
        monkeypatch.setattr(ui,'FormalConfig',lambda **kwargs: replace(local_config(peer),**kwargs))
        monkeypatch.setattr(ui,'default_output',lambda:tmp_path/'captures')
        window.open_formal_device();dialog=window.formal_dialog
        dialog.mode_combo.setCurrentIndex(1);assert dialog.hardware_combo.isHidden()
        dialog.reuse_checkbox.setChecked(True)
        dialog.run_button.setFocus();dialog.activateWindow();drain(app)
        QTest.keyClick(dialog,Qt.Key.Key_Return)
        assert not dialog.hardware_combo.isEnabled()
        wait(app,dialog)
    assert dialog.outcome.report['complete'],dialog.status.text()
    assert dialog.outcome.report['hardware_declaration']['candidate']=='formal_perf_p7_flash'
    assert '79,851' in window.resource_label.text()
    dialog.hardware_combo.setCurrentIndex(1)
    assert '79,851' in window.resource_label.text()  # Last success remains P7.
    monkeypatch.setattr(ui.QFileDialog,'getExistingDirectory',lambda *_a:str(tmp_path))
    QTest.keyClick(dialog,Qt.Key.Key_S,Qt.KeyboardModifier.ControlModifier)
    wait_for_idle(app,window);drain(app)
    saved=window.last_export
    assert load_record(saved).result.metadata['hardware_declaration']==hardware_declaration('p7-flash')
    if window.formal_dialog is not None:window.formal_dialog.close()
    drain(app)
    window.start_open_record(saved);wait_for_idle(app,window)
    assert '79,851' in window.resource_label.text() and '非自动识别' in window.version_label.toolTip()
