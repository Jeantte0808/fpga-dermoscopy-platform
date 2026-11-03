from pathlib import Path
import json
from uuid import uuid4
import pytest
from PySide6.QtWidgets import QMessageBox
from PySide6.QtCore import QSize
from PySide6.QtPdf import QPdfDocument
from dermademo.credentials import CredentialStore
from dermademo.ai_service import run_analysis,regenerate_pdf,load_analysis,bundle
from dermademo.formal_results import replay_pair
from dermademo.storage import save_result
from test_gui import app,window
from test_teaching_gui import settled
from test_rgb_device_gui import drain

ANSWER=dict(image_observations=['可见深色区域。'],segmentation_review='需要人工复核边界。',review_points=['核对边缘。'],teaching_summary='仅用于分割教学。',limitations=['不能用于诊断。'])
KEY='TEST-SECRET-'+uuid4().hex

def record(tmp_path):
    path=save_result(tmp_path,*replay_pair())
    assert bundle(path)
    return path

def response(key=KEY):
    return json.dumps(dict(id='test',model='fixture',echo=key,choices=[dict(finish_reason='stop',message=dict(content=json.dumps(ANSWER,ensure_ascii=False)))]),ensure_ascii=False).encode()


def test_single_cloud_call_persistence_redaction_and_pdf_regeneration(app,tmp_path):
    folder=record(tmp_path);calls=[]
    def post(base,key,payload):
        calls.append(json.loads(payload));assert key==KEY
        return response()
    result=run_analysis(folder,'https://dashscope.aliyuncs.com',KEY,post=post,historical=True)
    assert len(calls)==1 and len(calls[0]['messages'][1]['content'])==3
    assert result['meta']['cloud_complete'] and result['meta']['pdf_complete']
    task=Path(result['task']);regenerate_pdf(task)
    assert len(calls)==1
    assert not any(KEY.encode() in p.read_bytes() for p in folder.rglob('*') if p.is_file())
    from dermademo.ai_content_policy import prepare_draft
    meta,answer=load_analysis(task);assert answer==prepare_draft(ANSWER)[0]
    doc=QPdfDocument();assert doc.load(str(task/'analysis.pdf'))==QPdfDocument.Error.None_
    text='\n'.join(doc.getAllText(i).text() for i in range(doc.pageCount()))
    assert '结果摘要' in text and '历史' in text and '待人工审核' in text
    preview=Path('artifacts/ai-pdf-preview');preview.mkdir(parents=True,exist_ok=True)
    (preview/'AI_offline_example.pdf').write_bytes((task/'analysis.pdf').read_bytes())
    for i in range(doc.pageCount()):assert doc.render(i,QSize(850,1200)).save(str(preview/f'page-{i+1}.png'))
    (task/'analysis.json').write_text('{}')
    with pytest.raises(ValueError):load_analysis(task)


def test_cloud_error_no_retry_no_key_and_pdf_failure_recovery(app,tmp_path,monkeypatch):
    folder=record(tmp_path);calls=[]
    def failed(*args):calls.append(1);raise RuntimeError(KEY)
    result=run_analysis(folder,'https://dashscope.aliyuncs.com',KEY,post=failed)
    assert len(calls)==1 and not result['meta']['cloud_complete']
    assert KEY not in json.dumps(result)
    import dermademo.batch_pdf as pdf
    original=pdf.export_batch_pdf
    monkeypatch.setattr(pdf,'export_batch_pdf',lambda *_a,**_k:(_ for _ in ()).throw(OSError(KEY)))
    paid=run_analysis(folder,'https://dashscope.aliyuncs.com',KEY,post=lambda *_:response())
    assert paid['meta']['cloud_complete'] and paid['meta']['status']=='pdf_failed'
    monkeypatch.setattr(pdf,'export_batch_pdf',original)
    assert regenerate_pdf(paid['task'])['meta']['pdf_complete']
    assert not any(KEY.encode() in p.read_bytes() for p in folder.rglob('*') if p.is_file())


def test_gui_cancel_credential_save_replace_delete_and_no_implicit_cloud(app,window,tmp_path,monkeypatch):
    from dermademo import ai_ui
    class Store:
        value=''
        def read(self):return self.value
        def save(self,key):self.value=key
        def delete(self):self.value=''
    store=Store();monkeypatch.setattr(ai_ui,'CredentialStore',lambda:store)
    window.commit_formal_result(replay_pair());window.open_ai();d=window.ai_dialog
    d.key.setText(KEY);d.remember.setChecked(True);d.save_key()
    assert store.value==KEY
    d.key.setText(KEY+'next');d.save_key();assert store.value==KEY+'next'
    d.delete_key();assert not store.value and not d.key.text()
    d.key.setText(KEY)
    monkeypatch.setattr(ai_ui.QMessageBox,'question',lambda *_:QMessageBox.StandardButton.No)
    monkeypatch.setattr(ai_ui,'run_analysis',lambda *_a,**_k:pytest.fail('cancel cannot call cloud'))
    d.analyze();settled(app,d,lambda:not d.busy)
    assert d.task is None
    d.close();drain(app)


