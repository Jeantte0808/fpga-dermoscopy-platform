"""Qt-native technical experiment report, independent of clinical-style reports."""
from html import escape
from pathlib import Path
import os
from uuid import uuid4
from datetime import datetime
from PySide6.QtCore import QMarginsF, QUrl
from PySide6.QtGui import QFont, QPageSize, QPdfWriter, QTextDocument, QImage
from .hardware_lab import reopen


def export(folder, *, historical=True):
    folder=Path(folder);doc=reopen(folder)
    destination=folder/'experiment_report.pdf'
    temporary=folder/('report_'+uuid4().hex+'.pdf')
    def text(x):return escape(str(x if x is not None else '不可用'))
    def local_time(x):return datetime.fromisoformat(x).astimezone().isoformat(sep=' ',timespec='seconds') if x else None
    def table(headers,rows):
        return '<table width="100%" border="1" cellspacing="0" cellpadding="5"><thead><tr>'+''.join('<th>'+text(x)+'</th>' for x in headers)+'</tr></thead>'+''.join('<tr>'+''.join('<td>'+text(x)+'</td>' for x in row)+'</tr>' for row in rows)+'</table>'
    image_resources={}
    html=['<html><body><h1>FPGA硬件对照实验报告</h1>',
          '<p>本报告为工程实验记录。CPU为FP32，FPGA为INT8；结果一致性不代替人工标注精度。无功率数据，不作能效结论。</p>',
          table(['实验信息','记录'],[['实验编号',doc['experiment_id']],['开始时间',local_time(doc['created_at'])],
            ['结束时间',local_time(doc.get('ended_at'))],['来源','历史记录重新导出' if historical else '本次实验保存记录'],
            ['状态',doc['status']],['程序版本',doc['application_version']],['模式',doc['mode']],
            ['实验错误',doc.get('error') or '无'],
            ['FPGA采集来源',', '.join(sorted({r['origin'] for r in doc['trials'] if r['backend']=='fpga_int8'}))],
            ['统计模式',doc['config']['telemetry_mode']],['硬件来源','部署声明，非自动识别'],
            ['计时范围',doc['timing_scope']],['P95方法','最近秩；预热不参与正式统计']]),'<h2>正式测量汇总</h2>']
    rows=[]
    for name in ('cpu_fp32','fpga_int8'):
        s=doc['summary'][name];n=s['common_rgb_to_mask']
        rows.append([name,s['success'],s['failed'],s['cancelled'],s['not_run'],
                     *[f'{n[k]:.3f}' if n[k] is not None else '不可用' for k in ('mean_ms','median_ms','p95_ms')]])
    html.append(table(['后端','成功','失败','取消','未运行','均值ms','中位数ms','P95 ms'],rows))
    html.append('<p>以上为共同RGB至主机可用Mask边界；首次加载、统计、保存不计入该单项。板内图包含LUT、CNN与DDR等待。序列速率另包含必要采集保存，不能混为芯片速度。</p>')
    html.append('<p>序列实测帧率：'+text(doc['summary']['fpga_pipeline_frames_per_s'])+'；不宣称持续5FPS达标。</p>')
    for i,inp in enumerate(doc['inputs']):
        html.append('<h2 style="page-break-before:always">输入 '+str(i+1)+'：'+text(inp['filename'])+'</h2>')
        url=QUrl.fromLocalFile(str((folder/inp['folder']/'preview.png').resolve())).toString()
        html.append('<p>实际256模型输入；ROI：'+text(inp['roi'] if inp['roi'] is not None else '整图')+'。对应正式预处理声明和原始文件保存在输入目录。</p>')
        successful=[r for r in doc['trials'] if r['input_index']==i and r['status']=='success']
        input_cell='<td>模型实际输入<br/><img src="'+escape(url,quote=True)+'" width="160" height="160" /></td>'
        if successful:
            repeat=successful[-1]['repeat'];columns=[input_cell]
            for backend in ('cpu_fp32','fpga_int8'):
                row=next((r for r in reversed(successful) if r['backend']==backend and r['repeat']==repeat),None)
                if row is None or not row.get('mask_path'):
                    columns.append('<td>'+text(backend)+'：该轮未运行/未成功</td>');continue
                raw=(folder/row['mask_path']).read_bytes();key=f'mask://{i}/{backend}'
                image_resources[key]=QImage(raw,256,256,256,QImage.Format.Format_Grayscale8).copy()
                columns.append('<td>'+text(backend)+f' · 第{repeat}轮<br/><img src="{key}" width="160" height="160" /></td>')
            html.append('<table border="1" cellpadding="5" cellspacing="0"><tr>'+''.join(columns)+'</tr></table>')
            html.append('<p>以上只展示同输入、同一轮原始结果；预热状态及全部尝试见下表。CPU与FPGA差异不等于标注误差。</p>')
        else:html.append('<table border="1" cellpadding="5"><tr>'+input_cell+'<td>本输入尚无成功结果，不填示例Mask。</td></tr></table>')
    html.append('<h2>全部尝试（含预热、失败和未运行）</h2>')
    html.append(table(['序号','后端','输入','预热','状态','帧号','共同边界ms','错误'],[
        [r['trial_id'],r['backend'],r['input_index']+1,r['warmup'],r['status'],r.get('frame_id'),
         f"{r['common_rgb_to_mask_ms']:.3f}" if r.get('common_rgb_to_mask_ms') is not None else None,r.get('error','')]
        for r in doc['trials']]))
    html.append('<p>输入、原始Mask、参考对照及分项计数见同目录原始记录。模拟器结果不构成实板性能；缺少标注的输入不填写精度成绩。本报告不调用云端AI。</p></body></html>')
    try:
        writer=QPdfWriter(str(temporary));writer.setResolution(96)
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4));writer.setPageMargins(QMarginsF(12,12,12,12))
        document=QTextDocument();document.setDefaultFont(QFont('Microsoft YaHei',9))
        for key,img in image_resources.items():document.addResource(QTextDocument.ResourceType.ImageResource,QUrl(key),img)
        document.setHtml(''.join(html));document.print_(writer)
        del writer
        if not temporary.is_file() or temporary.stat().st_size<100 or not temporary.read_bytes().startswith(b'%PDF-'):
            raise OSError('报告导出失败：未生成有效PDF')
        os.replace(temporary,destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
