"""Hardware experiment workbench: explicit live actions, read-only evidence."""
import json
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, QUrl, QSignalBlocker
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,QSpinBox,
    QCheckBox,QFileDialog,QTabWidget,QWidget,QMessageBox)
from .analysis_ui import AnalysisDialog
from .analysis_identity import frozen_pair, result_identity
from .validation_ui import table,append_row
from .validation import TIMING_ROWS
from .hardware_lab import evidence,compare_evidence,run,reopen,successful_frames,load_frame
from .formal_input import PreparedImage,prepare_image
from .device.formal_session import FormalConfig,write_json
from .hardware import DEPLOYMENT_PROFILE
from .runtime_paths import default_output
from .cpu_runtime import selected_python


class HardwareLabDialog(AnalysisDialog):
    def __init__(self,parent):
        super().__init__(parent)
        self.setWindowTitle('硬件对照实验台')
        self.resize(1080,800);self.setMinimumSize(760,560)
        self.inputs=[];self.snapshot=None;self.baseline=None;self.folder=None;self.document=None
        layout=QVBoxLayout(self)
        self.notice=QLabel('CPU FP32 / FPGA INT8；板内图含LUT、CNN、DDR等待。浏览不连接板卡；新建实测须确认。部署声明非自动识别。')
        self.notice.setObjectName('algorithmNotice');self.notice.setWordWrap(True);layout.addWidget(self.notice)
        row=QHBoxLayout()
        self.current_button=QPushButton('读取工作台成功结果')
        self.open_button=QPushButton('打开记录 / 实验')
        self.images_button=QPushButton('选择新实验JPEG/PNG')
        for b in (self.current_button,self.open_button,self.images_button):row.addWidget(b);b.setAutoDefault(False)
        self.current_button.clicked.connect(self.current);self.open_button.clicked.connect(self.choose_record);self.images_button.clicked.connect(self.choose_images)
        layout.addLayout(row)
        self.experiment_identity=QLabel('实验总体：尚未运行或打开实验。')
        self.experiment_identity.setWordWrap(True);self.experiment_identity.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.experiment_identity)
        frames=QHBoxLayout();frames.addWidget(QLabel('本实验成功FPGA帧'))
        self.frame_combo=QComboBox();self.frame_combo.setAccessibleName('选择本实验成功FPGA帧，含图片名、轮次、帧号和预热状态')
        self.frame_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.frame_combo.setMinimumContentsLength(20)
        self.frame_combo.setPlaceholderText('实验结束或打开已有实验后选择成功帧')
        self.frame_combo.currentIndexChanged.connect(self.select_frame);frames.addWidget(self.frame_combo,1)
        layout.addLayout(frames)
        self.identity=QLabel('尚未读取；任意ROI不能套用整图参考。');self.identity.setWordWrap(True)
        self.identity.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.identity)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        trial=QWidget();tl=QVBoxLayout(trial)
        tl.addWidget(QLabel('下次实验配置（查看历史不自动运行；历史参数见下方结果）'))
        settings=QHBoxLayout();self.mode=QComboBox()
        self.mode.addItem('同输入 CPU/FPGA 成对实验','paired');self.mode.addItem('仅FPGA连续序列（无分类/AI）','fpga_sequence')
        self.warmup=QSpinBox();self.warmup.setRange(0,20);self.warmup.setValue(5);self.warmup.setPrefix('预热轮数 ')
        self.repeats=QSpinBox();self.repeats.setRange(1,200);self.repeats.setValue(20);self.repeats.setPrefix('测量轮数 ')
        self.telemetry=QComboBox();self.telemetry.addItem('完整249页（逐层）','full');self.telemetry.addItem('常规3页（无逐层）','summary')
        for w in (self.mode,self.warmup,self.repeats,self.telemetry):settings.addWidget(w)
        tl.addLayout(settings)
        from .gui import ImageView
        previews=QHBoxLayout();self.views=[]
        for title in ('当前单帧模型实际输入','同图同轮CPU FP32 Mask（无则不可用）','当前单帧FPGA INT8原始Mask'):
            col=QVBoxLayout();col.addWidget(QLabel(title));view=ImageView('未运行',smooth=False)
            view.setMinimumHeight(140);col.addWidget(view);previews.addLayout(col);self.views.append(view)
        tl.addLayout(previews)
        self.reuse=QCheckBox('已确认本次上电模型可复用（首次上电不要选）');tl.addWidget(self.reuse)
        self.run_button=QPushButton('确认配置并开始实测');self.run_button.setProperty('fluentAppearance','primary')
        self.run_button.setAutoDefault(False);self.run_button.clicked.connect(self.confirm_run);tl.addWidget(self.run_button)
        self.trial_table,self.trial_model=table(trial,['尝试','后端','输入','预热','状态','帧号','共同RGB→Mask ms'])
        tl.addWidget(QLabel('实验总体统计 · 全部正式尝试，切换单帧不会改变'))
        self.summary_table,self.summary_model=table(trial,['正式测量','成功','失败','未运行','均值 ms','中位数 ms','P95 ms'])
        tl.addWidget(self.summary_table)
        tl.addWidget(self.trial_table,1);self.tabs.addTab(trial,'实验与原始尝试')
        perf=QWidget();pl=QVBoxLayout(perf)
        self.pin=QPushButton('将当前证据设为对比基线');self.pin.setAutoDefault(False);self.pin.clicked.connect(self.pin_baseline);pl.addWidget(self.pin)
        self.compare_label=QLabel('先读取一份证据设为基线，再读取候选；条件缺失时不算提速。')
        self.compare_label.setWordWrap(True);pl.addWidget(self.compare_label)
        self.timing_table,self.timing_model=table(perf,['当前单帧分项','数值 / 状态']);pl.addWidget(self.timing_table)
        self.stage_table,self.stage_model=table(perf,['阶段ID','cycles','阶段ms','总图占比','AR请求','R beats','AW请求','W beats'])
        pl.addWidget(self.stage_table,2)
        self.stage_notice=QLabel('逐层不可用；等待计数可能重叠，不能相加。');self.stage_notice.setWordWrap(True);pl.addWidget(self.stage_notice)
        self.tabs.addTab(perf,'瓶颈与优化证据')
        stats=QWidget();sl=QVBoxLayout(stats)
        self.stats_notice=QLabel('板内统计不可用：新接口尚未冻结。下表仅由原始Mask在PC独立计算，不发送新命令。')
        self.stats_notice.setWordWrap(True);sl.addWidget(self.stats_notice)
        self.stats_table,self.stats_model=table(stats,['PC参考字段','值']);sl.addWidget(self.stats_table)
        sl.addWidget(QLabel('单位：模型256网格 px/px²；包围框是全部前景，不是最大连通域。'))
        self.tabs.addTab(stats,'Mask统计参考（待RTL）')
        self.status=QLabel('未运行；没有实测能耗，不作功耗或持续5FPS结论。');self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.status)
        actions=QHBoxLayout()
        self.export_button=QPushButton('导出当前证据JSON');self.pdf_button=QPushButton('重新生成技术PDF')
        self.open_pdf=QPushButton('打开PDF');self.open_dir=QPushButton('打开实验目录');self.cancel_button=QPushButton('停止等待')
        for b in (self.export_button,self.pdf_button,self.open_pdf,self.open_dir,self.cancel_button):actions.addWidget(b);b.setAutoDefault(False)
        self.export_button.clicked.connect(self.export_evidence);self.pdf_button.clicked.connect(self.export_pdf)
        self.open_pdf.clicked.connect(lambda:self.open_path(self.folder/'experiment_report.pdf' if self.folder else None))
        self.open_dir.clicked.connect(lambda:self.open_path(self.folder))
        self.cancel_button.clicked.connect(self.cancel)
        layout.addLayout(actions);self.refresh()

    def refresh(self):
        idle=not self.busy and not self._closing
        for w in (self.current_button,self.open_button,self.images_button,self.mode,self.warmup,self.repeats,self.telemetry,self.reuse):w.setEnabled(idle)
        self.run_button.setEnabled(idle and bool(self.inputs));self.pin.setEnabled(idle and self.snapshot is not None)
        self.export_button.setEnabled(idle and self.snapshot is not None)
        self.pdf_button.setEnabled(idle and self.document is not None)
        self.open_dir.setEnabled(idle and self.folder is not None)
        self.open_pdf.setEnabled(idle and self.folder is not None and (self.folder/'experiment_report.pdf').is_file())
        self.frame_combo.setEnabled(idle and self.frame_combo.count()>0)
        self.cancel_button.setEnabled(self.busy and not self._thread.cancel.is_set())

    def current(self):
        parent=self.parent()
        if self.busy or parent.request is None or parent.result is None:return
        try:pair=frozen_pair((parent.request,parent.result));result_identity(pair)
        except Exception as exc:self.status.setText(str(exc));return
        self.start_job('evidence',lambda *_:self.inspect_pair(pair))

    @staticmethod
    def inspect_pair(pair):
        snap=evidence(pair);request,result=pair;context=result.metadata.get('input_context')
        prepared=None
        if context is not None and request.source_bytes is not None:
            prepared=PreparedImage(request.source_path,request.source_bytes,
                np.ascontiguousarray(request.image_bgr[:,:,::-1]).tobytes(),context,None)
        return snap,prepared,[request.image_bgr.copy(),None,result.mask.copy()]

    def choose_images(self):
        names,_=QFileDialog.getOpenFileNames(self,'选择冻结实验输入','','JPEG/PNG (*.jpg *.jpeg *.png)')
        if names:self.start_job('inputs',lambda *_:[prepare_image(Path(n)) for n in names])

    def choose_record(self):
        name=QFileDialog.getExistingDirectory(self,'打开四窗记录、采集或实验目录')
        if name:self.load_folder(Path(name))

    def load_folder(self,folder):
        def action(*_):
            if (folder/'experiment.json').exists():return 'experiment',folder,reopen(folder)
            from .records import load_record
            if (folder/'record.json').exists():
                r=load_record(folder);pair=r.request,r.result
            else:
                from .device.formal_capture import load_capture
                from .formal_results import processing_pair
                from .resources import resource_path
                pair=processing_pair(load_capture(folder,resource_path('formal_root')/'assets/manifest.json',require_profile=False),capture_directory=folder)
            snap,prepared,previews=self.inspect_pair(pair);snap['origin']='历史证据回放（本次未推理） · '+snap['origin']
            return 'evidence',snap,prepared,previews
        self.start_job('load',action)

    def show_evidence(self,snap):
        if snap.get('evidence_scope')!='selected_experiment_frame':
            blocker=QSignalBlocker(self.frame_combo);self.frame_combo.setCurrentIndex(-1);del blocker
        self.snapshot=snap;self.identity.setText(f"{'外部单帧（实验总体保留） · ' if self.document else '单帧证据 · '}{snap['origin']} · 帧 {snap['identity']['frame_id']}\n实际RGB {snap['identity']['input_rgb_sha256']} · ROI {snap['identity']['roi']}")
        self.timing_model.removeRows(0,self.timing_model.rowCount())
        labels={key:label for label,key in TIMING_ROWS}
        for k,v in snap['values'].items():append_row(self.timing_model,[labels.get(k,k),'不可用' if v is None else f'{v:.3f} ms'])
        self.stage_model.removeRows(0,self.stage_model.rowCount())
        hz=snap.get('stage_clock_hz');total=(snap.get('total_counters') or {}).get('cycles')
        for s in snap['stages']:
            append_row(self.stage_model,[s['stage'],s['cycles'],f"{s['cycles']*1000/hz:.3f}" if hz else '不可用',
                f"{s['cycles']/total*100:.2f}%" if total else '不可用',*[s.get(k,'不可用') for k in ('AR_bursts','R_beats','AW_bursts','W_beats')]])
        self.stage_notice.setText(f'当前单帧{len(snap["stages"])}个阶段，按真实cycles排序；阶段ID尚不推测算子名。等待计数非互斥，不相加。' if snap['stages'] else '逐层数据不可用；3页总计不能还原249页逐层。')
        self.stats_model.removeRows(0,self.stats_model.rowCount())
        for k,v in snap['pc_statistics'].items():append_row(self.stats_model,[k,json.dumps(v,ensure_ascii=False)])
        if self.baseline:
            comp=compare_evidence(self.baseline,snap)
            self.compare_label.setText(('条件匹配；单帧差值，仅供检查：'+json.dumps(comp['metrics'],ensure_ascii=False)) if comp['eligible']
                                      else '不能计算正式提速：'+', '.join(comp['reasons']))

    def clear_frame(self,message):
        self.snapshot=None;self.identity.setText(message)
        for model in (self.timing_model,self.stage_model,self.stats_model):model.removeRows(0,model.rowCount())
        for view in self.views:view.clear_image()
        self.stage_notice.setText('当前单帧逐层数据不可用；未沿用上一帧数值。')
        self.compare_label.setText('当前证据不可用；已设基线保留。' if self.baseline else '尚未设置基线，不自动生成提速结论。')

    def select_frame(self,*_):
        if self.busy or self.document is None or self.frame_combo.currentIndex()<0:return
        trial_id=self.frame_combo.currentData();folder,doc=self.folder,self.document
        self.clear_frame('正在只读加载所选FPGA帧；不推理、不调用AI。')
        self.start_job('frame',lambda *_:load_frame(folder,doc,trial_id))

    def show_frame(self,payload):
        snap=payload['snapshot'];self.show_evidence(snap)
        for i,row in enumerate(self.document['trials']):
            if row['trial_id']==snap['trial_id']:
                self.trial_table.selectRow(i);break
        for view,pixels in zip(self.views,payload['previews']):
            view.clear_image()
            if pixels is not None:view.set_image(pixels)
        self.identity.setText(f"当前实验单帧 · {snap['filename']} · 第{snap['repeat']}轮 · "
            f"{'预热（不参与总体正式统计）' if snap['warmup'] else '正式测量'} · 帧{snap['identity']['frame_id']}\n"
            f"{snap['origin']} · 实际RGB {snap['identity']['input_rgb_sha256']} · ROI {snap['identity']['roi']}")

    def pin_baseline(self):
        if self.snapshot:self.baseline=self.snapshot;self.compare_label.setText('基线已固定；读取另一份候选证据进行条件核对。')

    def confirm_run(self):
        if self.busy or not self.inputs:return
        python=selected_python(self.parent().preferences)
        if self.mode.currentData()=='paired' and not python:
            self.status.setText('请先在CPU Python配置中选择并检查本机解释器。');return
        answer=QMessageBox.question(self,'确认真实实验',f'将使用当前P7 Flash部署声明连接192.168.0.2:5000。\n'
            f'{len(self.inputs)}个输入，预热{self.warmup.value()}轮，正式{self.repeats.value()}轮。\n'
            '实验占用设备至结束；不调用AI。请确认板卡可用及首次模型加载设置。',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        if answer==QMessageBox.StandardButton.Yes:self.start_experiment(python=python)

    def start_experiment(self,*,python='',config=None,output=None,state_dir=None):
        if self.busy:return
        inputs=list(self.inputs);warmup=self.warmup.value();repeats=self.repeats.value();mode=self.mode.currentData()
        config=config or FormalConfig(reuse_model=self.reuse.isChecked(),telemetry_mode=self.telemetry.currentData(),hardware_profile=DEPLOYMENT_PROFILE)
        output=Path(output or default_output())
        def action(cancel,progress):
            folder,doc=run(inputs,config,output,python=python,warmup=warmup,repeats=repeats,mode=mode,cancel=cancel,progress=progress,state_dir=state_dir)
            try:
                from .hardware_lab_pdf import export
                export(folder,historical=False)
                pdf_error=None
            except Exception as exc:pdf_error=f'报告导出失败：{type(exc).__name__}: {exc}'
            return folder,doc,pdf_error
        self.start_job('run',action)

    def show_experiment(self,folder,doc):
        self.folder,self.document=folder,doc
        def value(v):return f'{v:.3f}' if v is not None else '不可用'
        self.summary_model.removeRows(0,self.summary_model.rowCount())
        for backend,label in (('cpu_fp32','CPU FP32'),('fpga_int8','FPGA INT8')):
            s=doc['summary'][backend];n=s['common_rgb_to_mask']
            append_row(self.summary_model,[label,s['success'],s['failed'],s['not_run'],
                *[value(n[k]) for k in ('mean_ms','median_ms','p95_ms')]])
        self.summary_table.setMaximumHeight(112)
        self.trial_model.removeRows(0,self.trial_model.rowCount())
        names={'success':'成功','failed':'失败','cancelled':'取消','not_run':'未运行','running':'运行中','completed':'完成'}
        for r in doc['trials']:append_row(self.trial_model,[r['trial_id'],r['backend'],r['input_index']+1,
            '是' if r['warmup'] else '否',names[r['status']],r.get('frame_id','—'),value(r.get('common_rgb_to_mask_ms'))])
        real=sum(r.get('origin')=='live_FPGA_session' and r['status']=='success' for r in doc['trials'])
        simulated=sum(r.get('origin')=='protocol_simulator' and r['status']=='success' for r in doc['trials'])
        self.status.setText(f"最近实验 {names.get(doc['status'],doc['status'])} · 实板成功{real} / 协议模拟{simulated}（含预热）\n{folder}")
        self.experiment_identity.setText(f"实验总体 {doc['experiment_id']} · {doc['created_at']} · 预热{doc['warmup']}轮 / 正式{doc['repeats']}轮 · "
                             f"{'含本机协议模拟，不作实板性能' if simulated else '来源以逐次记录为准'}；查看/重导不推理、不调用AI。")
        rows=successful_frames(doc)
        blocker=QSignalBlocker(self.frame_combo);self.frame_combo.clear()
        for row in rows:
            name=doc['inputs'][row['input_index']]['filename']
            self.frame_combo.addItem(f"{name} · 第{row['repeat']}轮 · 帧{row['frame_id']} · {'预热' if row['warmup'] else '正式'}",row['trial_id'])
        preferred=next((r for r in reversed(rows) if not r['warmup']),rows[-1] if rows else None)
        self.frame_combo.setCurrentIndex(self.frame_combo.findData(preferred['trial_id']) if preferred else -1)
        del blocker
        self.clear_frame('本实验没有成功FPGA帧；总体统计与报告仍可查看。' if preferred is None else '正在准备本实验单帧证据。')
        if preferred is not None:self.select_frame()

    def completed(self,kind,value,error):
        if error:
            if kind=='frame':self.clear_frame('所选帧证据不可用：'+error+'；实验总体与PDF入口保留。')
            self.status.setText('任务失败：'+error+'；未改动主工作台原始结果。');return
        if kind=='evidence':
            snap,prepared,previews=value;self.clear_frame('正在加载外部单帧证据。');self.show_evidence(snap)
            for view,pixels in zip(self.views,previews):
                if pixels is not None:view.set_image(pixels)
            self.inputs=[prepared] if prepared else []
        elif kind=='inputs':
            self.inputs=value;self.identity.setText(f'已准备{len(value)}个正式输入；尚未发包，结果和实测成绩未改变。')
        elif kind=='load':
            if value[0]=='experiment':self.inputs=[];self.show_experiment(value[1],value[2])
            else:
                self.clear_frame('正在加载外部单帧证据。');self.show_evidence(value[1]);self.inputs=[value[2]] if value[2] else []
                for view,pixels in zip(self.views,value[3]):
                    if pixels is not None:view.set_image(pixels)
        elif kind=='run':
            self.show_experiment(value[0],value[1])
            if value[2]:self.status.setText(self.status.text()+'\n'+value[2]+'；推理记录保留，不需复位。')
        elif kind=='frame':self.show_frame(value)
        elif kind=='pdf':self.status.setText('技术报告已生成：'+str(value)+'；未重复推理或调用AI。')
        elif kind=='export':self.status.setText('证据JSON已保存：'+str(value))

    def export_evidence(self):
        if not self.snapshot or self.busy:return
        name,_=QFileDialog.getSaveFileName(self,'保存当前证据','','JSON (*.json)')
        if name:
            snap=self.snapshot;baseline=self.baseline
            def action(*_):
                p=Path(name);write_json(p,dict(current=snap,baseline=baseline,
                    comparison=compare_evidence(baseline,snap) if baseline else None));return p
            self.start_job('export',action)

    def export_pdf(self):
        if self.folder and not self.busy:
            from .hardware_lab_pdf import export
            folder=self.folder;self.start_job('pdf',lambda *_:export(folder,historical=True))

    def open_path(self,path):
        if path and path.exists():QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def cancel(self):
        if self.busy:self._thread.cancel.set();self.status.setText('正在停止PC等待，保留已有结果；不自动复位板卡。');self.refresh()
