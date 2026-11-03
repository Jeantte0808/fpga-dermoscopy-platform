from copy import deepcopy
from pathlib import Path
from threading import Event
import json
import pytest
from PySide6.QtCore import QSize
from PySide6.QtPdf import QPdfDocument
from dermademo.batch_pdf import export_batch_pdf
from dermademo.formal_queue import FormalQueue
from dermademo.device.formal_session import FormalConfig
from dermademo.hardware import hardware_declaration
from test_gui import app, window
from test_formal_queue import inputs, install_offline_wire
from test_teaching_gui import settled


def pdf_text(app,path):
    doc=QPdfDocument();assert doc.load(str(path))==QPdfDocument.Error.None_
    text='\n'.join(doc.getAllText(i).text() for i in range(doc.pageCount()))
    return doc,text


def test_flash_declaration_evidence_and_old_labels():
    flash=hardware_declaration('p7-flash');old=hardware_declaration('p7')
    assert flash['bit_sha256']=='758e81d28d872e9f0fa8e0d9b988cb49fd938179e534e82df9351abb0a85ff8d'
    assert flash['mcs_sha256']=='678cb92577f370b73e62c354c2c4b57161c742f4e542a9cd4455bcafe8c22343'
    assert flash['resources']==old['resources'] and flash['graph_clock_hz']==100000000
    assert flash['P7_Flash_programmed'] and not old['P7_Flash_programmed']
    assert not flash['hardware_identity_verified'] and not flash['DDR_weights_persist_across_power_cycle']
    assert flash['cold_boot_elapsed_ms'] is None and not flash['sustained_5FPS_pass']


def test_pdf_matches_saved_frames_partial_and_history(app,tmp_path,monkeypatch):
    install_offline_wire(monkeypatch,tmp_path)
    q=FormalQueue(inputs(tmp_path),FormalConfig(),tmp_path/'run')
    step=q.step();q.stop()
    out=tmp_path/'report.pdf'
    result=export_batch_pdf(q.summary(),out,historical=True)
    doc,text=pdf_text(app,out)
    assert doc.pageCount()==2 and len(result['records'])==1
    assert '边界叠加' in text and '分析结论' in text and '技术与来源记录' not in text
    assert '历史' in text and '未处理' in text and '待审核' in text
    assert result['records'][0]['identity']==step.row['identity']
    tampered=deepcopy(q.summary());tampered['attempts'][0]['identity']['frame_id']+=1
    wrong=export_batch_pdf(tampered,tmp_path/'wrong.pdf')
    assert not wrong['records'] and len(wrong['record_errors'])==1
    original=out.read_bytes();cancel=Event();cancel.set()
    with pytest.raises(InterruptedError): export_batch_pdf(q.summary(),out,cancel=cancel)
    assert out.read_bytes()==original and not list(tmp_path.glob('.*.tmp'))


def test_failed_batch_has_report_and_no_previous_success_images(app,tmp_path):
    summary=dict(inputs=[dict(item_id='a',filename='failed.png'),dict(item_id='b',filename='waiting.png')],
                 attempts=[dict(item_id='a',filename='failed.png',status='failed',attempt=1,error='TIMEOUT',frame_id=4)],status='failed')
    out=tmp_path/'failed.pdf';result=export_batch_pdf(summary,out)
    doc,text=pdf_text(app,out)
    assert doc.pageCount()==1 and not result['records'] and '分析超时' in text and '图像2' in text and 'waiting.png' not in text


def test_queue_auto_pdf_and_export_failure_independent(app,window,tmp_path,monkeypatch):
    install_offline_wire(monkeypatch,tmp_path)
    window.open_formal_queue();d=window.queue_dialog;d.output_parent=tmp_path/'queue'
    from teaching_fixtures import prepared_case
    d.add_paths([prepared_case().path]);settled(app,d,lambda:len(d.inputs)==1)
    assert d.hardware_combo.isHidden() and d.hardware_combo.currentData()=='p7-flash'
    assert not window.ai_button.isEnabled()
    d.dwell_spin.setValue(0);d.start_queue()
    settled(app,d,lambda:d.pdf_path is not None)
    assert d.session.status=='completed' and d.pdf_view_button.isEnabled()
    assert d.session.summary()['success_count']==1
    monkeypatch.setattr('dermademo.batch_pdf.export_batch_pdf',lambda *_a,**_k:(_ for _ in ()).throw(OSError('disk full')))
    d.export_pdf();settled(app,d,lambda:'报告导出失败' in d.pdf_status.text())
    assert d.session.status=='completed' and d.session.rows[0]['local_record_complete']
    d.close()

def test_roi_pdf_preview_and_geometry(app,tmp_path,monkeypatch):
    from dermademo.resources import resource_path
    from dermademo.roi import decode_source
    from dermademo.roi_preprocessing import ConfirmedROIProcessor
    from dermademo.formal_queue import freeze_input
    root=resource_path('roi_root');row=json.loads((root/'samples.json').read_text('utf-8'))[1]
    prepared=ConfirmedROIProcessor().prepare(decode_source(root/row['directory']/row['input_file']),row['roi'])
    install_offline_wire(monkeypatch,tmp_path)
    item=freeze_input(prepared.path,tmp_path/'snapshot',prepared=prepared)
    q=FormalQueue([item],FormalConfig(hardware_profile='p7-flash'),tmp_path/'run');q.step()
    preview=Path('artifacts/flash-pdf-preview');preview.mkdir(parents=True,exist_ok=True)
    result=export_batch_pdf(q.summary(),preview/'ROI_batch_example.pdf',include_attachments=True)
    doc,text=pdf_text(app,preview/'ROI_batch_example.pdf')
    assert doc.pageCount()==3 and 'ROI框外未处理' in text and str(row['roi']) in text
    assert 'px' in text and '原图与分析范围' in text
    for i in range(doc.pageCount()):
        assert doc.render(i,QSize(850,1200)).save(str(preview/f'page-{i+1}.png'))
