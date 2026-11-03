from copy import deepcopy
from pathlib import Path
import re
from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPdfWriter,QPageSize,QPainter
from PySide6.QtPdf import QPdfDocument
import pytest
from dermademo.batch_pdf import export_batch_pdf
from dermademo.formal_queue import FormalQueue
from dermademo.device.formal_session import FormalConfig
from dermademo.pdf_layout import ReportLayout
from test_gui import app
from test_formal_queue import inputs,install_offline_wire
from test_ai import record,KEY,response


def read_pdf(path):
    doc=QPdfDocument();assert doc.load(str(path))==QPdfDocument.Error.None_
    return doc,[doc.getAllText(i).text() for i in range(doc.pageCount())]


def test_ai_report_starts_with_images_and_local_table_without_another_call(app,tmp_path,monkeypatch):
    from dermademo import ai_service as service
    calls=[]
    def post(*_):calls.append(1);return response()
    folder=record(tmp_path);result=service.run_analysis(folder,'https://dashscope.aliyuncs.com',KEY,post=post)
    task=Path(result['task']);original={p.name:p.read_bytes() for p in task.glob('*.json') if p.name!='analysis_report.json' and not p.name.endswith('.pdf.json')}
    monkeypatch.setattr(service,'post_once',lambda *_:pytest.fail('PDF layout must not call cloud'))
    regenerated=service.regenerate_pdf(task)
    assert regenerated['meta']['pdf_layout']=='unified_v1' and calls==[1]
    assert all((task/name).read_bytes()==data for name,data in original.items())
    doc,pages=read_pdf(task/'analysis.pdf')
    assert all(s in pages[0] for s in ('实际模型输入','原始Mask','轮廓叠加'))
    assert all(s in pages[1] for s in ('前景面积','最大Feret径','检查所见','结果摘要','审核建议','整图分类','人工复核'))
    assert '批次总览' not in pages[0]
    assert doc.pageCount()==2
    assert '增强显示' not in pages[0] and '分割区域' not in pages[0]
    assert '审核意见' in pages[1] and '审核人' in pages[1]
    assert re.search(r'报告生成时间：\s*\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}',pages[0])
    assert re.search(r'记录保存时间：\s*\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}',pages[0])
    assert not any(term in '\n'.join(pages) for term in ('SHA256','技术与来源','FCP1','LUT','bit SHA','分项计时','模型版本','提示词'))
    assert len(re.findall(rb'/Subtype\s*/Image\b',(task/'analysis.pdf').read_bytes()))>=2
    doc.close()  # Windows PDF reader holds the target open until explicitly closed.
    del doc
    app.processEvents()
    assert service.regenerate_pdf(task,include_attachments=True)['meta']['pdf_complete']
    attached,attached_pages=read_pdf(task/'analysis.pdf')
    assert attached.pageCount()==3 and '补充图像' in attached_pages[2]
    assert '展示增强' in attached_pages[2] and '原始Mask' in attached_pages[2]
    assert all((task/name).read_bytes()==data for name,data in original.items()) and calls==[1]


def test_two_frame_batch_and_tampered_frame_never_share_figures(app,tmp_path,monkeypatch):
    install_offline_wire(monkeypatch,tmp_path)
    queue=FormalQueue(inputs(tmp_path),FormalConfig(),tmp_path/'run');first=queue.step();second=queue.step()
    path=tmp_path/'batch.pdf';summary=queue.summary();exported=export_batch_pdf(summary,path,historical=True)
    doc,pages=read_pdf(path)
    assert len(exported['records'])==2 and all(r['identity'] in [first.row['identity'],second.row['identity']] for r in exported['records'])
    image_pages=[p for p in pages if '预测前景面积' in p]
    assert len(image_pages)==2
    assert all('.jpg' not in p.lower() and '.png' not in p.lower() and ''.join(map(chr,[22806,26143,20154,20043,23478])) not in p for p in pages)
    assert '逐项状态' in pages[0] and '历史' in pages[0]
    wrong=deepcopy(summary);wrong['attempts'][1]['identity']['raw_mask_sha256']='0'*64
    output=tmp_path/'invalid-second.pdf';invalid=export_batch_pdf(wrong,output)
    _,text=read_pdf(output)
    assert len(invalid['records'])==1 and len(invalid['record_errors'])==1
    assert sum('预测前景面积' in page for page in text)==1


