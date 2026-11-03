"""Experiment context regression; loopback data is not physical FPGA evidence."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Event
import json

import numpy as np
import pytest
from PySide6.QtWidgets import QMessageBox

from test_gui import app,window
from test_rgb_device_gui import drain
from test_hardware_lab_gui import wait
from formal_mask_peer import FormalPeer,local_config
from teaching_fixtures import prepared_case
from dermademo.hardware_lab_ui import HardwareLabDialog
from dermademo.hardware_lab import run,load_frame,successful_frames


class FakeCPU:
    calls=0
    def __init__(self,_):pass
    def run(self,rgb,**_):
        FakeCPU.calls+=1
        raw=bytearray(65536);raw[sha256(rgb).digest()[0]]=255
        return bytes(raw),dict(host_request_ms=3.,report={'rgb_to_mask_ms':2.},runtime={'load_ms':4.,'environment':{'device':'cpu'}})
    def close(self):pass


def make_experiment(tmp_path,monkeypatch,*,telemetry='full',warmup=1,repeats=2):
    import dermademo.hardware_lab as lab
    monkeypatch.setattr(lab,'CPUClient',FakeCPU)
    prepared=[prepared_case(0),prepared_case(1)]
    with FormalPeer() as peer:
        folder,doc=run(prepared,replace(local_config(peer),telemetry_mode=telemetry),tmp_path,python='fixture',
                       warmup=warmup,repeats=repeats,state_dir=tmp_path/'state')
    assert doc['status']=='completed',doc.get('error')
    return folder,doc


def test_confirmed_5_warmup_20_formal_auto_loads_and_switches_without_running_again(app,window,tmp_path,monkeypatch):
    import dermademo.hardware_lab as lab
    import dermademo.hardware_lab_ui as ui
    monkeypatch.setattr(lab,'CPUClient',FakeCPU);FakeCPU.calls=0
    d=HardwareLabDialog(window);d.show();drain(app)
    try:
        with FormalPeer() as peer:
            monkeypatch.setattr(ui,'FormalConfig',lambda **_:replace(local_config(peer),telemetry_mode='full'))
            monkeypatch.setattr(ui,'default_output',lambda:tmp_path)
            monkeypatch.setattr(ui,'selected_python',lambda _:'fixture')
            monkeypatch.setattr(ui.QMessageBox,'question',lambda *_:QMessageBox.StandardButton.Yes)
            d.inputs=[prepared_case()];d.refresh();d.confirm_run();wait(app,d)
            assert d.document['status']=='completed',d.status.text()
            assert d.frame_combo.count()==25 and d.stage_model.rowCount()==82
            rows=successful_frames(d.document)
            assert d.snapshot['trial_id']==rows[-1]['trial_id'] and d.snapshot['warmup'] is False
            assert d.document['summary']['fpga_int8']['success']==20
            assert '实验总体' in d.experiment_identity.text() and '正式测量' in d.identity.text()
            assert d.pdf_button.isEnabled() and d.open_pdf.isEnabled() and d.open_dir.isEnabled()
            requests=len(peer.requests);cpu_calls=FakeCPU.calls
            assert d.baseline is None and '尚未设置基线' in d.compare_label.text()
            for index in (0,12,24):
                d.frame_combo.setCurrentIndex(index);wait(app,d)
                row=rows[index]
                assert d.snapshot['identity']['frame_id']==row['frame_id']
                assert d.snapshot['warmup']==row['warmup']
                assert d.stage_model.rowCount()==82 and d.stats_model.rowCount()>0
                assert d.views[0].has_image and d.views[1].has_image and d.views[2].has_image
                assert '帧'+str(row['frame_id']) in d.identity.text()
                assert '尚未冻结' in d.stats_notice.text()
            d.export_pdf();wait(app,d)
            assert len(peer.requests)==requests and FakeCPU.calls==cpu_calls
    finally:
        if d.busy:wait(app,d)
        d.close();drain(app)


@pytest.mark.parametrize('theme',['light','dark','high-contrast'])
def test_external_single_frame_preserves_experiment_and_summary_mode_clears_stages(app,window,tmp_path,monkeypatch,theme):
    app.fluent_theme.set_mode(theme)
    full,doc=make_experiment(tmp_path/'full',monkeypatch)
    summary,sdoc=make_experiment(tmp_path/'summary',monkeypatch,telemetry='summary',warmup=0,repeats=1)
    d=HardwareLabDialog(window);d.show();drain(app)
    try:
        d.load_folder(full);wait(app,d)
        assert d.frame_combo.count()==6 and d.stage_model.rowCount()==82
        preserved=d.document;folder=d.folder
        stats=[d.summary_model.item(1,c).text() for c in range(d.summary_model.columnCount())]
        srow=successful_frames(sdoc)[0]
        d.load_folder(summary/srow['record']);wait(app,d)
        assert d.document is preserved and d.folder==folder and d.frame_combo.count()==6
        assert d.frame_combo.currentIndex()==-1
        assert '外部单帧' in d.identity.text() and d.stage_model.rowCount()==0
        assert '3页总计' in d.stage_notice.text() and d.snapshot['telemetry_mode']=='summary'
        assert [d.summary_model.item(1,c).text() for c in range(d.summary_model.columnCount())]==stats
        assert d.pdf_button.isEnabled() and d.open_dir.isEnabled()
        d.frame_combo.setCurrentIndex(0);wait(app,d)
        assert d.stage_model.rowCount()==82 and d.snapshot['warmup'] is True
        d.pin_baseline();baseline=d.baseline
        d.frame_combo.setCurrentIndex(5);wait(app,d)
        assert d.baseline is baseline and '不能计算正式提速' in d.compare_label.text()
    finally:d.close();drain(app);app.fluent_theme.set_mode('light')


def test_last_formal_fpga_is_selected_even_if_later_cpu_succeeds_or_warmup_follows(app,window,tmp_path,monkeypatch):
    folder,doc=make_experiment(tmp_path,monkeypatch)
    # Presentation accepts partial experiments; success order is not inferred from CPU rows.
    formal=[r for r in successful_frames(doc) if not r['warmup']]
    warm=next(r for r in successful_frames(doc) if r['warmup'])
    doc=deepcopy(doc);doc['trials'].append(dict(warm,trial_id=100))
    d=HardwareLabDialog(window);d.show();drain(app)
    try:
        d.show_experiment(folder,doc);wait(app,d)
        assert d.snapshot['trial_id']==formal[-1]['trial_id'] and not d.snapshot['warmup']
    finally:d.close();drain(app)


def test_no_success_or_bad_frame_clears_old_evidence_but_keeps_experiment(app,window,tmp_path,monkeypatch):
    folder,doc=make_experiment(tmp_path/'good',monkeypatch)
    d=HardwareLabDialog(window);d.show();drain(app)
    try:
        d.show_experiment(folder,doc);wait(app,d)
        row=successful_frames(doc)[0]
        (folder/row['record']/'record.json').write_text('{}','utf-8')
        d.frame_combo.setCurrentIndex(0);wait(app,d)
        assert d.snapshot is None and d.stage_model.rowCount()==0 and d.stats_model.rowCount()==0
        assert '所选帧证据不可用' in d.identity.text() and d.pdf_button.isEnabled() and d.document is doc
        cancelled=Event();cancelled.set()
        empty,edoc=run([prepared_case()],local_config_stub(),tmp_path/'empty',warmup=0,repeats=1,
                      mode='fpga_sequence',cancel=cancelled,state_dir=tmp_path/'empty-state')
        d.show_experiment(empty,edoc);d.refresh()
        assert d.frame_combo.count()==0 and d.snapshot is None and not d.views[0].has_image
        assert '没有成功FPGA帧' in d.identity.text() and d.pdf_button.isEnabled()
    finally:d.close();drain(app)


def local_config_stub():
    from dermademo.device.formal_session import FormalConfig
    return FormalConfig()


def test_frame_reader_rejects_wrong_row_identity_without_inference(tmp_path,monkeypatch):
    folder,doc=make_experiment(tmp_path,monkeypatch)
    row=successful_frames(doc)[0]
    assert load_frame(folder,doc,row['trial_id'])['snapshot']['identity']==row['identity']
    wrong=deepcopy(doc);wrong['trials'][wrong['trials'].index(next(r for r in wrong['trials'] if r['trial_id']==row['trial_id']))]['frame_id']+=1
    with pytest.raises(ValueError,match='帧号不符'):load_frame(folder,wrong,row['trial_id'])
    with pytest.raises(ValueError):load_frame(folder,doc,999999)
