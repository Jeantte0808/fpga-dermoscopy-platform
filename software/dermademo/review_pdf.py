"""PDF from an explicit saved review, independent of AI and device state."""
from datetime import datetime
import os
from pathlib import Path
from uuid import uuid4
from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPageSize,QPainter,QPdfWriter
from .batch_pdf import number,timestamp
from .pdf_layout import ReportLayout
from .review import load_review,measurement_overlay,quality_message,digest
from .device.formal_session import write_json


def valid_review_pdf(folder):
    import json
    folder=Path(folder)
    try:
        meta=json.loads((folder/'pdf_report.json').read_text('utf-8'))
        return meta['review_sha256']==digest(folder/'review.json') and meta['pdf_sha256']==digest(folder/'review.pdf')
    except (OSError,ValueError,KeyError,TypeError):return False


def export_review_pdf(folder,*,cancel=None):
    from .unified_report import export_single
    folder=Path(folder);saved=load_review(folder)
    exported=export_single(saved['record'].directory,review=folder,cancel=cancel)
    target=folder/'review.pdf';temp=folder/('.review_'+uuid4().hex+'.tmp')
    try:
        temp.write_bytes(Path(exported['pdf']).read_bytes())
        os.replace(temp,target)
        write_json(folder/'pdf_report.json',dict(review_sha256=digest(folder/'review.json'),pdf_sha256=digest(target),pages=exported['pages'],
                   generated_at=datetime.now().astimezone().isoformat(timespec='seconds'),context_signature=exported['records'][0]['context_signature']))
    finally:temp.unlink(missing_ok=True)
    return target


def _export_legacy_review_pdf(folder,*,cancel=None):
    saved=load_review(folder);data=saved['document'];record=saved['record'];folder=Path(folder)
    snapshot=digest(folder/'review.json');target=folder/'review.pdf';temp=folder/('.review_'+uuid4().hex+'.tmp')
    writer=QPdfWriter(str(temp));writer.setResolution(72);writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(12,12,12,12));writer.setTitle('皮肤镜图像人工复核报告')
    painter=QPainter(writer)
    if not painter.isActive():raise OSError('复核PDF绘制设备无法打开')
    layout=ReportLayout(writer,painter,historical=True,cancel=cancel)
    try:
        simulated=data['identity']['source_kind']=='protocol_simulator'
        original_label='协议模拟原始结果' if simulated else '原始FPGA'
        layout.new_page('人工复核记录 · 协议模拟（非实板）' if simulated else '人工复核记录')
        if simulated:layout.clinical_text('结果来源','软件协议模拟，非实板推理。',size=8)
        layout.pairs([('复核保存时间',timestamp(data['saved_at'])),('报告生成时间',datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S'))])
        raw=data['original_measurement'];revised=data['review_measurement']
        layout.images([
            (original_label+'分割与测量',measurement_overlay(record.request.image_bgr,record.result.mask,raw),'模型网格原始结果，保留不覆盖。',False),
            ('人工修订与测量' if data['changed_pixels'] else '复核结果（Mask未修改）',measurement_overlay(record.request.image_bgr,saved['mask'],revised),'黄线为最大Feret径；蓝/紫为矩轴。',False)],height=130)
        def value(m,key):
            if key=='area':return number(m['area_px2'],'px²',0)
            if key=='feret':return number(m['max_feret_px'],'px')
            if key=='perimeter':return number(m['perimeter']['value_px'],'px')
            if key=='circularity':return number(m['circularity']['value'])
            return number(m['axes'].get('axis_ratio'))
        layout.table(('指标',original_label,'本次复核'),[(label,value(raw,key),value(revised,key)) for label,key in (
            ('前景面积','area'),('最大Feret径','feret'),('离散轮廓周长','perimeter'),('圆度','circularity'),('矩轴比','ratio'))],(1.2,1,1))
        roi=data['identity']['roi']
        layout.clinical_text('分析范围','ROI '+str(roi)+'；框外未处理。' if roi else '整图输入对应的模型网格。',size=8)
        layout.clinical_text('计量说明','全部长度/面积为256模型网格px/px²，未标定毫米；矩轴不等于Feret径。',size=8)
        layout.clinical_text('输入提醒',quality_message(data['quality']),size=8)
        for label,key in [('人工所见','observations'),('人工摘要','summary'),('复核建议','advice')]:
            layout.clinical_text(label,data['texts'][key] or '未填写，待人工补充。')
        if data['note']:layout.clinical_text('修订说明',data['note'])
        layout.clinical_text('复核记录',('已修改'+str(data['changed_pixels'])+'个网格像素。' if data['changed_pixels'] else '原始Mask未修改。')+
                             '复核人：'+(data['operator'] or '未填写')+'；此处为用户保存内容。',size=8)
        layout.reviewer();load_review(folder)
        if digest(folder/'review.json')!=snapshot:raise ValueError('生成报告期间复核记录发生变化')
        layout.finish();painter.end();layout.check()
        if not temp.is_file() or temp.stat().st_size<100:raise OSError('复核PDF未完整生成')
        os.replace(temp,target)
        write_json(folder/'pdf_report.json',dict(review_sha256=snapshot,pdf_sha256=digest(target),pages=layout.page,
                   generated_at=datetime.now().astimezone().isoformat(timespec='seconds')))
    finally:
        if painter.isActive():painter.end()
        temp.unlink(missing_ok=True)
    return target
