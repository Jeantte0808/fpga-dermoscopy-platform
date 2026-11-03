"""Image/data/conclusion/review report; no implementation appendix."""
from pathlib import Path
import json,os
from datetime import datetime
from uuid import uuid4
import numpy as np
from PIL import Image,ImageOps
from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPdfWriter,QPageSize,QPainter
from .classification import load,NOTICE
from .pdf_layout import ReportLayout

def export(folder,*,cancel=None):
    folder=Path(folder);result=load(folder);meta=json.loads((folder/'classification.json').read_text('utf-8'))
    target=folder/'classification.pdf';temp=folder/('report_'+uuid4().hex+'.tmp')
    writer=QPdfWriter(str(temp));writer.setResolution(72);writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4));writer.setPageMargins(QMarginsF(12,12,12,12))
    painter=QPainter(writer);layout=ReportLayout(writer,painter,historical=True,cancel=cancel)
    try:
        layout.new_page('整图分类分析 · '+meta['source_filename'])
        from .batch_pdf import timestamp
        layout.pairs([('记录保存时间',timestamp(meta.get('created_at'))),('报告生成时间',datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S'))])
        with Image.open(folder/meta['source_filename']) as image:original=np.asarray(ImageOps.exif_transpose(image).convert('RGB'))[:,:,::-1].copy()
        with Image.open(folder/'actual_224_input.png') as image:prepared=np.asarray(image)[:,:,::-1].copy()
        layout.images([('整张原图',original,'本次分类原图；不使用分割ROI。',False),('实际分类视野',prepared,'标准中心裁剪可能舍弃原图外围。',False)],height=115)
        render_classification(layout,result)
        layout.clinical_text('其他分析','分割与测量、质量、人工修订、AI：未运行。')
        layout.reviewer()
        load(folder);layout.finish()
    except BaseException:
        painter.end();temp.unlink(missing_ok=True);raise
    finally:
        if painter.isActive():painter.end()
    os.replace(temp,target);return target

def render_classification(layout,result):
    layout.section('整图分类结论 · 教学候选V2')
    label='黑色素瘤' if result['predicted_class']=='melanoma' else '非黑色素瘤组（不等于良性）'
    layout.table(('项目','结果'),[('预测类别',label),('分类范围','整张原图；与任意ROI分割独立')],(1,4))
    layout.table(('类别','模型分数（不是患病概率）'),[
        ('非黑色素瘤组',f"{result['class_scores'][0]['score']:.6f}"),('黑色素瘤',f"{result['class_scores'][1]['score']:.6f}")],(1,2))
    layout.table(('审核建议',),[(NOTICE,),('请结合原图人工审核，不能用该分类排除恶性或替代专业诊断。',)])
