import copy
from hashlib import sha256
import json
from pathlib import Path
import pytest
from PySide6.QtPdf import QPdfDocument
from dermademo import ai_service as service
from dermademo.ai_content_policy import prepare_draft,OMITTED,NOTICE
from dermademo.ai_report_display import presentation_copy,REPORT_NOTICE
from test_ai import app,window,record,response,ANSWER,KEY


def numeric_response():
    obj=json.loads(response());answer=dict(ANSWER,segmentation_review='最大径222.4像素。请人工检查边界。',
                                         limitations=['当前网格内未截断。','由于CLAHE导致分割精度受限。'])
    obj['choices'][0]['message']['content']=json.dumps(answer,ensure_ascii=False)
    return json.dumps(obj,ensure_ascii=False).encode()


def test_numeric_prose_is_draft_not_cloud_failure(app,tmp_path):
    calls=[]
    def post(_base,_key,payload):calls.append(json.loads(payload));return numeric_response()
    result=service.run_analysis(record(tmp_path),'https://dashscope.aliyuncs.com',KEY,post=post)
    meta=result['meta'];task=Path(result['task'])
    assert meta['cloud_complete'] and meta['transport_complete'] and meta['response_schema_valid'] and meta['pdf_complete']
    assert meta['status']=='draft_pending_review' and not meta['content_quality_verified'] and len(calls)==1
    assert calls[0]['max_tokens']==700
    answer=json.loads((task/'analysis.json').read_text('utf-8'))
    assert OMITTED in answer['segmentation_review'] and '222.4' not in answer['segmentation_review']
    audit=json.loads((task/'draft_audit.json').read_text('utf-8'))
    assert {e['reason'] for e in audit['draft_edits']}=={'numeric_prose','no_edge_does_not_prove_completeness','unverified_CLAHE_causality'}
    assert '222.4' in (task/'response.json').read_text('utf-8')
    before={p.name:p.read_bytes() for p in task.glob('*.json') if p.name!='analysis_report.json' and not p.name.endswith('.pdf.json')}
    assert service.regenerate_pdf(task)['meta']['pdf_complete']
    assert all((task/n).read_bytes()==b for n,b in before.items())
    doc=QPdfDocument();assert doc.load(str(task/'analysis.pdf'))==QPdfDocument.Error.None_
    text='\n'.join(doc.getAllText(i).text() for i in range(doc.pageCount()))
    assert OMITTED not in text and NOTICE not in text and text.count(REPORT_NOTICE)==1
    assert '222.4像素' not in text and '请人工检查边界' in text


def saved_task(tmp_path):
    source=record(tmp_path)
    result=service.run_analysis(source,'https://dashscope.aliyuncs.com',KEY,post=lambda *_:numeric_response())
    return source,Path(result['task'])


def test_offline_recovery_no_network_or_key_original_bytes_unchanged(app,tmp_path,monkeypatch):
    from dermademo.credentials import CredentialStore
    source,original=saved_task(tmp_path)
    def forbidden(*_a,**_k):pytest.fail('offline recovery must not read a Key or call cloud')
    monkeypatch.setattr(service,'post_once',forbidden);monkeypatch.setattr(CredentialStore,'read',forbidden)
    before={p:p.read_bytes() for p in original.rglob('*') if p.is_file()}
    result=service.recover_saved_response(original,source)
    meta=result['meta'];task=Path(result['task'])
    assert task!=original and meta['new_cloud_calls']==0 and meta['pdf_complete']
    assert not meta['request_started_local'] and meta['stage_events']==[]
    assert all(p.read_bytes()==data for p,data in before.items())
    assert (task/'facts.json').read_bytes()==(original/'facts.json').read_bytes()
    assert (task/'response.json').read_bytes()==(original/'response.json').read_bytes()
    service.load_analysis(task)


@pytest.mark.parametrize('mutation',['facts','identity','response_hash','finish','model','http'])
def test_recovery_rejects_wrong_source_and_incomplete_response(app,tmp_path,mutation):
    source,original=saved_task(tmp_path)
    meta=json.loads((original/'analysis_report.json').read_text('utf-8'))
    if mutation=='facts':
        facts=json.loads((original/'facts.json').read_text('utf-8'));facts['frame_id']+=1
        (original/'facts.json').write_text(json.dumps(facts),'utf-8')
    elif mutation=='identity':meta['identity']['frame_id']+=1
    elif mutation=='http':meta.update(transport_complete=False,http_response_received=False)
    elif mutation=='response_hash':meta['response_sha256']='0'*64
    else:
        obj=json.loads((original/'response.json').read_text('utf-8'))
        if mutation=='finish':obj['choices'][0]['finish_reason']='length'
        else:obj['model']='other-model'
        data=json.dumps(obj).encode();(original/'response.json').write_bytes(data);meta['response_sha256']=sha256(data).hexdigest()
    (original/'analysis_report.json').write_text(json.dumps(meta),'utf-8')
    tasks=set((source/'ai').iterdir())
    with pytest.raises(ValueError):service.recover_saved_response(original,source)
    assert set((source/'ai').iterdir())==tasks


@pytest.mark.parametrize('raw,status',[(b'not-json','response_invalid'),(b'{"choices":[{"finish_reason":"length"}]}','response_invalid')])
def test_malformed_response_is_distinct_from_transport_failure(app,tmp_path,raw,status):
    result=service.run_analysis(record(tmp_path),'https://dashscope.aliyuncs.com',KEY,post=lambda *_:raw)
    assert result['meta']['status']==status and result['meta']['transport_complete'] and not result['meta']['cloud_complete']


def test_presentation_copy_hides_empty_sections_does_not_mutate():
    raw=dict(ANSWER,limitations=[OMITTED,NOTICE],teaching_summary=OMITTED)
    before=copy.deepcopy(raw);shown=presentation_copy(raw)
    assert raw==before and shown['limitations']==[] and shown['teaching_summary']==''


def test_gui_offline_recovery_from_current_saved_frame(app,window,tmp_path,monkeypatch):
    from dermademo import ai_ui
    from dermademo.records import load_record
    from test_teaching_gui import settled
    from test_rgb_device_gui import drain
    source,original=saved_task(tmp_path)
    window.commit_record(load_record(source));window.open_ai();dialog=window.ai_dialog
    def forbidden(*_a,**_k):pytest.fail('offline button cannot invoke cloud or credential read')
    monkeypatch.setattr(service,'post_once',forbidden)
    monkeypatch.setattr(dialog.store,'read',forbidden)
    monkeypatch.setattr(ai_ui.QFileDialog,'getExistingDirectory',lambda *_:str(original))
    dialog.recover_button.click();settled(app,dialog,lambda:dialog.pdf_button.isEnabled())
    assert dialog.task!=original and service.load_analysis(dialog.task)[0]['new_cloud_calls']==0
    dialog.close();drain(app)
