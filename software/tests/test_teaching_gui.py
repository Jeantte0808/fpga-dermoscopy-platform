from copy import deepcopy
from pathlib import Path
import json
from time import monotonic, sleep

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
import pytest

from dermademo.formal_results import replay_pair
from dermademo.formal_input import prepare_image
from dermademo.storage import save_result
from dermademo.resources import resource_path
from test_gui import app, window
from test_rgb_device_gui import drain
from test_formal_queue import install_offline_wire
from teaching_fixtures import prepared_case


def settled(app, dialog, predicate=lambda: True, timeout=15):
    end = monotonic()+timeout
    stable = 0
    while monotonic()<end:
        app.processEvents()
        QTest.qWait(5)
        # Give Python worker code the GIL as well as pumping Qt's event loop.
        sleep(.005)
        if not dialog.busy and predicate():
            stable += 1
            if stable >= 3:
                drain(app)
                return
        else:
            stable = 0
    pytest.fail(dialog.status.text())


@pytest.mark.parametrize('mode', ['light','dark','high-contrast'])
def test_validation_readonly_current_reference_history_and_theme(app,window,mode):
    app.fluent_theme.set_mode(mode)
    window.commit_formal_result(replay_pair())
    original = window.result
    window.open_validation()
    dialog=window.validation_dialog
    settled(app,dialog,lambda:dialog.pair is not None)
    assert dialog.comparison['byte_exact'] and 'dice' not in dialog.comparison
    assert all(v.has_image for v in dialog.views)
    assert '历史' in dialog.identity_label.text()
    assert dialog.stage_model.rowCount()==82
    dialog.history_button.click()
    settled(app,dialog,lambda:dialog.history_model.rowCount()==2)
    assert '9.29%' in dialog.history_model.item(0,3).text()
    assert '原报告旧V6' in dialog.history_note.toPlainText()
    assert window.result is original
    assert dialog.current_button.accessibleName() or dialog.current_button.text()
    dialog.close();drain(app)
    assert window.validation_dialog is None
    app.fluent_theme.set_mode('light')


def test_missing_reference_and_failed_import_never_keep_stale_scores(app,window,tmp_path,monkeypatch):
    window.commit_formal_result(replay_pair())
    window.open_validation();dialog=window.validation_dialog
    settled(app,dialog,lambda:dialog.comparison is not None)
    assert dialog.comparison['byte_exact']
    path=tmp_path/'invalid.json';path.write_text('{}','utf-8')
    monkeypatch.setattr('dermademo.validation_ui.QFileDialog.getOpenFileName',lambda *_a,**_k:(str(path),''))
    dialog.choose_reference()
    settled(app,dialog)
    assert dialog.comparison is None and not dialog.views[1].has_image
    assert '不可用' in dialog.status.text()
    dialog.close();drain(app)


def test_queue_add_freezes_preview_does_not_process_and_saves_each_frame(app,window,tmp_path,monkeypatch):
    install_offline_wire(monkeypatch,tmp_path)
    window.commit_formal_result(replay_pair());old=window.result
    window.open_formal_queue();dialog=window.queue_dialog
    dialog.output_parent=tmp_path/'outputs'
    assert not dialog.run_button.isEnabled()
    paths=[prepared_case(i).path for i in (0,1)]
    dialog.add_paths(paths)
    settled(app,dialog,lambda:len(dialog.inputs)==2 and dialog.preview.has_image)
    assert window.result is old
    assert dialog.run_button.isEnabled()
    dialog.dwell_spin.setValue(0)
    dialog.activateWindow();dialog.run_button.setFocus()
    QTest.keyClick(dialog,Qt.Key.Key_Return)
    settled(app,dialog,lambda:dialog.session is not None and dialog.session.status=='completed')
    assert dialog.session.summary()['success_count']==2
    assert dialog.queue_model.item(0,2).text()=='分割 / 分类成功'
    assert dialog.session.summary()['classification_model_load_count']==1
    assert window.analysis_record()==Path(dialog.session.rows[-1]['record_directory'])
    assert dialog.queue_model.item(1,2).text()=='分割 / 分类成功'
    assert window.result.metadata['source_kind']=='protocol_simulator'
    assert len(list(dialog.session.directory.glob('presentation_*.json')))==2
    assert len(list((dialog.session.directory/'records').glob('*/record.json')))==2
    dialog.queue_table.selectRow(0)
    settled(app,dialog)
    dialog.open_selected_record()
    settled(app,dialog,lambda:window.opened_record is not None)
    saved=[]
    monkeypatch.setattr(window,'choose_export',lambda:saved.append(True))
    QTest.keyClick(dialog,Qt.Key.Key_S,Qt.KeyboardModifier.ControlModifier)
    assert saved==[True]
    dialog.close();drain(app)
    assert window.queue_dialog is None


