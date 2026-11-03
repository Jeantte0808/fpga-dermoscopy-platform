from pathlib import Path
from copy import deepcopy
from threading import Event
import json
import numpy as np
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtPdf import QPdfDocument
from dermademo import classification,ai_service
from dermademo.formal_queue import FormalQueue,freeze_input,read_input
from dermademo.device.formal_session import FormalConfig
from dermademo.records import load_record
from dermademo.analysis_snapshot import build_snapshot,upload_facts,save_selection
from dermademo.unified_report import export_batch_pdf,export_single,valid_report
from dermademo.review import save_review
from dermademo.cpu_runtime import probe_python,save_checked,selected_python,validate_path
from dermademo.preferences import Preferences
from test_ai import app,window,record,response,KEY
from test_formal_queue import inputs,install_offline_wire
from test_teaching_gui import settled
from test_rgb_device_gui import drain

PYTHON=Path('.cache/cpu-venv/Scripts/python.exe').resolve()


def test_report_UTC_and_offset_dates_share_one_local_seconds_clock():
    from dermademo.batch_pdf import timestamp
    utc=timestamp('2026-10-31T01:02:03+00:00')
    assert utc==timestamp('2026-10-31T09:02:03+08:00') and utc.endswith(':03')
    assert timestamp('2026-10-31T09:02:03')=='2026-10-31 09:02:03'


def pdf_text(path):
    d=QPdfDocument();assert d.load(str(path))==QPdfDocument.Error.None_
    text='\n'.join(d.getAllText(i).text() for i in range(d.pageCount()));d.close();del d
    return text


def test_shared_python_probe_save_restart_and_invalid_path(app,tmp_path,window):
    result=probe_python(str(PYTHON));assert result['probe']['device']=='cpu'
    p=tmp_path/'settings.ini';prefs=Preferences(QSettings(str(p),QSettings.Format.IniFormat))
    save_checked(prefs,result)
    restarted=Preferences(QSettings(str(p),QSettings.Format.IniFormat));assert selected_python(restarted)==str(PYTHON)
    window.preferences=restarted
    from dermademo.classification_ui import ClassificationDialog
    from dermademo.cpu_ui import CPUDialog
    from dermademo.formal_queue_ui import FormalQueueDialog
    dialogs=[ClassificationDialog(window),CPUDialog(window),FormalQueueDialog(window)]
    assert dialogs[0].python.text()==dialogs[1].python_edit.text()==dialogs[2].class_python.text()==str(PYTHON)
    for d in dialogs:d.close()
    for bad in ('https://example.com/python.exe','python.exe',str(tmp_path/'missing/python.exe')):
        with pytest.raises(ValueError):validate_path(bad)
    prefs._write({'cpu/python':str(tmp_path/'gone/python.exe')})
    assert selected_python(Preferences(QSettings(str(p),QSettings.Format.IniFormat)))==''


def test_queue_classifies_each_original_once_and_reuses_one_model(app,tmp_path,monkeypatch):
    install_offline_wire(monkeypatch,tmp_path)
    items=inputs(tmp_path);items.append(freeze_input(read_input(items[0]).path,tmp_path/'again',prepared=read_input(items[0])))
    queue=FormalQueue(items,FormalConfig(),tmp_path/'run',classification_enabled=True,classification_python=str(PYTHON))
    rows=[queue.step() for _ in items]
    assert all(s.success and s.row['classification']['status']=='success' for s in rows)
    assert [s.row['classification']['model_loaded_this_item'] for s in rows]==[True,False,False]
    assert queue.summary()['classification_model_load_count']==1 and queue._classifier is None
    for item,step in zip(items,rows):
        r=load_record(step.row['record_directory']);p=read_input(item);c=classification.for_record(r)
        assert c['identity']['source_sha256']==__import__('hashlib').sha256(p.source_bytes).hexdigest()
        assert (r.directory/'input.rgb').read_bytes()==p.rgb
        assert step.row['classification']['scope']=='whole_original_image'
        facts=upload_facts(build_snapshot(r.directory))
        assert facts['classification']['model_version']==classification.MODEL
        assert facts['classification']['scope']=='whole_original_image' and not facts['classification']['scores_are_disease_probability']
    pdf=tmp_path/'batch.pdf';export_batch_pdf(queue.summary(),pdf)
    text=pdf_text(pdf)
    assert all(s in text for s in ('批次总览','实际模型输入','原始Mask','模型分数','不是患病概率','未运行'))
    assert valid_report(pdf)


