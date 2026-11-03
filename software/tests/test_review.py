from copy import deepcopy
from pathlib import Path
import json
from threading import Event
import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import Qt,QPointF
from PySide6.QtTest import QTest
from PySide6.QtPdf import QPdfDocument
from dermademo import review
from dermademo.morphology import measure,evaluate_quality_scores,load_quality_config
from dermademo.ai_report_display import result_summary
from dermademo.records import load_record
from test_ai import record,app,window,response,KEY
from test_teaching_gui import settled
from test_rgb_device_gui import drain


@pytest.mark.parametrize('name',['normal','dark','blur','overexposed','near_over_above','near_over_below'])
def test_quality_member2_goldens_and_unchanged_bytes(name):
    root=Path('integrations/pc_algorithm_r1/samples/quality');raw=(root/(name+'.rgb888.bin')).read_bytes()
    expected=json.loads((root/(name+'.expected.json')).read_text('utf-8'))
    actual=review.quality_bytes(raw)
    assert actual['triggered']==expected['triggered']
    assert actual['scores']==pytest.approx(expected['scores'],abs=1e-9,rel=1e-12)
    assert (root/(name+'.rgb888.bin')).read_bytes()==raw
    assert actual['config_sha256']==review.digest(Path('dermademo/data/quality_v1.json'))


def test_quality_exact_comparison_boundaries():
    scores=dict(laplacian_variance=24.3,mean_luma=130.2,dark_fraction=.25,overexposed_fraction=.06)
    config=load_quality_config();a=evaluate_quality_scores(scores,config)
    assert a['triggered']==['overexposed']
    scores.update(laplacian_variance=24.2999,mean_luma=130.1999)
    assert evaluate_quality_scores(scores,config)['triggered']==['blur','dark','overexposed']


@pytest.mark.parametrize('name',['ISIC_0000091','ISIC_0000127'])
def test_measurements_match_member2_except_explicitly_disabled_mirror(name):
    root=Path('integrations/pc_algorithm_r1/samples')/name
    rgb=np.frombuffer((root/'input.rgb888.bin').read_bytes(),np.uint8).reshape(256,256,3)
    mask=np.array(Image.open(root/'prediction_original.png'))
    expected=json.loads((root/'expected_measurement.json').read_text('utf-8'))
    before=(rgb.tobytes(),mask.tobytes());actual=measure(rgb,mask,mask_source='raw_fpga')
    def equal(a,b):
        if isinstance(b,float):assert a==pytest.approx(b,abs=1e-10,rel=1e-12)
        elif isinstance(b,dict):
            assert set(a)==set(b)
            for key in b:equal(a[key],b[key])
        elif isinstance(b,list):
            assert len(a)==len(b)
            for x,y in zip(a,b):equal(x,y)
        else:assert a==b
    for key in ('area_px2','max_feret_px','max_feret_endpoints_xy','perimeter','circularity','axes','colors'):
        equal(actual[key],expected[key])
    assert actual['symmetry']['valid'] is False and 'pending' in actual['symmetry']['reason']
    assert before==(rgb.tobytes(),mask.tobytes())


def test_editor_stroke_undo_redo_restore_and_binary_grid():
    mask=np.zeros((256,256),np.uint8);editor=review.MaskEditor(mask)
    for x in range(4,24):editor.line((x,10),(x+1,11),radius=3,value=255)
    editor.end();assert len(editor.undo_stack)==1 and editor.changed_pixels>0
    stroke=editor.mask.copy();editor.undo();assert not editor.changed_pixels
    editor.redo();np.testing.assert_array_equal(editor.mask,stroke)
    editor.line((10,10),(10,10),radius=1,value=0);editor.end();editor.undo()
    assert editor.replace(editor.original) and not editor.changed_pixels
    assert not np.count_nonzero(mask) and set(np.unique(editor.mask))<={0,255}


