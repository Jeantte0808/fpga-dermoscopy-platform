from pathlib import Path
import json,io
import pytest
import numpy as np
from PIL import Image
from dermademo.classification import ClassifierClient,run,load,attach,for_record,NOTICE
from dermademo.records import load_record
from test_gui import app,window

ROOT=Path('integrations/classifier_v2')
PYTHON=Path('.cache/cpu-venv/Scripts/python.exe').resolve()

def test_four_real_fixed_samples_and_resident_worker(tmp_path):
    client=ClassifierClient(PYTHON);pid=None
    try:
        for folder in sorted((ROOT/'samples').glob('0*')):
            sample=next(folder.glob('*.jpg'));expected=json.loads((folder/'expected_cpu_output.json').read_text('utf-8'))
            output=run(client,sample.read_bytes(),sample.name,tmp_path)
            actual=load(output)
            assert actual['identity']==expected['identity'] and actual['predicted_class']==expected['predicted_class']
            np.testing.assert_allclose([s['score'] for s in actual['class_scores']],[s['score'] for s in expected['class_scores']],atol=1e-5,rtol=1e-4)
            if pid is None:pid=client.process.pid
            assert client.process.pid==pid
            if folder.name.startswith('01'):assert actual['predicted_class']=='non_melanoma_group' # Retained false negative, not a success claim.
    finally:client.close()

def test_invalid_color_format_and_tampered_classification(tmp_path):
    client=ClassifierClient(PYTHON)
    try:
        buf=io.BytesIO();Image.new('RGBA',(50,50)).save(buf,format='PNG')
        with pytest.raises(RuntimeError):run(client,buf.getvalue(),'alpha.png',tmp_path)
        sample=next((ROOT/'samples').rglob('*.jpg'));output=run(client,sample.read_bytes(),sample.name,tmp_path)
        (output/'result.json').write_text('{}')
        with pytest.raises(ValueError):load(output)
    finally:client.close()

def test_classification_gui_whole_image_save_reopen_and_pdf(app,window,tmp_path,monkeypatch):
    from dermademo import classification_ui as ui
    from dermademo.classification_pdf import export
    from test_teaching_gui import settled
    from test_rgb_device_gui import drain
    monkeypatch.setattr(ui,'default_output',lambda:tmp_path)
    window.open_classification();dialog=window.classification_dialog
    sample=next((ROOT/'samples').rglob('*.jpg'))
    dialog.set_source(sample.read_bytes(),sample.name);dialog.python.setText(str(PYTHON));dialog.infer()
    settled(app,dialog,lambda:dialog.folder is not None,timeout=60)
    assert '不等于良性' in dialog.result.text() and '分数' in dialog.result.text()
    assert load(dialog.folder)['identity']['source_filename']==sample.name
    saved=dialog.folder
    dialog.set_source(b'old-source','different.png')
    monkeypatch.setattr(ui.QFileDialog,'getExistingDirectory',lambda *_:str(saved))
    dialog.open_saved()
    assert dialog.raw==sample.read_bytes() and dialog.name==sample.name and dialog.folder==saved
    path=export(dialog.folder)
    from PySide6.QtPdf import QPdfDocument
    doc=QPdfDocument();assert doc.load(str(path))==QPdfDocument.Error.None_
    text=''.join(doc.getAllText(i).text() for i in range(doc.pageCount()))
    assert doc.pageCount()==1
    assert '不是患病概率' in text and '审核' in text and 'SHA256' not in text
    assert ''.join(map(chr,[22806,26143,20154,20043,23478])) not in text and '.jpg' not in text.lower() and '.png' not in text.lower()
    assert b'/Image' in path.read_bytes()
    dialog.close();drain(app)

def test_attach_only_matching_original_and_report_reopen(app,tmp_path,monkeypatch):
    from test_formal_queue import install_offline_wire,inputs
    from dermademo.formal_queue import FormalQueue
    from dermademo.device.formal_session import FormalConfig
    from dermademo.batch_pdf import export_batch_pdf
    install_offline_wire(monkeypatch,tmp_path)
    queue=FormalQueue(inputs(tmp_path,1),FormalConfig(),tmp_path/'queue');step=queue.step()
    record=load_record(Path(step.row['record_directory']));original=(record.directory/'record.json').read_bytes()
    client=ClassifierClient(PYTHON)
    try:
        result=run(client,record.request.source_bytes,record.request.source_path.name,tmp_path)
        attach(record.directory,result);assert for_record(load_record(record.directory))
        assert (record.directory/'record.json').read_bytes()==original
        report=tmp_path/'combined.pdf';export_batch_pdf(queue.summary(),report)
        from PySide6.QtPdf import QPdfDocument
        doc=QPdfDocument();doc.load(str(report))
        text=''.join(doc.getAllText(i).text() for i in range(doc.pageCount()))
        assert all(term in text for term in ('整图分类','教学候选V2','不等于良性','不是患病概率'))
        sample=next((ROOT/'samples').rglob('*.jpg'));other=run(client,sample.read_bytes(),sample.name,tmp_path)
        with pytest.raises(ValueError):attach(record.directory,other)
    finally:client.close()
