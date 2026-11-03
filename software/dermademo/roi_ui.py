"""ROI workspace. Draft selection stays separate from the last successful frame."""
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np
from PySide6.QtCore import Qt, Slot, QSignalBlocker, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
                               QCheckBox, QSpinBox, QFileDialog, QScrollArea, QWidget, QLineEdit, QComboBox)

from .gui import ImageView
from .roi_selector import ROISelector
from .roi import (decode_source, crop_rgb, validate_rect, get_roi_processor, validate_roi_context,
                  map_mask, save_draft, load_draft)
from .formal_input import prepare_image, verify_preprocessing, validate_source, EDUCATION_NOTICE
from .formal_results import live_pair
from .device.formal_session import run_formal, FormalConfig
from .rgb_device_ui import RGBWorker
from .runtime_paths import default_output
from .storage import save_result
from .records import load_record
from ._fluent.style import set_fluent_property
from .hardware import PROFILE_CHOICES, DEPLOYMENT_PROFILE


class ROIDialog(QDialog):
    def __init__(self, parent, *, processor=None):
        super().__init__(parent)
        self.setWindowTitle('矩形ROI工作区')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumWidth(780)
        self.processor = processor if processor is not None else get_roi_processor()
        self.source = self.whole_input = self.prepared = self.last_pair = None
        self.last_export = self.last_draft = None
        self._thread = self._completion = self._kind = None
        self._closing = self._pending_crop = self._invalid = False
        self._revision = 0
        self._ancestors = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        self.rule_label = QLabel('ROI 预处理不可用；可框选、预览裁剪和保存草稿，暂不能发送推理。'
                                if self.processor is None else '框选分析区域，每边至少 16 像素；检查模型输入后按 Enter 分割。')
        self.rule_label.setObjectName('algorithmNotice'); self.rule_label.setWordWrap(True)
        outer.addWidget(self.rule_label)
        commands = QHBoxLayout()
        self.image_button = QPushButton('打开原图')
        self.open_button = QPushButton('打开记录/草稿')
        self.clear_button = QPushButton('清除ROI · 返回整图')
        self.selection_checkbox = QCheckBox('左键框选/调整')
        self.selection_checkbox.setChecked(True)
        self.fit_button = QPushButton('适应窗口')
        for w in (self.image_button,self.open_button,self.clear_button,self.selection_checkbox,self.fit_button): commands.addWidget(w)
        outer.addLayout(commands)
        coords = QHBoxLayout()
        coords.addWidget(QLabel('原图半开区间 [x0, y0, x1, y1)'))
        self.spins = []
        for name in ('x0','y0','x1','y1'):
            spin = QSpinBox(); spin.setRange(0,24000000); spin.setPrefix(name+'：')
            spin.setAccessibleName('方向校正原图ROI坐标 '+name)
            spin.valueChanged.connect(self._coordinate_edited)
            coords.addWidget(spin); self.spins.append(spin)
        outer.addLayout(coords)
        self.selection_label = QLabel('尚未载入原图')
        self.selection_label.setWordWrap(True); self.selection_label.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(self.selection_label)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget(); grid = QGridLayout(body)
        scroll.setWidget(body); outer.addWidget(scroll,1)
        self.selector = ROISelector()
        self.overlay = ImageView('成功后显示映射叠加；ROI外阴影表示未处理')
        self.original_label = QLabel('方向校正原图 · 草稿框选，可拖动框内及8个边/角调整')
        self.overlay_label = QLabel('上次成功结果 · 尚无；阴影区域未处理，不等于背景')
        self.original_label.setWordWrap(True); self.overlay_label.setWordWrap(True)
        self.original_label.setTextFormat(Qt.TextFormat.PlainText); self.overlay_label.setTextFormat(Qt.TextFormat.PlainText)
        grid.addWidget(self.original_label,0,0,1,2); grid.addWidget(self.overlay_label,0,2,1,2)
        self.selector.setMinimumHeight(220); self.overlay.setMinimumHeight(220)
        grid.addWidget(self.selector,1,0,1,2); grid.addWidget(self.overlay,1,2,1,2)
        thumbs = QHBoxLayout()
        self.crop_view, self.input_view, self.mask_view = [ImageView(text, smooth=i!=2) for i,text in enumerate(
            ('框选后显示实际裁剪区域','准备后显示模型输入','分割后显示原始结果'))]
        for title, view in zip(('实际裁剪区域','待发送模型输入 · 256×256','成功帧原始Mask · 256×256'),
                               (self.crop_view,self.input_view,self.mask_view)):
            column=QVBoxLayout(); label=QLabel(title); label.setWordWrap(True); column.addWidget(label)
            if view is self.input_view:self.input_label=label
            view.setMinimumHeight(120); view.setMaximumHeight(160); column.addWidget(view); thumbs.addLayout(column,1)
        grid.addLayout(thumbs,2,0,1,4)
        options=QHBoxLayout()
        self.reuse_checkbox=QCheckBox('复用已验证模型（首次上电取消）')
        self.telemetry_combo=QComboBox()
        self.telemetry_combo.addItem('常规演示 · 3页总计', 'summary')
        self.telemetry_combo.addItem('完整诊断 · 249页逐层', 'full')
        self.telemetry_combo.setAccessibleName('板内统计采集模式')
        self.telemetry_combo.setToolTip('常规演示逐层统计不可用；统计查询不计入图像E2E，不代表CNN提速。')
        options.addWidget(self.telemetry_combo)
        self.seed_edit=QLineEdit(); self.seed_edit.setPlaceholderText('可选：旧客户端最新高水位文件路径')
        self.seed_edit.setAccessibleName('最新事务高水位迁移路径')
        options.addWidget(self.reuse_checkbox); options.addWidget(self.seed_edit,1)
        outer.addLayout(options)
        hardware_row=QHBoxLayout()
        # Deployment declaration is recorded without a daily selection control.
        self.hardware_combo=QComboBox()
        for label,key in PROFILE_CHOICES:self.hardware_combo.addItem(label,key)
        self.hardware_combo.setAccessibleName('用户确认实际烧录的硬件版本，非自动识别')
        self.hardware_combo.setToolTip('部署配置来自用户确认；非自动识别；回放保留旧标签。')
        self.hardware_combo.setCurrentIndex(self.hardware_combo.findData(DEPLOYMENT_PROFILE))
        self.hardware_combo.hide()
        outer.addLayout(hardware_row)
        self.result_label=QLabel('尚无成功帧；测量仅使用模型256网格 px/px²，不作原图或毫米测量。')
        self.result_label.setWordWrap(True); self.result_label.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(self.result_label)
        self.edge_label=QLabel('触边提醒：尚无成功ROI结果，未进行检查。')
        self.edge_label.setObjectName('roiEdgeReminder');self.edge_label.setWordWrap(True)
        self.edge_label.setTextFormat(Qt.TextFormat.PlainText)
        self.edge_label.setAccessibleName('最近成功ROI帧的预测触边提醒')
        self.edge_label.setProperty('fluentRole','messageBar')
        outer.addWidget(self.edge_label)
        self.chain_label=QLabel('历史父子记录对照')
        self.chain_label.setWordWrap(True);self.chain_label.setTextFormat(Qt.TextFormat.PlainText);outer.addWidget(self.chain_label)
        self.chain_label.hide()
        self.previous_group=QWidget();history_layout=QVBoxLayout(self.previous_group)
        history_layout.addWidget(QLabel('历史父帧叠加 · 与右上结果在同一原图坐标显示；阴影为未处理'))
        self.previous_overlay=ImageView('打开旧版父子记录后显示历史对照')
        self.previous_overlay.setMinimumHeight(160)
        history_layout.addWidget(self.previous_overlay);grid.addWidget(self.previous_group,3,0,1,4)
        self.previous_group.hide()
        self.status=QLabel('先打开原图；框选/预览不会发包。Enter推理，Ctrl+S保存最近成功结果。')
        self.status.setWordWrap(True); self.status.setTextFormat(Qt.TextFormat.PlainText); self.status.setObjectName('statusMessage')
        outer.addWidget(self.status)
        actions=QHBoxLayout()
        self.prepare_button=QPushButton('准备实际模型输入')
        self.run_button=QPushButton('开始FPGA推理'); self.run_button.setProperty('fluentAppearance','primary')
        self.draft_button=QPushButton('保存ROI草稿')
        self.save_button=QPushButton('保存成功结果')
        self.cancel_button=QPushButton('停止等待')
        self.close_button=QPushButton('关闭')
        for w in (self.prepare_button,self.run_button,self.draft_button,self.save_button,self.cancel_button,self.close_button):
            w.setAutoDefault(False); actions.addWidget(w)
        outer.addLayout(actions)
        footer=QLabel(EDUCATION_NOTICE); footer.setObjectName('muted'); footer.setWordWrap(True); outer.addWidget(footer)
        self.image_button.clicked.connect(self.choose_image); self.open_button.clicked.connect(self.choose_open)
        self.clear_button.clicked.connect(self.clear_roi)
        self.selection_checkbox.toggled.connect(self._selection_mode)
        self.fit_button.clicked.connect(lambda: [v.fit_image() for v in (self.selector,self.overlay,self.crop_view,self.input_view,self.mask_view)])
        self.selector.roi_changed.connect(self._roi_changed)
        self.selector.selection_committed.connect(self.preview_crop)
        self.prepare_button.clicked.connect(self.prepare_input)
        self.run_button.clicked.connect(self.start)
        self.draft_button.clicked.connect(self.choose_draft); self.save_button.clicked.connect(self.choose_save)
        self.cancel_button.clicked.connect(self.cancel); self.close_button.clicked.connect(self.close)
        self._timer=QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(150); self._timer.timeout.connect(self.preview_crop)
        for key,fn in ((Qt.Key.Key_Return,self.run_button.click),(Qt.Key.Key_Enter,self.run_button.click),('Ctrl+S',self.save_button.click)):
            shortcut=QShortcut(QKeySequence(key),self); shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.setAutoRepeat(False); shortcut.activated.connect(fn)
        screen=self.screen().availableGeometry()
        self.resize(min(1160,screen.width()-40),min(850,screen.height()-60))
        self._selection_mode(True); self._refresh()
        pair=(getattr(parent,'request',None),getattr(parent,'result',None))
        if pair[0] is not None and pair[0].source_bytes is not None:
            QTimer.singleShot(0,lambda: self.load_source(pair[0].source_path, pair[0].source_bytes, pair=pair))

    @property
    def busy(self): return self._thread is not None

    def _refresh(self):
        idle=not self.busy and not self._closing
        editable=idle and self.source is not None
        valid=editable and not self._invalid
        for w in (self.image_button,self.open_button,self.reuse_checkbox,self.seed_edit,self.telemetry_combo,self.hardware_combo): w.setEnabled(idle)
        for w in (*self.spins,self.clear_button,self.selection_checkbox):w.setEnabled(editable)
        for w in (self.prepare_button,self.draft_button):w.setEnabled(valid)
        self.selector.setEnabled(idle and self.source is not None)
        self.run_button.setEnabled(valid and self.prepared is not None)
        self.save_button.setEnabled(idle and self.last_pair is not None)
        self.cancel_button.setEnabled(self.busy and not self._thread.cancel.is_set())
        if self.source and self.selector.roi is not None and self.processor is None: self.prepare_button.setEnabled(False)

    def _selection_mode(self, enabled):
        self.selector.selection_enabled=enabled
        self.selector.setDragMode(ImageView.DragMode.NoDrag if enabled else ImageView.DragMode.ScrollHandDrag)
        self.selector.viewport().setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.OpenHandCursor)

    def choose_image(self):
        name,_=QFileDialog.getOpenFileName(self,'选择ROI原图','','JPEG/PNG (*.jpg *.jpeg *.png);;所有文件 (*)')
        if name:self.load_source(Path(name))

    def load_source(self,path,data=None,*,pair=None,rect=None):
        if self.busy:return
        self.prepared=None; self._revision+=1
        def action(cancel,progress):
            source=decode_source(path,data); verify_preprocessing()
            whole=prepare_image(source.path,source_data=source.data)
            if cancel.is_set():raise InterruptedError('已取消原图读取')
            return source,whole,pair,rect,self._presentation(source,pair) if pair else None
        self._start_job('load',action)

    @staticmethod
    def _presentation(source,pair):
        request,result=pair
        context=result.metadata.get('input_context',{})
        rect=context['roi']['bounds'] if context.get('roi') else None
        mapped=map_mask(source,rect or (0,0,source.rgb.shape[1],source.rgb.shape[0]),result.mask)
        return mapped['mapped_overlay.png'],rect,source.path.name

    def _set_source(self,source,whole,pair,rect,presentation,history=()):
        self.source,self.whole_input=source,whole
        self._ancestors=list(history);self.previous_overlay.clear_image();self.previous_group.hide();self.chain_label.hide()
        self._invalid=False
        self.selector.set_image(cv2.cvtColor(source.rgb,cv2.COLOR_RGB2BGR))
        blockers=[QSignalBlocker(s) for s in self.spins]
        for s,size in zip(self.spins,(source.rgb.shape[1],source.rgb.shape[0])*2):s.setMaximum(size)
        del blockers
        self._set_roi(rect)
        if pair is not None:
            context=pair[1].metadata.get('input_context',{})
            if context.get('schema')=='formal_roi_v1':
                self._set_roi(context['roi']['bounds'])
                self.prepared=None  # Existing result is not a newly prepared inference request.
            self._display_result(pair,presentation,reopened=True)
        self.original_label.setText('方向校正原图 · '+source.path.name[:60])

    def _set_roi(self,rect):
        previous=self.selector.roi
        self.selector.set_roi(rect)
        if previous==self.selector.roi:self._roi_changed(rect)
        self.preview_crop()

    def _roi_changed(self,rect):
        self._revision+=1; self._invalid=False; self.prepared=None
        self.input_view.clear_image()
        self.input_view.scene.setSceneRect(0,0,1,1)
        self.input_label.setText('正式ROI模型输入 · 等待成员2规则' if self.processor is None else 'ROI模型输入 · 准备后显示')
        if self.source is None:return
        w,h=self.source.rgb.shape[1],self.source.rgb.shape[0]
        shown=rect or (0,0,w,h)
        blockers=[QSignalBlocker(s) for s in self.spins]
        for spin,value in zip(self.spins,shown):spin.setValue(value)
        del blockers
        self.selection_label.setText(f'当前草稿：{list(rect)} · 宽{rect[2]-rect[0]} 高{rect[3]-rect[1]} · 原图{w}×{h}' if rect else
                                     f'当前整图模式 · 原图{w}×{h}；Enter才发送新帧')
        if rect is None:
            self.prepared=self.whole_input
            self._show_input(self.prepared.rgb)
        self.status.setText('ROI已改动：尚未发送。等待成员2预处理规则/金标准；可保存草稿。' if rect and self.processor is None else
                            '当前选择尚未推理；旧成功结果仍对应原帧。准备并检查实际输入后再按Enter。')
        self._refresh()

    def _coordinate_edited(self,*_args):
        if self.source is None:return
        values=tuple(s.value() for s in self.spins)
        try:
            validate_rect(values,self.source.rgb.shape[1],self.source.rgb.shape[0])
            self.selector.set_roi(values)
            self._invalid=False; self._timer.start()
        except ValueError as exc:
            self._invalid=True;self.prepared=None;self.status.setText(str(exc))
        self._refresh()

    def clear_roi(self):
        if not self.busy and self.source:self._set_roi(None)

    def preview_crop(self):
        if self.source is None or self._invalid or self._closing:return
        if self.busy:
            self._pending_crop=True;return
        source,rect,revision=self.source,self.selector.roi,self._revision
        def action(cancel,progress):
            return revision, source.rgb if rect is None else crop_rgb(source,rect)
        self._start_job('crop',action)

    def prepare_input(self):
        if self.busy or self.source is None or self._invalid:return
        if self.selector.roi is None:
            self.prepared=self.whole_input;self._show_input(self.prepared.rgb);self._refresh();return
        if self.processor is None:
            self.status.setText('尚无成员2确认的ROI转换入口，未生成正式模型输入、未发包。');return
        source,rect,revision=self.source,self.selector.roi,self._revision
        processor=self.processor
        def action(cancel,progress):
            prepared=processor.prepare(source,rect)
            validate_source(prepared.context,prepared.rgb,prepared.source_bytes)
            if prepared.source_bytes!=source.data or tuple(prepared.context['roi']['bounds'])!=rect:
                raise ValueError('ROI适配器返回了其他原图或坐标')
            if cancel.is_set():raise InterruptedError('已取消ROI预处理')
            return revision,prepared
        self._start_job('prepare',action)

    def _show_input(self,raw):
        from .review import quality_bytes,quality_message
        self.input_label.setText('待发送模型输入 · 256×256（检查后显式推理）\n输入提醒：'+quality_message(quality_bytes(raw)))
        self.input_label.setWordWrap(True)
        self.parent().show_input_quality(raw,'ROI / 整图正式输入',pending=True)
        self.input_view.set_image(cv2.cvtColor(np.frombuffer(raw,np.uint8).reshape(256,256,3),cv2.COLOR_RGB2BGR))

    def start(self):
        if self.busy or self._closing or self.prepared is None or self._invalid:
            return
        prepared=self.prepared
        context=prepared.context
        if self.selector.roi is not None and (context.get('schema')!='formal_roi_v1'
                                             or tuple(context['roi']['bounds'])!=self.selector.roi):
            self.status.setText('ROI已变更，请重新准备输入');return
        config=FormalConfig(reuse_model=self.reuse_checkbox.isChecked(),telemetry_mode=self.telemetry_combo.currentData(),
                            hardware_profile=DEPLOYMENT_PROFILE)
        seed=Path(self.seed_edit.text().strip()) if self.seed_edit.text().strip() else None
        def action(cancel,progress):
            outcome=run_formal(prepared.rgb,config,default_output(),source_state=seed,cancel=cancel,progress=progress,
                               input_context=context,source_bytes=prepared.source_bytes,cpu_preprocessing_ms=prepared.cpu_preprocessing_ms)
            if not outcome.report['complete']:
                state='合法FINISH已确认，板卡本帧已释放；处理本地保存故障，不需盲目复位。' if outcome.report.get('hardware_finish_confirmed') else (
                    '板卡释放状态未确认，请核对会话。' if outcome.report.get('manual_recovery_required') else '未发起需恢复的板卡会话。')
                raise RuntimeError(f"{outcome.report['error']}\n{state}\n{outcome.directory}")
            pair=live_pair(outcome)
            source=decode_source(pair[0].source_path,pair[0].source_bytes)
            return pair,self._presentation(source,pair)
        self._start_job('run',action)

    def _display_result(self,pair,presentation,*,reopened=False):
        request,result=pair
        overlay,rect,filename=presentation
        self.overlay.set_image(overlay)
        self.mask_view.set_image(result.mask)
        self._show_input(np.ascontiguousarray(request.image_bgr[:,:,::-1]).tobytes())
        self.input_label.setText('成功帧实际模型输入 · 256×256，非新选择')
        self.last_pair=pair
        from .roi_edge import reminder_text
        text,warning=reminder_text(result.metadata,result.mask,historical=reopened or result.metadata.get('display_mode')=='offline_replay')
        self.edge_label.setText(text or '当前成功帧为整图模式；没有ROI触边提醒。')
        set_fluent_property(self.edge_label,'fluentSeverity','warning' if warning else 'info')
        origin='本机协议模拟，非实板验收' if result.metadata['source_kind']=='protocol_simulator' else (
               '记录查看，本次未推理' if reopened else '本次真实FPGA结果')
        self.overlay_label.setText(f'成功帧 {result.metadata["frame_id"]} · {origin} · {filename[:50]}\nROI {rect if rect is not None else "整图"}；阴影区未处理，不等于背景')
        graph=result.timings_ms.get('fpga_graph')
        diameter=result.measurements.max_feret_px
        diameter_text=f'{diameter:.2f} px' if diameter is not None else '不适用（无前景）'
        self.result_label.setText(f'模型256网格：面积 {result.measurements.area_px2} px² · Feret {diameter_text}\n'
                                 f'板内图 {graph if graph is not None else "不可用"} ms · 图像E2E {result.timings_ms["image_transport_e2e"]:.2f} ms；不含模型加载/PC预处理，未验收5FPS。')
        if self._ancestors:
            parent=self._ancestors[-1]
            self.previous_overlay.set_image(self._presentation(self.source,parent)[0])
            self.chain_label.setText(f'父帧 {parent[1].metadata["frame_id"]} → 当前帧 {result.metadata["frame_id"]}；'
                                    '均为各自原始结果，框外未处理；测量不可直接相减。')
            self.previous_group.show();self.chain_label.show()
        elif result.metadata.get('roi_chain_warning'):
            self.chain_label.setText(result.metadata['roi_chain_warning']+'；当前帧原始结果可用。')
            self.previous_group.hide();self.chain_label.show()
        else:
            self.previous_group.hide();self.chain_label.hide()

    def choose_open(self):
        folder=QFileDialog.getExistingDirectory(self,'打开包含record.json或roi_draft.json的目录')
        if folder:self.open_folder(Path(folder))

    def open_folder(self,folder):
        if self.busy:return
        self.prepared=None
        def action(cancel,progress):
            if (folder/'roi_draft.json').exists():
                source,rect=load_draft(folder);pair=None
            else:
                record=load_record(folder)
                if record.request.source_bytes is None:raise ValueError('记录没有原文件快照，请另行打开原图')
                source=decode_source(record.request.source_path,record.request.source_bytes)
                pair=(record.request,record.result);rect=None
            whole=prepare_image(source.path,source_data=source.data)
            history=[]
            if pair:
                from .roi_chain import load
                try:history=load(folder,pair)
                except (ValueError,OSError) as exc:
                    pair[1].metadata['roi_chain_warning']='父记录不可用：'+str(exc)
            return source,whole,pair,rect,self._presentation(source,pair) if pair else None,history
        self._start_job('load',action)

    def choose_draft(self):
        folder=QFileDialog.getExistingDirectory(self,'选择ROI草稿保存目录')
        if folder:self.export_draft(Path(folder))

    def export_draft(self,folder):
        if self.busy or self.source is None or self._invalid:return
        source,rect=self.source,self.selector.roi
        self._start_job('draft',lambda cancel,progress:save_draft(folder,source,rect))

    def choose_save(self):
        folder=QFileDialog.getExistingDirectory(self,'保存最近成功结果（非当前未推理草稿）')
        if folder:self.export_result(Path(folder))

    def export_result(self,folder):
        if self.busy or self.last_pair is None:return
        request,result=self.last_pair
        from .roi_chain import save
        ancestors=list(self._ancestors)
        self._start_job('save',lambda cancel,progress:save(folder,(request,result),ancestors))

    def _start_job(self,kind,action):
        self._action_started=perf_counter()
        self._kind=kind;self._completion=None;self._timer.stop()
        self._thread=RGBWorker(action,self)
        self._thread.succeeded.connect(self._success);self._thread.failed.connect(self._failure)
        self._thread.progress.connect(self._progress);self._thread.finished.connect(self._finished)
        self.status.setText({'load':'正在读取原图/记录（不连接FPGA）','crop':'正在显示当前实际裁剪（未发送）',
                             'prepare':'正在生成并校验实际ROI模型输入（未发送）','run':'正在执行本次FPGA帧',
                             'draft':'正在保存选择草稿（非推理结果）','save':'正在保存最近成功结果'}[kind])
        self._refresh();self._thread.start()

    @Slot(object)
    def _success(self,value):self._completion=True,value
    @Slot(str)
    def _failure(self,message):self._completion=False,message
    @Slot(str,int)
    def _progress(self,stage,count):self.status.setText(f'{stage} · {count}')

    @Slot()
    def _finished(self):
        thread,self._thread=self._thread,None;thread.deleteLater()
        kind,self._kind=self._kind,None
        try:
            if self._completion is None or not self._completion[0]:raise RuntimeError(self._completion[1] if self._completion else '任务未返回')
            value=self._completion[1]
            if kind=='load':self._set_source(*value)
            elif kind=='crop':
                revision,rgb=value
                if revision==self._revision:
                    self.crop_view.set_image(cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR))
                    self.status.setText('实际裁剪预览已更新；ROI规则待成员2确认，未生成正式256输入、未发包。'
                                        if self.selector.roi is not None and self.processor is None else
                                        '当前输入预览就绪，尚未发送新帧；旧成功结果保持原坐标。')
                else:self._pending_crop=True
            elif kind=='prepare':
                revision,prepared=value
                if revision==self._revision:
                    self.prepared=prepared;self._show_input(prepared.rgb)
                    self.status.setText('实际ROI输入已就绪；尚未发送。检查后按Enter创建新帧。')
                else:self.status.setText('选择已改动，已丢弃过期输入')
            elif kind=='run':
                pair,presentation=value
                self._ancestors=[];self.previous_overlay.clear_image()
                self.parent().commit_formal_result(pair);self._display_result(pair,presentation)
                pair[1].metadata['user_visible_wait_ms']=(perf_counter()-self._action_started)*1000
                self.status.setText('本次新帧已完成；原始256Mask保留，原图叠加仅供显示；Ctrl+S保存成功结果。')
                from .performance import timing_text
                self.status.setText(self.status.text()+'\n'+timing_text(pair[1]))
            elif kind in ('save','draft'):
                if kind=='save':
                    self.last_export=value
                else:self.last_draft=value
                self.status.setText(f'{"成功结果" if kind=="save" else "ROI草稿（未推理）"}已保存：{value}')
            set_fluent_property(self.status,'fluentSeverity','info')
        except Exception as exc:
            self.status.setText(f'失败 · {exc}\n保留上一次成功帧和叠加结果。')
            set_fluent_property(self.status,'fluentSeverity','danger')
        if self._pending_crop and not self.busy and not self._closing:
            self._pending_crop=False;self.preview_crop()
        self._refresh()
        if self._closing:self.close()

    def cancel(self):
        if self.busy:
            self._thread.cancel.set()
            self.status.setText('正在停止PC等待；不自动复位或回退状态。' if self._kind=='run' else '正在等待本地任务安全结束。')
            self._refresh()

    def reject(self):self.close()
    def closeEvent(self,event):
        self._timer.stop()
        if self.busy:
            self._closing=True;self.cancel();event.ignore()
        else:event.accept()