def test_review_roundtrip_and_report_leave_base_files_unchanged(app,tmp_path):
    folder=record(tmp_path);before={p:p.read_bytes() for p in folder.iterdir() if p.is_file()}
    data=review.prepare(folder);mask=data['record'].result.mask.copy();mask[0,0]=255-mask[0,0]
    texts=dict(observations='人工测试说明，不是临床复核。',summary='核对边界后保存独立修订。',advice='对照原图继续审核。')
    saved=review.save_review(folder,mask,operator='离线验收',note='测试单像素修订',texts=texts)
    loaded=review.load_review(saved);manifest=loaded['document']
    assert manifest['changed_pixels']==1 and manifest['mask_variant'].startswith('manual_revision:')
    assert not manifest['clinical_review_verified'] and manifest['texts']==texts
    assert manifest['identity']==data['identity']
    from dermademo.review_pdf import export_review_pdf,valid_review_pdf
    pdf=export_review_pdf(saved);doc=QPdfDocument();assert doc.load(str(pdf))==QPdfDocument.Error.None_
    text=''.join(doc.getAllText(i).text() for i in range(doc.pageCount()))
    assert all(term in text for term in ('原始FPGA','人工修订','人工摘要','核对边界','256','未标定毫米'))
    assert not any(''.join(map(chr,[22806,26143,20154,20043,23478])) in s for s in [text])
    assert all(p.read_bytes()==raw for p,raw in before.items())
    assert valid_review_pdf(saved)
    doc.close();del doc;app.processEvents()
    pdf.write_bytes(pdf.read_bytes()+b'\nmodified')
    assert not valid_review_pdf(saved)


def test_unchanged_review_does_not_claim_manual_mask_revision(tmp_path):
    folder=record(tmp_path);mask=load_record(folder).result.mask
    saved=review.save_review(folder,mask)
    manifest=review.load_review(saved)['document']
    assert manifest['changed_pixels']==0 and manifest['mask_variant']=='raw_fpga'


@pytest.mark.parametrize('what',['mask','input','identity','measurements','incomplete'])
def test_review_rejects_tampered_or_incomplete_result(tmp_path,what):
    folder=record(tmp_path);saved=review.save_review(folder,load_record(folder).result.mask)
    if what=='mask':(saved/'review_mask.png').write_bytes(b'invalid')
    elif what=='input':(saved/'input.rgb').write_bytes(b'invalid')
    elif what=='incomplete':(saved/'INCOMPLETE.txt').write_text('failed')
    else:
        doc=json.loads((saved/'review.json').read_text('utf-8'))
        if what=='identity':doc['identity']['frame_id']+=1
        else:doc['review_measurement']['area_px2']+=1
        (saved/'review.json').write_text(json.dumps(doc),'utf-8')
    with pytest.raises((ValueError,OSError)):review.load_review(saved)


def test_cancel_and_local_save_failure_are_independent_of_base_record(tmp_path,monkeypatch):
    folder=record(tmp_path);base=(folder/'record.json').read_bytes();mask=load_record(folder).result.mask
    cancel=Event();cancel.set()
    with pytest.raises(InterruptedError):review.save_review(folder,mask,cancel=cancel)
    monkeypatch.setattr(review,'write_json',lambda *_:(_ for _ in ()).throw(OSError('simulated full disk')))
    with pytest.raises(OSError):review.save_review(folder,mask)
    incomplete=next((folder/'reviews').glob('review_*'))
    with pytest.raises(ValueError):review.load_review(incomplete)
    assert (folder/'record.json').read_bytes()==base


def test_orphaned_teaching_summary_is_not_presented_as_a_conclusion():
    answer={'teaching_summary':'此外，需明确区分未触边与病灶完整。'};before=deepcopy(answer)
    assert result_summary(answer)=='未形成有效结果摘要，待人工审核。'
    assert answer==before
    assert result_summary({'teaching_summary':'已获得当前输入的分割结果，边界需要人工核对。'}).startswith('已获得')