def test_windows_credentials_roundtrip_unique_test_target():
    store=CredentialStore('AlienHomeDerma/TEST/'+uuid4().hex)
    try:
        assert store.read()==''
        store.save(KEY);assert store.read()==KEY
        store.save(KEY+'replace');assert store.read()==KEY+'replace'
        store.delete();assert store.read()==''
    finally:store.delete()

def test_invalid_host_and_incomplete_response_never_auto_retry(app,tmp_path):
    from dermademo.ai_protocol import validate_base
    for url in ('http://dashscope.aliyuncs.com','https://evil.example','https://dashscope.aliyuncs.com@evil.example'):
        with pytest.raises(ValueError):validate_base(url)
    folder=record(tmp_path);calls=[]
    def post(*_):calls.append(1);return b'{"choices":[{"finish_reason":"length"}]}'
    result=run_analysis(folder,'https://dashscope.aliyuncs.com',KEY,post=post)
    assert calls==[1] and not result['meta']['cloud_complete']


def test_simulator_ineligible_and_late_result_cannot_attach(app,window,tmp_path,monkeypatch):
    from dermademo import ai_ui
    pair=replay_pair();window.commit_formal_result(pair);window.open_ai();d=window.ai_dialog
    folder=record(tmp_path)
    result=run_analysis(folder,'https://dashscope.aliyuncs.com',KEY,post=lambda *_:response())
    # Another source/frame on the workbench must not receive these links.
    window.result.metadata['frame_id']+=1
    d.completed('cloud',result,None)
    assert 'ai_tasks' not in window.result.metadata
    d.close();drain(app)
    loaded=__import__('dermademo.records',fromlist=['load_record']).load_record(folder)
    loaded.result.metadata.update(source_kind='protocol_simulator')
    simulated=save_result(tmp_path,loaded.request,loaded.result)
    with pytest.raises(ValueError):bundle(simulated)


def test_confirm_one_call_reopen_report_and_local_regenerate(app,window,tmp_path,monkeypatch):
    from dermademo import ai_ui
    import dermademo.ai_service as service
    calls=[]
    def post(*_):calls.append(1);return response()
    monkeypatch.setattr(service,'post_once',post)
    monkeypatch.setattr(ai_ui,'default_output',lambda:tmp_path)
    monkeypatch.setattr(ai_ui.QMessageBox,'question',lambda *_:QMessageBox.StandardButton.Yes)
    window.commit_formal_result(replay_pair());window.open_ai();d=window.ai_dialog;d.key.setText(KEY)
    d.analyze();settled(app,d,lambda:d.pdf_button.isEnabled())
    assert calls==[1]
    task=d.task;d.regenerate();settled(app,d);assert calls==[1]
    snapshot=save_result(tmp_path,window.request,window.result)
    d.close();drain(app)
    window.commit_record(__import__('dermademo.records',fromlist=['load_record']).load_record(snapshot))
    window.open_ai();d=window.ai_dialog
    assert d.task==task and d.pdf_button.isEnabled() and calls==[1]
    assert '已有AI记录' in d.status.text()
    d.close();drain(app)


def test_startup_reads_credentials_and_dialog_autofills_without_request(app,monkeypatch):
    from dermademo.gui import MainWindow
    monkeypatch.setattr(CredentialStore,'read',lambda _:KEY)
    window=MainWindow()
    try:
        assert window.ai_session_key==KEY
        window.commit_formal_result(replay_pair());window.open_ai();d=window.ai_dialog
        assert d.key.text()==KEY and not d.busy and d.task is None
        assert '已加载' in d.credential_status.text()
        d.close();drain(app)
    finally:window.close();window.deleteLater();drain(app)


def test_ai_attaches_to_current_saved_record_without_reopening(app,window,tmp_path,monkeypatch):
    import dermademo.ai_service as service
    from dermademo import ai_ui
    monkeypatch.setattr(service,'post_once',lambda *_:response())
    monkeypatch.setattr(ai_ui.QMessageBox,'question',lambda *_:QMessageBox.StandardButton.Yes)
    window.commit_formal_result(replay_pair())
    saved=save_result(tmp_path,window.request,window.result);window.last_export=saved
    window.open_ai();d=window.ai_dialog
    assert d.record_folder==saved.resolve()
    d.key.setText(KEY);d.analyze();settled(app,d,lambda:d.pdf_button.isEnabled())
    assert (saved/'ai_index.json').is_file() and Path(load_analysis(d.task)[0]['source_record'])==saved.resolve()
    d.close();drain(app)