def test_long_table_repeats_headers_and_keeps_tail_and_full_hash(app,tmp_path):
    path=tmp_path/'long.pdf';writer=QPdfWriter(str(path));writer.setResolution(72)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4));writer.setPageMargins(QMarginsF(12,12,12,12))
    painter=QPainter(writer);layout=ReportLayout(writer,painter,historical=True)
    layout.new_page('长表分页回归')
    long_text='开始标记'+('人工复核图像边界和毛发遮挡。'*150)+'结束标记'
    digest='0123456789abcdef'*4
    layout.table(('分析项目','完整内容'),[('长段落',long_text),('摘要值',digest),('末项','LAST_ROW_PRESENT')],(1,2))
    layout.finish();painter.end()
    doc,pages=read_pdf(path);text=''.join(pages)
    assert doc.pageCount()>=3 and all('分析项目' in page and '完整内容' in page for page in pages)
    assert all(f'第{i+1}页' in page for i,page in enumerate(pages))
    assert '开始标记' in text and '结束标记' in text and 'LAST_ROW_PRESENT' in text
    assert digest in ''.join(text.split())


def test_changed_source_during_export_keeps_previous_pdf(app,tmp_path,monkeypatch):
    import dermademo.batch_pdf as pdf
    install_offline_wire(monkeypatch,tmp_path)
    queue=FormalQueue(inputs(tmp_path,1),FormalConfig(),tmp_path/'run');queue.step()
    path=tmp_path/'previous.pdf';path.write_bytes(b'previous complete report')
    load=pdf.load_record;calls=[]
    def changing(folder):
        value=load(folder);calls.append(1)
        if len(calls)==1:
            manifest=Path(folder)/'record.json';manifest.write_bytes(manifest.read_bytes()+b'\n')
        return value
    monkeypatch.setattr(pdf,'load_record',changing)
    with pytest.raises(ValueError,match='发生变化'):export_batch_pdf(queue.summary(),path)
    assert path.read_bytes()==b'previous complete report' and not list(tmp_path.glob('.*.tmp'))


def test_domestic_sheet_blank_fields_and_monochrome_paper(app,tmp_path):
    from PySide6.QtCore import QSize
    from PIL import Image
    import numpy as np
    path=tmp_path/'sheet.pdf';writer=QPdfWriter(str(path));writer.setResolution(72)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4));writer.setPageMargins(QMarginsF(12,12,12,12))
    painter=QPainter(writer);layout=ReportLayout(writer,painter,historical=True)
    layout.new_page('图像分析 · 分类样例.jpg');layout.pairs([('报告日期','2026-10-31'),('审核状态','待审核')])
    layout.section('检查所见');layout.table(('项目','结果'),[('测量','100 px²')])
    layout.finish();painter.end();doc,pages=read_pdf(path)
    text=''.join(pages)
    assert all(label in text for label in ('皮肤镜图像分析报告单','姓名','性别','年龄','检查部位','历史记录'))
    assert 'DERMA IMAGE LAB' not in text and ''.join(map(chr,[22806,26143,20154,20043,23478])) not in text and '.jpg' not in text
    png=tmp_path/'sheet.png';assert doc.render(0,QSize(850,1200)).save(str(png))
    pixels=np.asarray(Image.open(png).convert('RGB')).astype(int)
    assert np.max(pixels.max(axis=2)-pixels.min(axis=2))<=1


def test_ruled_metrics_and_clinical_rows_have_visible_rules_and_keep_long_text(app,tmp_path):
    from PySide6.QtCore import QSize
    from PIL import Image
    import numpy as np
    path=tmp_path/'ruled.pdf';writer=QPdfWriter(str(path));writer.setResolution(72)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4));writer.setPageMargins(QMarginsF(12,12,12,12))
    painter=QPainter(writer);layout=ReportLayout(writer,painter,historical=True)
    layout.new_page('图像分析')
    layout.measurements([('预测前景面积','31836 px²'),('最大Feret径','222.407 px'),('分析范围','整幅图像')])
    layout.clinical_text('检查所见','MARK_OBSERVATION')
    layout.clinical_text('分析结论','MARK_CONCLUSION')
    layout.clinical_text('审核建议','MARK_ADVICE')
    layout.new_page('长文续表')
    layout.clinical_text('检查所见','HEAD_MARK '+('人工复核边界和毛发遮挡。'*180)+' TAIL_MARK')
    layout.reviewer();layout.finish();painter.end()
    doc,pages=read_pdf(path)
    assert all(value in pages[0] for value in ('31836','222.407','MARK_OBSERVATION','MARK_CONCLUSION','MARK_ADVICE'))
    assert 'HEAD_MARK' in ''.join(pages) and 'TAIL_MARK' in ''.join(pages)
    assert '检查所见（续）' in ''.join(''.join(pages[2:]).split()) and '审核签署' in pages[-1]
    png=tmp_path/'ruled.png';assert doc.render(0,QSize(1000,1415)).save(str(png))
    pixels=np.asarray(Image.open(png).convert('RGB'))
    dark=pixels[:,:,:3].max(axis=2)<150
    # Long horizontal strokes verify printed rules, not just labels in extracted text.
    ruled=np.flatnonzero(dark[:,65:935].mean(axis=1)>.9)
    groups=np.split(ruled,np.where(np.diff(ruled)>1)[0]+1)
    assert len([group for group in groups if len(group)])>=8