def test_classification_failure_does_not_fail_segmentation_or_next_frame(tmp_path,monkeypatch):
    install_offline_wire(monkeypatch,tmp_path)
    original=classification.ClassifierClient
    class OneFailure(original):
        def predict(self,*args,**kwargs):
            value=super().predict(*args,**kwargs)
            if not getattr(self,'injected',False):self.injected=True;raise RuntimeError('injected classification-only failure')
            return value
    monkeypatch.setattr(classification,'ClassifierClient',OneFailure)
    queue=FormalQueue(inputs(tmp_path),FormalConfig(),tmp_path/'run',classification_enabled=True,classification_python=str(PYTHON))
    first,second=queue.step(),queue.step()
    assert first.success and first.row['local_record_complete'] and first.row['hardware_finish_confirmed']
    assert first.row['classification']['status']=='failed' and second.row['classification']['status']=='success'
    assert queue.summary()['failure_count']==0 and queue.summary()['classification_failure_count']==1
    assert queue.summary()['classification_model_load_count']==1
    load_record(first.row['record_directory'])


def test_cancel_during_classification_keeps_finished_frame_and_stops_queue(tmp_path,monkeypatch):
    install_offline_wire(monkeypatch,tmp_path);cancel=Event()
    def cancelled(*args,**kwargs):cancel.set();raise InterruptedError('cancel after FINISH')
    monkeypatch.setattr(classification,'run',cancelled)
    queue=FormalQueue(inputs(tmp_path),FormalConfig(),tmp_path/'run',classification_enabled=True,classification_python=str(PYTHON))
    step=queue.step(cancel=cancel)
    assert step.success and step.row['classification']['status']=='cancelled' and queue.status=='stopped'
    assert step.row['hardware_finish_confirmed'] and load_record(step.row['record_directory'])
    assert len(queue.rows)==1


def test_snapshot_review_binding_and_ai_context_never_switch_to_new_revision(app,tmp_path,monkeypatch):
    folder=record(tmp_path);raw=load_record(folder).result.mask;mask=raw.copy();mask[0,0]=255-mask[0,0]
    revision=save_review(folder,mask,texts=dict(observations='人工所见',summary='待审核',advice='核对原图'))
    save_selection(folder,revision)
    payloads=[]
    def post(_base,_key,payload):payloads.append(json.loads(payload));return response()
    result=ai_service.run_analysis(folder,'https://dashscope.aliyuncs.com',KEY,post=post,historical=True)
    assert result['meta']['cloud_complete'] and result['meta']['pdf_complete']
    task=Path(result['task']);facts=json.loads((task/'facts.json').read_text('utf-8'))
    assert facts['analyses']['manual']['review_id']==revision.name and len(payloads[0]['messages'][1]['content'])==4
    assert 'operator' not in facts['analyses']['manual']
    before={task/name:(task/name).read_bytes() for name in ('facts.json','context.json','response.json','analysis.json')}
    mask[0,1]=255-mask[0,1];later=save_review(folder,mask);save_selection(folder,later)
    current=build_snapshot(folder)
    assert current['context']['manual']['document']['review_id']==later.name and current['ai']['status']=='not_run'
    monkeypatch.setattr(ai_service,'post_once',lambda *_:pytest.fail('PDF re-export must not call cloud'))
    regenerated=ai_service.regenerate_pdf(task)
    assert regenerated['meta']['pdf_complete'] and len(payloads)==1
    text=''.join(pdf_text(task/'analysis.pdf').split())
    assert revision.name in text and later.name not in text
    assert all(p.read_bytes()==b for p,b in before.items())


