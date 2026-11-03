"""Table-based reports from validated saved frames; all images and values stay frame-bound."""
from datetime import datetime
from hashlib import sha256
import os
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPdfWriter,QPageSize,QPainter

from . import __version__
from .analysis_identity import result_identity
from .pdf_layout import ReportLayout
from .records import load_record


def number(value,unit='',digits=3):
    if not isinstance(value,(int,float)) or isinstance(value,bool):return '未记录'
    return (f'{value:.{digits}f}' if digits else str(int(value)))+(' '+unit if unit else '')


def edge_values(mask):
    import numpy as np
    return [int(np.count_nonzero(edge)) for edge in (mask[0],mask[-1],mask[:,0],mask[:,-1])]

def timestamp(value):
    if not value:return '未记录'
    try:
        moment=datetime.fromisoformat(value)
        if moment.tzinfo is not None:moment=moment.astimezone()
        return moment.strftime('%Y-%m-%d %H:%M:%S')
    except (TypeError,ValueError):return '未记录'


def export_batch_pdf(summary,destination,*,historical=False,cancel=None,ai=None,include_attachments=False,unified=False,**kwargs):
    if unified:
        from .unified_report import export_batch_pdf as export
        return export(summary,destination,historical=historical,cancel=cancel,ai=ai,include_attachments=include_attachments,**kwargs)
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    temp=destination.with_name('.'+destination.name+'.'+uuid4().hex+'.tmp')
    writer=QPdfWriter(str(temp));writer.setResolution(72)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4));writer.setPageMargins(QMarginsF(12,12,12,12))
    title='皮肤镜图像分析报告' if ai else '批量图像分析报告'
    writer.setTitle(title);writer.setCreator('DermaWorkbench '+__version__)
    painter=QPainter(writer)
    if not painter.isActive():raise OSError('PDF绘制设备无法打开')
    layout=ReportLayout(writer,painter,historical=historical,cancel=cancel)
    rows=[];errors=[];snapshots=[]
    attempts=summary.get('attempts',[]);inputs=summary.get('inputs',[])
    generated=datetime.now().astimezone().isoformat(timespec='seconds')
    try:
        successes=[row for row in attempts if row['status']=='success']
        if ai is not None and (len(successes)!=1 or successes[0].get('identity')!=ai['meta']['identity']):
            raise ValueError('AI与PDF帧身份不一致')

        def validated_rows():
            # Stream one decoded record at a time; never cache the whole batch or decode twice.
            for row in successes:
                layout.check()
                try:
                    directory=Path(row['record_directory']);manifest=directory/'record.json'
                    digest=sha256(manifest.read_bytes()).hexdigest()
                    record=load_record(directory)
                    identity=result_identity((record.request,record.result));expected=row.get('identity')
                    if expected is None or any(identity.get(k)!=expected.get(k) for k in (
                            'frame_id','generation','input_rgb_sha256','raw_mask_sha256','model_version','roi')):
                        raise ValueError('记录身份与队列帧不一致')
                    if record.document['source']['filename']!=row['filename']:raise ValueError('图片名与队列不符')
                except Exception as exc:
                    if ai is not None:raise ValueError('AI与PDF帧身份或源记录不一致') from exc
                    errors.append({'attempt':row.get('attempt'),'error':str(exc)})
                    layout.new_page('记录不可用 · '+row['filename'])
                    layout.table(('项目','说明'),[('状态','该图片记录缺失或校验未通过'),('处理方式','该项未引用图片或指标；其他有效项继续保留。')],(1,4))
                    continue
                if sha256(manifest.read_bytes()).hexdigest()!=digest:
                    raise ValueError('生成报告期间源记录发生变化；保留已有PDF')
                if ai is not None and identity!=ai['meta']['identity']:raise ValueError('AI与PDF帧身份不一致')
                snapshots.append((manifest,digest))
                rows.append({'attempt':row['attempt'],'record_id':record.document['record_id'],'identity':identity})
                yield row,record,identity

        # A single-image AI report starts with actual figures, rather than an empty batch cover.
        if ai is None:
            layout.new_page('批次总览')
            seen={r['item_id'] for r in attempts}
            state={'completed':'已完成','stopped':'已中断','failed':'失败','paused':'已暂停','running':'进行中',
                   'prepared':'待处理'}.get(summary.get('status'),'未记录')
            layout.pairs([
                ('报告时间',timestamp(generated)),('图片数量',str(len(inputs))),
                ('批次状态',state),('人工审核','待审核'),
                ('成功 / 失败',f"{sum(r['status']=='success' for r in attempts)} / {sum(r['status']=='failed' for r in attempts)}"),
                ('取消 / 未尝试',f"{sum(r['status']=='cancelled' for r in attempts)} / {sum(i['item_id'] not in seen for i in inputs)}")])
            layout.section('逐项状态')
            states={'success':'成功','failed':'失败','cancelled':'取消'}
            item_numbers={item['item_id']:index+1 for index,item in enumerate(inputs)}
            def outcome(row):
                if row['status']=='success':return '已生成分割结果，待审核'
                if row['status']=='cancelled':return '处理已取消'
                if 'TIMEOUT' in str(row.get('error','')).upper():return '分析超时，请重新处理'
                return '分析失败，请重新处理'
            state_rows=[(str(r.get('attempt','?')),'图像'+str(item_numbers.get(r['item_id'],'?')),states.get(r['status'],r['status']),
                         outcome(r)) for r in attempts]
            state_rows.extend(('—','图像'+str(item_numbers[i['item_id']]),'未处理 / 未尝试','尚无推理结果') for i in inputs if i['item_id'] not in seen)
            if not state_rows:state_rows=[('—','无图片','未处理','没有可引用的图像结果')]
            layout.table(('次序','图片','状态','说明'),state_rows,(.45,1.9,.85,1.6))


        for row,record,identity in validated_rows():
            import numpy as np
            from .classification import for_record,NOTICE as CLASS_NOTICE
            m=record.result.measurements
            classified=for_record(record)
            artifacts=None
            if identity['roi'] is not None:
                from .roi import roi_artifacts
                artifacts=roi_artifacts(record.request,record.result)
                original=artifacts['source_oriented.png'].copy()
                overlay=artifacts['mapped_overlay.png']
                import cv2
                x0,y0,x1,y1=identity['roi']
                cv2.rectangle(original,(x0,y0),(x1-1,y1-1),(0,255,255),max(1,original.shape[1]//300))
            elif record.request.source_bytes:
                from .roi import decode_source,map_mask
                source=decode_source(record.request.source_path.name,record.request.source_bytes)
                mapped=map_mask(source,(0,0,source.rgb.shape[1],source.rgb.shape[0]),record.result.mask)
                original,overlay=mapped['source_oriented.png'],mapped['mapped_overlay.png']
            else:original,overlay=record.request.image_bgr,record.result.overlay_bgr
            layout.new_page('图像分析 · '+row['filename'])
            layout.pairs([('记录保存时间',timestamp(record.document.get('saved_at'))),('报告生成时间',timestamp(generated))])
            layout.images([
                ('原图与分析范围' if identity['roi'] else '分析图像',original,'黄色框为选区。' if identity['roi'] else '本次分析图像。',False),
                ('边界叠加',overlay,'ROI框外未处理；暗区不等于背景。' if identity['roi'] else '预测边界供人工核对。',False)],height=130)
            values=[('预测前景面积',number(m.area_px2,'px²',0)),('最大Feret径',number(m.max_feret_px,'px')),
                    ('网格前景占比',number(m.area_px2/record.result.mask.size*100,'%',2)),
                    ('分析范围','矩形选区 '+str(identity['roi']) if identity['roi'] else '整幅图像')]
            layout.measurements(values)
            layout.clinical_text('计量说明','面积和径线为256×256像素网格测量，未标定毫米；原图映射仅用于显示。',size=8)
            if classified:
                label='黑色素瘤' if classified['predicted_class']=='melanoma' else '非黑色素瘤组（不等于良性）'
                layout.clinical_text('整图分类','教学候选V2：'+label)
                layout.clinical_text('模型分数',f"非黑色素瘤组 {classified['class_scores'][0]['score']:.4f} / 黑色素瘤 {classified['class_scores'][1]['score']:.4f}")
                layout.clinical_text('分类说明',CLASS_NOTICE,size=8)
            if ai is not None:
                from .ai_report_display import presentation_copy,result_summary,REPORT_NOTICE
                display=presentation_copy(ai['answer']) if ai['meta'].get('content_policy_version') else ai['answer']
                def readable(value):
                    text='\n'.join(value) if isinstance(value,list) else str(value)
                    return text.replace('FPGA原始二值Mask','分割图').replace('Mask','分割区域').replace('输入图','分析图像')
                for label,key in [('检查所见','image_observations'),('分割所见','segmentation_review'),('审核建议','review_points')]:
                    if label=='审核建议':layout.clinical_text('结果摘要',readable(result_summary(display)))
                    if display[key]:layout.clinical_text(label,readable(display[key]))
                if ai['meta'].get('content_policy_version'):layout.clinical_text('审核提示',REPORT_NOTICE,size=8)
            else:
                layout.clinical_text('检查所见','本次已获得预测分割区域。' if m.area_px2 else '本次未获得预测前景区域。')
                layout.clinical_text('分析结论','分割范围和边界待人工审核。')
                layout.clinical_text('审核建议','请对照原图核对分割边界，必要时调整选区后重新分析。')
            if identity['roi'] and any(edge_values(record.result.mask)):
                layout.clinical_text('选区提醒','预测接触推理框边缘，可能截断病灶，请扩大范围或返回整图。')
            layout.reviewer()
            if include_attachments:
                layout.new_page('附件 · '+row['filename'])
                layout.images([('增强显示',record.result.enhanced_bgr,'仅供观察，不改变模型输入。',False),
                               ('分割区域',record.result.mask,'原始256×256二值结果。',True)],height=135)
                if artifacts:
                    layout.images([('选区实际裁剪',artifacts['roi_crop.png'],'原图中的实际裁剪范围。',False),
                                   ('原始分析网格',record.request.image_bgr,'分割模型实际256×256输入。',False)],height=135)
                    layout.pairs([('ROI原图坐标',str(identity['roi'])),('原图尺寸',f'{original.shape[1]} × {original.shape[0]} px')])
                    layout.clinical_text('范围说明','ROI框外未处理。坐标左闭右开；原始Mask最近邻映射至选区，不将网格测量换算为原图或毫米。')
        if any(sha256(manifest.read_bytes()).hexdigest()!=digest for manifest,digest in snapshots):
            raise ValueError('生成报告期间源记录发生变化；保留已有PDF')
        layout.finish()
    except BaseException:
        painter.end()
        if temp.exists():temp.unlink()
        raise
    finally:
        if painter.isActive():painter.end()
    try:
        layout.check()
        if not temp.is_file() or temp.stat().st_size<100:raise OSError('PDF未完整写入')
        os.replace(temp,destination)
    finally:
        if temp.exists():temp.unlink()
    return dict(schema='alienhome.batch_pdf.v1',pdf=str(destination),sha256=sha256(destination.read_bytes()).hexdigest(),
                pages=layout.page,records=rows,record_errors=errors,historical=historical,application_version=__version__,
                report_layout='ruled_clinical_v1',include_attachments=include_attachments)