def test_queue_pause_between_frames_and_failure_requires_recovery(app,window,tmp_path,monkeypatch):
    calls=install_offline_wire(monkeypatch,tmp_path)
    window.open_formal_queue();dialog=window.queue_dialog
    dialog.output_parent=tmp_path/'outputs'
    dialog.add_paths([prepared_case().path])
    settled(app,dialog,lambda:len(dialog.inputs)==1)
    dialog.mode_combo.setCurrentIndex(1)
    dialog.dwell_spin.setValue(0)
    dialog.start_queue()
    dialog.pause_queue()  # Pending frame finishes and saves, no second frame.
    settled(app,dialog,lambda:dialog.session.status=='paused')
    assert len(calls)==1 and dialog.session.summary()['success_count']==1
    dialog.resume_queue()
    dialog.pause_queue()
    settled(app,dialog,lambda:dialog.session.status=='paused')
    assert len(calls)==2
    assert calls[0].reuse_model is False and calls[1].reuse_model is True
    dialog.stop_queue()
    assert dialog.session.status=='stopped'
    dialog.close();drain(app)


def test_queue_can_add_existing_confirmed_roi_input_without_using_mask_as_input(app,window,tmp_path,monkeypatch):
    from dermademo.formal_results import live_pair
    from dermademo.roi import decode_source
    from dermademo.roi_preprocessing import ConfirmedROIProcessor
    from teaching_fixtures import offline_outcome
    root=resource_path('roi_root');row=json.loads((root/'samples.json').read_text('utf-8'))[1]
    prepared=ConfirmedROIProcessor().prepare(decode_source(root/row['directory']/row['input_file']),row['roi'])
    window.commit_formal_result(live_pair(offline_outcome(prepared,tmp_path)))
    window.open_formal_queue();dialog=window.queue_dialog
    dialog.output_parent=tmp_path/'outputs'
    dialog.add_current()
    settled(app,dialog,lambda:len(dialog.inputs)==1 and dialog.preview.has_image)
    from dermademo.formal_queue import read_input
    snapshot=read_input(dialog.inputs[0])
    assert snapshot.rgb==prepared.rgb and snapshot.context['roi']['bounds']==row['roi']
    assert 'ROI' in dialog.queue_model.item(0,1).text()
    dialog.close();drain(app)


@pytest.mark.parametrize('kind',['queue','validation'])
def test_analysis_dialog_minimum_geometry_and_close_before_pending_callbacks(app,window,kind):
    from PySide6.QtCore import QPoint,QRect
    if kind=='queue':
        window.open_formal_queue();dialog=window.queue_dialog
    else:
        window.open_validation();dialog=window.validation_dialog
    dialog.resize(800,630)
    app.processEvents()
    for name in ('run_button','pause_button','resume_button','stop_button','cancel_button','new_button','reference_button'):
        button=getattr(dialog,name,None)
        if button is not None:
            assert dialog.rect().contains(QRect(button.mapTo(dialog,QPoint(0,0)),button.size()))
    dialog.close();drain(app)
