"""One complete report for a selected frame or a frame-bound batch. No network."""
from datetime import datetime
from hashlib import sha256
import os
from pathlib import Path
from uuid import uuid4
import cv2
from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPageSize,QPainter,QPdfWriter,QFontMetricsF
from . import __version__
from .analysis_snapshot import build_snapshot,validate_context
from .analysis_identity import result_identity
from .batch_pdf import number,timestamp,edge_values
from .pdf_layout import ReportLayout
from .review import quality_message
from .device.formal_session import write_json


def label_status(status):
    return {'success':'已运行','not_run':'未运行','not_enabled':'未运行','failed':'失败','cancelled':'已取消',
            'unavailable':'不可用（校验未通过）'}.get(status,'未运行')


def valid_report(path):
    import json
    path=Path(path)
    try:
        meta=json.loads(path.with_suffix('.pdf.json').read_text('utf-8'))
        return meta.get('schema')=='derma.unified_report.v1' and meta['sha256']==sha256(path.read_bytes()).hexdigest()
    except (OSError,ValueError,TypeError,KeyError):return False


def figures(snapshot):
    record=snapshot['record'];identity=snapshot['context']['identity'];artifacts=None
    if identity['roi'] is not None:
        from .roi import roi_artifacts
        artifacts=roi_artifacts(record.request,record.result)
        original=artifacts['source_oriented.png'].copy();overlay=artifacts['mapped_overlay.png']
        x0,y0,x1,y1=identity['roi'];cv2.rectangle(original,(x0,y0),(x1-1,y1-1),(0,255,255),max(1,original.shape[1]//300))
    elif record.request.source_bytes:
        from .roi import decode_source,map_mask
        source=decode_source(record.request.source_path.name,record.request.source_bytes)
        mapped=map_mask(source,(0,0,source.rgb.shape[1],source.rgb.shape[0]),record.result.mask)
        original,overlay=mapped['source_oriented.png'],mapped['mapped_overlay.png']
    else:original,overlay=record.request.image_bgr,record.result.overlay_bgr
    return original,overlay,artifacts


def render_frame(layout,snapshot,generated,*,index,include_attachments=False):
    record=snapshot['record'];c=snapshot['context'];identity=c['identity'];roi=identity['roi']
    manual=c['manual'];raw=c['original_measurement'];q=c['quality'];classified=c['classification'];ai=snapshot['ai']
    revision=manual['document']['review_id'] if manual['status']=='success' else None
    layout.new_page(f'图像{index} / 图像与范围')
    if identity['source_kind']=='protocol_simulator':layout.clinical_text('结果来源','软件协议模拟，非实板推理；不作为实板验收结果。',size=8)
    layout.pairs([('记录保存时间',timestamp(record.document.get('saved_at'))),('报告生成时间',timestamp(generated)),
                  ('帧号',str(identity['frame_id'])),('所选复核版本',revision or '原始结果；人工复核未运行')])
    original,overlay,artifacts=figures(snapshot)
    has_source=bool(record.request.source_bytes)
    layout.images([('原图及ROI位置' if roi else '方向校正原图' if has_source else '实际256输入（原图未保存）',original,
                    '黄色框为推理选区。' if roi else '对应本图的原始视野。' if has_source else '本历史记录未保存高分辨率原图，不据此还原原始视野。',False),
                   ('轮廓叠加',overlay,'ROI框外未处理，暗区不等于背景。' if roi else '原始分割边界，供人工核对。',False)],height=135)
    layout.images([('实际模型输入',record.request.image_bgr,'实际发送的256×256 RGB，未作展示增强。',False),
                   ('原始Mask',record.result.mask,'保留原始256×256二值结果。',True)],height=125)
    layout.clinical_text('分析范围','矩形ROI '+str(roi)+'；框外未处理。' if roi else '整幅原图对应的模型输入。',size=8)
    touched=any(edge_values(record.result.mask))
    layout.clinical_text('触边提醒',('预测接触推理框边缘，可能截断病灶，请扩大范围或返回整图。' if touched else '未触及网格最外侧边缘；不据此保证病灶完整。')+
                         ' 此项依据原始Mask。',size=8)
    layout.clinical_text('输入提醒',quality_message(q),size=8)
    layout.new_page(f'图像{index} / 综合分析')
    layout.pairs([('帧号',str(identity['frame_id'])),('所选复核版本',revision or '原始结果')])
    revised=manual['document']['review_measurement'] if revision else None
    def value(m,key):
        if m is None:return '未运行'
        values={'area':(m['area_px2'],'px²',0),'feret':(m['max_feret_px'],'px',3),
                'perimeter':(m['perimeter']['value_px'],'px',3),'circularity':(m['circularity']['value'],'',3),
                'major':(m['axes'].get('major_length_px'),'px',3),'minor':(m['axes'].get('minor_length_px'),'px',3)}
        return number(*values[key])
    layout.table(('测量项目','原始FPGA结果','所选人工复核'),[(label,value(raw,key),value(revised,key)) for label,key in (
        ('前景面积','area'),('最大Feret径','feret'),('轮廓周长','perimeter'),('圆度','circularity'),('矩长轴','major'),('矩短轴','minor'))],(1,1,1))
    layout.clinical_text('计量说明','面积和径线均为256模型网格px/px²，未标定毫米；原图映射仅显示，矩轴不是Feret径。',size=8)
    wire=record.result.metadata.get('wire_report',{})
    layout.clinical_text('实际耗时','板内图 '+number(record.result.timings_ms.get('fpga_graph'),'ms')+'；图像E2E '+number(wire.get('image_E2E_ms'),'ms')+
                         ('；PC分类前向 '+number(classified['result']['timing'].get('forward_ms'),'ms') if classified['status']=='success' else '')+'。各范围分开计时。',size=8)
    scores=q['scores']
    layout.clinical_text('质量指标',f"清晰度分数 {scores['laplacian_variance']:.3f}；平均亮度 {scores['mean_luma']:.3f}；暗像素 {scores['dark_fraction']:.2%}；高亮像素 {scores['overexposed_fraction']:.2%}。",size=8)
    comparison=snapshot.get('reference',{});integer=comparison.get('int8',{});annotation=comparison.get('annotation',{})
    layout.clinical_text('整数一致性',(f"{integer['different_bytes']}字节差异；"+('逐字节一致。' if integer['byte_exact'] else '不一致。'))
        if integer.get('status')=='matched' else '不可用；'+integer.get('reason','无匹配参考。'),size=8)
    layout.clinical_text('人工标注对照',f"Dice {annotation['dice']:.6f}；IoU {annotation['iou']:.6f}。" if annotation.get('status')=='matched'
        else '不可用；'+annotation.get('reason','无匹配标注。'),size=8)
    layout.clinical_text('对照说明','以上仅比较原始Mask；整数参考验证实现，人工标注评价分割质量；所选人工修订未参与上述对照。',size=8)
    if any(v.get('provenance',{}).get('library')=='DEMO100_REFERENCE_IMPORT_20261031_V1' for v in (integer,annotation)):
        layout.clinical_text('参考范围','Demo100为既有验证集高分精选，不能代表总体精度或新增100图实板验收。',size=8)
    if classified['status']=='success':
        r=classified['result'];label='黑色素瘤' if r['predicted_class']=='melanoma' else '非黑色素瘤组（不等于良性）'
        layout.clinical_text('整图分类','教学候选V2：'+label+'；范围为原始整图，'+('与本ROI分割范围不同。' if roi else '使用独立预处理。'))
        layout.clinical_text('模型分数',f"非黑色素瘤组 {r['class_scores'][0]['score']:.6f}；黑色素瘤 {r['class_scores'][1]['score']:.6f}。不是患病概率。")
        layout.clinical_text('分类模型',r['model_version'],size=8)
    else:layout.clinical_text('整图分类',label_status(classified['status'])+'；分类状态不改变已保存分割结果。')
    if ai['status']=='success':
        from .ai_report_display import presentation_copy,result_summary,REPORT_NOTICE
        shown=presentation_copy(ai['answer'])
        if ai.get('legacy_context'):layout.clinical_text('AI范围','已有原始分割解读；当时未包含新增分类、质量或修订数据。',size=8)
        else:layout.clinical_text('AI范围','对应本图、帧及所选分析/修订版本。',size=8)
        for label,key in [('检查所见','image_observations'),('分割所见','segmentation_review'),('结果摘要',None),('审核建议','review_points')]:
            value=result_summary(shown) if key is None else shown[key]
            text='\n'.join(value) if isinstance(value,list) else str(value)
            if text:layout.clinical_text(label,text)
        layout.clinical_text('AI审核',REPORT_NOTICE,size=8)
    else:layout.clinical_text('AI解读',label_status(ai['status'])+'；'+ai.get('reason','基础报告可独立生成。'))
    if revision:
        d=manual['document']
        layout.clinical_text('人工复核',('Mask已修改'+str(d['changed_pixels'])+'个网格像素。' if d['changed_pixels'] else 'Mask未修改。')+
                             '保存时间 '+timestamp(d['saved_at'])+'；复核人：'+(d['operator'] or '未填写')+'。',size=8)
        for label,key in [('人工所见','observations'),('人工摘要','summary'),('人工建议','advice')]:layout.clinical_text(label,d['texts'][key] or '未填写')
        if d['note']:layout.clinical_text('修订说明',d['note'])
        if d['changed_pixels'] and snapshot['review'] is not None:
            from .review import measurement_overlay
            # Keep the signature with the comparison images rather than on an empty page.
            note_font=layout.font(8);note_width=(layout.width-12)/2-16
            note_height=max(len(layout.wrap(t,note_width,note_font)) for t in (
                '原始FPGA Mask保持不变。','仅为所选人工版本；不替换原始结果。'))*QFontMetricsF(note_font).lineSpacing()
            reserved=QFontMetricsF(layout.font()).lineSpacing()*3+16
            available=layout.bottom-layout.y-reserved-(35+note_height+8+4)-2
            if available<45:
                layout.new_page(layout.title);height=125
            else:height=min(100,available)
            layout.images([('原始测量叠加',measurement_overlay(record.request.image_bgr,record.result.mask,raw),'原始FPGA Mask保持不变。',False),
                           ('人工修订叠加',measurement_overlay(record.request.image_bgr,snapshot['review']['mask'],revised),'仅为所选人工版本；不替换原始结果。',False)],height=height)
    else:layout.clinical_text('人工复核',label_status(manual['status']))
    layout.reviewer()
    if include_attachments:
        layout.new_page(f'图像{index} / 补充图像')
        layout.images([('展示增强',record.result.enhanced_bgr,'仅为展示，不是模型输入。',False),
                       ('原始Mask',record.result.mask,'本帧原始二值结果。',True)],height=135)
        if artifacts:layout.images([('实际ROI裁剪',artifacts['roi_crop.png'],'原图中的选区。',False),
                                   ('实际模型输入',record.request.image_bgr,'与正式发送RGB一致。',False)],height=135)


def export_batch_pdf(summary,destination,*,historical=False,cancel=None,ai=None,include_attachments=False,single=False,selections=None):
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    temp=destination.with_name('.'+destination.name+'.'+uuid4().hex+'.tmp')
    writer=QPdfWriter(str(temp));writer.setResolution(72);writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4));writer.setPageMargins(QMarginsF(12,12,12,12))
    writer.setTitle('皮肤镜统一分析报告');writer.setCreator('DermaWorkbench '+__version__)
    painter=QPainter(writer)
    if not painter.isActive():raise OSError('报告绘制设备无法打开')
    layout=ReportLayout(writer,painter,historical=historical,cancel=cancel);rows=[];errors=[];checks=[]
    generated=datetime.now().astimezone().isoformat(timespec='seconds');attempts=summary.get('attempts',[])
    inputs=summary.get('inputs',[]);selections=selections or {}
    try:
        if ai is not None:
            success=[r for r in attempts if r['status']=='success']
            if len(success)!=1 or success[0].get('identity')!=ai['meta']['identity']:raise ValueError('AI与报告帧身份不一致')
        if not single and ai is None:
            layout.new_page('批次总览')
            layout.measurements([('批次开始时间',timestamp(summary.get('started_at'))),('报告生成时间',timestamp(generated)),
                ('程序版本',__version__),('图片 / 已尝试',f"{len(inputs)} / {len(attempts)}"),
                ('分割成功 / 失败',f"{sum(r['status']=='success' for r in attempts)} / {sum(r['status']=='failed' for r in attempts)}"),
                ('分类成功 / 失败',f"{sum(r.get('classification',{}).get('status')=='success' for r in attempts)} / {sum(r.get('classification',{}).get('status')=='failed' for r in attempts)}"),
                ('批次等待总计',number(summary.get('wall_ms'),'ms')),('PC分类总计',number(summary.get('classification_host_ms'),'ms'))])
            layout.table(('次序','图像标识','分割','整图分类','板内图 / 图像E2E'),[(str(r.get('attempt','?')),Path(r['filename']).stem,
                {'success':'成功','failed':'失败','cancelled':'取消'}.get(r['status'],'未运行'),
                label_status(r.get('classification',{}).get('status')),
                number(r.get('graph_ms'),'ms')+' / '+number(r.get('image_E2E_ms'),'ms')) for r in attempts],(.35,1.15,.55,.8,1.5))
            seen={r['item_id'] for r in attempts};remaining=sum(i['item_id'] not in seen for i in inputs)
            layout.clinical_text('批次状态',str(summary.get('status','未记录'))+'；未尝试 '+str(remaining)+'项。分类、AI及报告失败不替代分割状态。',size=8)
            pending=[Path(i['filename']).stem for i in inputs if i['item_id'] not in seen and i.get('filename')]
            if pending:layout.clinical_text('未运行图像','、'.join(pending),size=8)
            for r in attempts:
                if r.get('error') or r.get('classification',{}).get('error'):
                    layout.clinical_text('图像'+str(r.get('attempt','?'))+'状态',str(r.get('error') or '分割已保存；分类：'+r['classification']['error']),size=8)
        for index,row in enumerate(attempts,1):
            layout.check()
            if row['status']!='success':continue
            try:
                selected=selections.get(row.get('attempt'),selections.get(str(row.get('attempt')),'auto'))
                if ai is not None and ai['meta'].get('analysis_snapshot'):
                    selected=ai['meta']['analysis_snapshot']['manual'].get('selection')
                if ai is not None and ai.get('context') is not None:
                    from .analysis_snapshot import from_context
                    snap=from_context(row['record_directory'],ai['context'],ai=ai)
                else:snap=build_snapshot(row['record_directory'],review=selected,expected_classification=row.get('classification'),
                                        expected_reference=row.get('reference_comparison'),ai_override=ai,include_ai=ai is None)
                identity=snap['context']['identity']
                if identity!=row.get('identity') or snap['record'].request.source_path.name!=row['filename']:raise ValueError('报告记录与本项帧身份不符')
            except Exception as exc:
                if ai is not None:raise
                errors.append(dict(attempt=row.get('attempt'),error=str(exc)));layout.new_page('图像'+str(index)+' · 记录不可用')
                layout.clinical_text('状态','记录缺失或校验未通过，本项不引用图片和指标。');continue
            render_frame(layout,snap,generated,index=index,include_attachments=include_attachments)
            checks.append((snap['folder'],snap['context']))
            rows.append(dict(attempt=row.get('attempt'),record_id=snap['context']['record_id'],identity=identity,
                context_signature=snap['context']['signature'],review=snap['context']['manual'].get('selection'),
                classification_status=snap['context']['classification']['status'],ai_status=snap['ai']['status'],reference_comparison=snap.get('reference')))
        for folder,context in checks:validate_context(folder,context)
        if not layout.page:layout.new_page('尚无成功结果');layout.clinical_text('状态','未运行；没有可引用的成功记录。')
        layout.finish();painter.end();layout.check()
        if temp.stat().st_size<100:raise OSError('PDF未完整生成')
        os.replace(temp,destination)
    finally:
        if painter.isActive():painter.end()
        temp.unlink(missing_ok=True)
    result=dict(schema='derma.unified_report.v1',pdf=str(destination),sha256=sha256(destination.read_bytes()).hexdigest(),
        pages=layout.page,records=rows,record_errors=errors,historical=historical,application_version=__version__,
        report_layout='unified_v1',include_attachments=include_attachments,generated_at=generated)
    write_json(destination.with_suffix('.pdf.json'),result)
    return result


def export_single(folder,*,review='auto',cancel=None,ai=None,include_attachments=False,historical=True):
    if ai is not None and ai.get('context') is not None:
        from .analysis_snapshot import from_context
        snap=from_context(folder,ai['context'],ai=ai)
    else:snap=build_snapshot(folder,review=review,include_ai=False)
    record=snap['record'];context=snap['context'];identity=context['identity']
    row=dict(item_id=context['record_id'],attempt=1,status='success',identity=identity,filename=record.request.source_path.name,record_directory=str(folder))
    return export_batch_pdf(dict(status='single',inputs=[dict(item_id=row['item_id'])],attempts=[row]),
         Path(folder)/'reports'/context['signature']/'report.pdf',historical=historical,single=True,cancel=cancel,ai=ai,
         selections={1:review},include_attachments=include_attachments)