def test_failed_ai_still_exports_local_unified_report_without_retry(app,tmp_path,monkeypatch):
    folder=record(tmp_path);calls=[]
    def failed(*_):calls.append(1);raise RuntimeError(KEY)
    result=ai_service.run_analysis(folder,'https://dashscope.aliyuncs.com',KEY,post=failed)
    task=Path(result['task']);assert not result['meta']['cloud_complete'] and result['meta']['pdf_complete']
    assert result['meta']['pdf_kind']=='base_report_ai_unavailable' and len(calls)==1
    assert 'AI' in pdf_text(task/'analysis.pdf') and KEY not in pdf_text(task/'analysis.pdf')
    monkeypatch.setattr(ai_service,'post_once',lambda *_:pytest.fail('no automatic retry'))
    ai_service.regenerate_pdf(task);assert len(calls)==1
    ai_service.load_display_task(task)


def test_home_quality_preview_is_immediate_and_does_not_refresh_old_result(app,window,tmp_path):
    from teaching_fixtures import prepared_case
    window.commit_formal_result(__import__('dermademo.formal_results',fromlist=['replay_pair']).replay_pair())
    previous=window.result
    window.show_input_quality(bytes([255])*196608,'待处理过亮测试输入')
    assert window.input_quality_banner.isVisible() and '高亮' in window.input_quality_banner.text()
    assert window.result is previous
    window.show_input_quality(prepared_case().rgb,'另一张输入')
    assert '另一张输入' in window.input_quality_banner.text() and window.result is previous


def test_roi_queue_classifies_whole_source_and_report_marks_scope(app,tmp_path,monkeypatch):
    from dermademo.resources import resource_path
    from dermademo.roi import decode_source,get_roi_processor
    from teaching_fixtures import prepared_case
    p=prepared_case();source=decode_source(p.path,p.source_bytes)
    root=resource_path('roi_root');sample=json.loads((root/'samples.json').read_text('utf-8'))[0]
    # Use the delivered ROI golden coordinates rather than an invented mask.
    rect=sample.get('roi',sample.get('bounds'))
    if isinstance(rect,dict):rect=rect.get('bounds')
    prepared=get_roi_processor().prepare(source,tuple(rect))
    install_offline_wire(monkeypatch,tmp_path)
    queue=FormalQueue([freeze_input(prepared.path,tmp_path/'snap',prepared=prepared)],FormalConfig(),tmp_path/'roi-run',
                      classification_enabled=True,classification_python=str(PYTHON))
    step=queue.step();assert step.success and step.row['classification']['status']=='success'
    r=load_record(step.row['record_directory']);classified=classification.for_record(r)
    assert classified['identity']['source_sha256']==__import__('hashlib').sha256(p.source_bytes).hexdigest()
    assert step.row['identity']['roi']==list(rect)
    pdf=export_single(r.directory);text=pdf_text(Path(pdf['pdf']))
    assert '与本ROI分割范围不同' in text and '框外未处理' in text
    changed=r.result.mask.copy();changed[0,0]=255-changed[0,0]
    revision=save_review(r.directory,changed,texts=dict(observations='离线测试',summary='核对版本',advice='复核边界'))
    revised=export_single(r.directory,review=revision)
    doc=QPdfDocument();assert doc.load(revised['pdf'])==QPdfDocument.Error.None_
    assert doc.pageCount() in (2,3)
    assert all(s in doc.getAllText(doc.pageCount()-1).text() for s in ('人工修订叠加','审核签署'))
    doc.close()


def test_missing_python_marks_classification_only_and_partial_pdf(app,tmp_path,monkeypatch):
    install_offline_wire(monkeypatch,tmp_path)
    queue=FormalQueue(inputs(tmp_path),FormalConfig(),tmp_path/'run',classification_enabled=True,
                      classification_python=str(tmp_path/'missing/python.exe'))
    step=queue.step();queue.stop();queue.close_classifier()
    assert step.success and step.row['classification']['status']=='failed'
    result=export_batch_pdf(queue.summary(),tmp_path/'partial.pdf')
    assert len(result['records'])==1 and result['records'][0]['classification_status']=='failed'
    text=pdf_text(tmp_path/'partial.pdf');assert '未尝试 1项' in text and '失败' in text