def test_gui_paint_undo_save_reopen_and_original_unchanged(app,window,tmp_path,monkeypatch):
    folder=record(tmp_path);window.commit_record(load_record(folder));original=window.result.mask.copy()
    window.review_button.click();dialog=window.review_dialog
    try:
        settled(app,dialog,lambda:dialog.editor is not None)
        dialog.mode.setCurrentIndex(1)
        y,x=np.argwhere(original==0)[100]
        point=dialog.canvas.mapFromScene(QPointF(float(x),float(y)))
        QTest.mouseClick(dialog.canvas.viewport(),Qt.MouseButton.LeftButton,pos=point)
        settled(app,dialog,lambda:dialog.editor.changed_pixels>0)
        np.testing.assert_array_equal(window.result.mask,original)
        dialog.undo_button.click();settled(app,dialog,lambda:dialog.editor.changed_pixels==0)
        dialog.redo_button.click();settled(app,dialog,lambda:dialog.editor.changed_pixels>0)
        dialog.texts['summary'].setPlainText('离线GUI修订验收，不是临床审核。')
        dialog.save_button.click();settled(app,dialog,lambda:dialog.saved is not None,timeout=60)
        assert (dialog.saved/'review.pdf').is_file()
        restored=review.load_review(dialog.saved)
        assert restored['document']['changed_pixels']>0 and restored['document']['texts']['summary'].startswith('离线GUI')
        import dermademo.review_ui as ui
        monkeypatch.setattr(ui.QFileDialog,'getExistingDirectory',lambda *_:str(dialog.saved))
        dialog.reopen_button.click();settled(app,dialog,lambda:'恢复' in dialog.status.text())
        np.testing.assert_array_equal(dialog.editor.mask,restored['mask'])
        np.testing.assert_array_equal(window.result.mask,original)
    finally:
        dialog.dirty=False;dialog.close();drain(app)


def test_gui_adopt_same_frame_ai_and_reject_wrong_frame_without_new_request(app,window,tmp_path,monkeypatch):
    from dermademo import ai_service,review_ui
    folder=record(tmp_path)
    result=ai_service.run_analysis(folder,'https://dashscope.aliyuncs.com',KEY,post=lambda *_:response())
    task=Path(result['task']);before={p:p.read_bytes() for p in task.glob('*.json')}
    monkeypatch.setattr(ai_service,'post_once',lambda *_:pytest.fail('adopting saved draft cannot call cloud'))
    monkeypatch.setattr(review_ui.QFileDialog,'getExistingDirectory',lambda *_:str(task))
    window.commit_record(load_record(folder));window.open_review();dialog=window.review_dialog
    try:
        settled(app,dialog,lambda:dialog.editor is not None)
        dialog.import_ai.click();settled(app,dialog,lambda:dialog.ai_draft is not None)
        assert dialog.texts['summary'].toPlainText()=='未形成有效结果摘要，待人工审核。'
        selected=dialog.ai_draft
        wrong=deepcopy(result['meta']);wrong['identity']['frame_id']+=1
        monkeypatch.setattr(ai_service,'load_analysis',lambda *_:(wrong,{}))
        dialog.import_ai.click();settled(app,dialog,lambda:'另一帧' in dialog.status.text())
        assert dialog.ai_draft==selected and all(p.read_bytes()==raw for p,raw in before.items())
    finally:
        dialog.dirty=False;dialog.close();drain(app)


def test_review_pdf_failure_keeps_saved_review_and_can_regenerate(app,window,tmp_path,monkeypatch):
    from dermademo import review_pdf
    folder=record(tmp_path);window.commit_record(load_record(folder));window.open_review();dialog=window.review_dialog
    exporter=review_pdf.export_review_pdf
    try:
        settled(app,dialog,lambda:dialog.editor is not None)
        monkeypatch.setattr(review_pdf,'export_review_pdf',lambda *_a,**_k:(_ for _ in ()).throw(OSError('simulated disk error')))
        dialog.save_button.click();settled(app,dialog,lambda:dialog.saved is not None)
        saved=dialog.saved
        assert '报告导出失败' in dialog.status.text() and review.load_review(saved)
        assert not dialog.pdf_button.isEnabled() and dialog.regenerate_button.isEnabled()
        monkeypatch.setattr(review_pdf,'export_review_pdf',exporter)
        dialog.regenerate_button.click();settled(app,dialog,lambda:dialog.pdf_button.isEnabled())
        assert dialog.saved==saved
    finally:
        dialog.dirty=False;dialog.close();drain(app)