def test_home_formal_image_selection_runs_quality_before_inference(app,window,tmp_path):
    from dermademo.formal_device_ui import FormalMaskDialog
    from teaching_fixtures import prepared_case
    p=tmp_path/'bright.png';__import__('PIL.Image',fromlist=['Image']).fromarray(np.full((320,400,3),255,np.uint8)).save(p)
    window.commit_formal_result(__import__('dermademo.formal_results',fromlist=['replay_pair']).replay_pair())
    old=window.result;d=FormalMaskDialog(window);d.show()
    d.source_combo.setCurrentIndex(d.source_combo.findData('image'));d.prepare_selected_image(p)
    settled(app,d,lambda:d.prepared_image is not None)
    assert '高亮' in window.input_quality_banner.text() and 'bright.png' in window.input_quality_banner.text()
    assert window.result is old and d.outcome is None
    d.close();drain(app)


def test_report_dialog_exports_same_saved_record_without_ai(app,window,tmp_path,monkeypatch):
    folder=record(tmp_path);r=load_record(folder);window.commit_record(r)
    monkeypatch.setattr(ai_service,'post_once',lambda *_:pytest.fail('local report cannot call AI'))
    window.open_report();d=window.report_dialog;settled(app,d,lambda:d.folder is not None)
    d.generate.click();settled(app,d,lambda:d.pdf is not None)
    meta=json.loads(d.pdf.with_suffix('.pdf.json').read_text('utf-8'))
    assert meta['records'][0]['record_id']==r.document['record_id'] and meta['records'][0]['ai_status']=='not_run'
    assert d.open_pdf.isEnabled();d.close();drain(app)


def test_report_dialog_selected_relative_revision_can_save_and_regenerate(app,window,tmp_path):
    folder=record(tmp_path);raw=load_record(folder).result.mask
    saved=save_review(folder,raw,texts=dict(observations='',summary='本测试仅核对保存版本，不是临床审核。',advice=''))
    save_selection(folder,saved);window.commit_record(load_record(folder));window.open_report()
    d=window.report_dialog;settled(app,d,lambda:d.folder is not None)
    assert d.revision.currentData()==saved.relative_to(folder).as_posix()
    d.generate.click();settled(app,d,lambda:d.pdf is not None)
    assert valid_report(d.pdf)
    assert json.loads((folder/'report_selection.json').read_text('utf-8'))['review']==saved.relative_to(folder).as_posix()
    d.generate.click();settled(app,d,lambda:d.pdf is not None);assert valid_report(d.pdf)
    d.close();drain(app)


def test_unified_batch_rejects_other_frame_and_changed_dependencies(app,tmp_path,monkeypatch):
    from dermademo.analysis_snapshot import validate_context
    install_offline_wire(monkeypatch,tmp_path)
    q=FormalQueue(inputs(tmp_path),FormalConfig(),tmp_path/'run');first,second=q.step(),q.step()
    summary=deepcopy(q.summary());summary['attempts'][0]['record_directory']=second.row['record_directory']
    exported=export_batch_pdf(summary,tmp_path/'wrong-frame.pdf')
    assert len(exported['record_errors'])==1 and len(exported['records'])==1
    assert exported['records'][0]['identity']==second.row['identity']
    snapshot=build_snapshot(first.row['record_directory'])
    p=Path(first.row['record_directory'])/'input.rgb';p.write_bytes(bytes(len(p.read_bytes())))
    with pytest.raises(ValueError):validate_context(snapshot['folder'],snapshot['context'])


def test_cpu_configuration_dialog_checks_before_save_and_broadcasts(app,window,tmp_path):
    prefs=Preferences(QSettings(str(tmp_path/'cpu.ini'),QSettings.Format.IniFormat));window.preferences=prefs
    window.open_cpu_settings(str(PYTHON));d=window.cpu_settings_dialog
    assert not d.save.isEnabled();d.check.click();settled(app,d,lambda:d.checked is not None,timeout=60)
    received=[];window.cpu_runtime_changed.connect(received.append);d.save.click()
    assert received==[str(PYTHON)] and selected_python(prefs)==str(PYTHON)
    d.path.setText(str(tmp_path/'else/python.exe'));assert not d.save.isEnabled()
    assert selected_python(prefs)==str(PYTHON)
    d.close();drain(app)
