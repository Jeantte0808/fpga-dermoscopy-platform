"""Model-grid review workstation with explicit immutable save and offline PDF."""
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt,Signal,QUrl
from PySide6.QtGui import QDesktopServices,QKeySequence,QShortcut
from PySide6.QtWidgets import (QWidget,QLabel,QPushButton,QVBoxLayout,QHBoxLayout,QFormLayout,
    QComboBox,QSpinBox,QCheckBox,QTabWidget,QPlainTextEdit,QLineEdit,QFileDialog,
    QTableWidget,QTableWidgetItem,QAbstractItemView,QHeaderView,QMessageBox)
from .analysis_ui import AnalysisDialog
from .analysis_identity import frozen_pair,result_identity
from .gui import ImageView
from .imaging import make_overlay
from .morphology import measure
from .review import (MaskEditor,prepare,save_review,load_review,measurement_overlay,
                     quality_message,digest,TEXT_FIELDS)
from .runtime_paths import default_output


class MaskCanvas(ImageView):
    edited=Signal()
    def __init__(self):
        super().__init__('正在准备复核输入',smooth=False)
        self.editor=None;self.base=None;self.radius=3;self.mode='pan';self._last=None
        self.setAccessibleName('256模型网格修订画布；选择画笔或擦除后按住左键编辑，浏览模式可拖动')
        self.setMinimumSize(300,280)

    def present(self,image):
        if not self.has_image:self.set_image(image)
        else:
            self.pixmap=self.prepare_pixmap(image)
            self.scene.items()[0].setPixmap(self.pixmap)

    def point(self,event):
        p=self.mapToScene(event.position().toPoint())
        return p.x(),p.y()

    def mousePressEvent(self,event):
        p=self.point(event)
        if self.editor and self.mode!='pan' and event.button()==Qt.MouseButton.LeftButton and 0<=p[0]<256 and 0<=p[1]<256:
            self._last=p;self.editor.begin();self.editor.line(p,p,radius=self.radius,value=255 if self.mode=='paint' else 0)
            self.present(make_overlay(self.base,self.editor.mask));event.accept();return
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self._last is not None:
            p=self.point(event);self.editor.line(self._last,p,radius=self.radius,value=255 if self.mode=='paint' else 0)
            self._last=p;self.present(make_overlay(self.base,self.editor.mask));event.accept();return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if self._last is not None and event.button()==Qt.MouseButton.LeftButton:
            self._last=None
            if self.editor.end():self.edited.emit()
            event.accept();return
        super().mouseReleaseEvent(event)


class ReviewDialog(AnalysisDialog):
    def __init__(self,parent):
        super().__init__(parent);self.setWindowTitle('分割复核与测量');self.resize(1120,790)
        self.pair=frozen_pair((parent.request,parent.result));self.identity=result_identity(self.pair)
        self.folder=None;self.saved=None;self.record=None;self.editor=None
        self.original_measurement=self.current_measurement=None;self.ai_draft=None;self.dirty=False
        source=parent.analysis_record()
        layout=QVBoxLayout(self)
        self.scope=QLabel('固定原始成功帧；画笔只修改独立副本。测量单位：256模型网格px / px²，未标定毫米。')
        self.scope.setWordWrap(True);layout.addWidget(self.scope)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        canvas_page=QWidget();cl=QVBoxLayout(canvas_page);self.tabs.addTab(canvas_page,'分割修订')
        toolbar=QHBoxLayout();self.mode=QComboBox();self.mode.addItem('浏览 / 拖动','pan');self.mode.addItem('画笔：增加前景','paint');self.mode.addItem('擦除：移除前景','erase')
        self.mode.setAccessibleName('修订工具');toolbar.addWidget(self.mode)
        toolbar.addWidget(QLabel('画笔半径'));self.brush=QSpinBox();self.brush.setRange(1,32);self.brush.setValue(3);self.brush.setSuffix(' px');toolbar.addWidget(self.brush)
        self.undo_button=QPushButton('撤销');self.redo_button=QPushButton('重做');self.reset_button=QPushButton('恢复原始Mask')
        for b in (self.undo_button,self.redo_button,self.reset_button):toolbar.addWidget(b)
        self.feret=QCheckBox('最大Feret径（黄）');self.feret.setChecked(True)
        self.axes=QCheckBox('矩轴（蓝 / 紫）');self.axes.setChecked(True)
        toolbar.addWidget(self.feret);toolbar.addWidget(self.axes);cl.addLayout(toolbar)
        columns=QHBoxLayout();left=QVBoxLayout();right=QVBoxLayout()
        self.raw_caption=QLabel('原始分割结果（只读）');left.addWidget(self.raw_caption);right.addWidget(QLabel('独立修订预览'))
        self.raw_view=ImageView('原始结果',smooth=False);self.raw_view.setMinimumSize(300,280);self.canvas=MaskCanvas()
        left.addWidget(self.raw_view,1);right.addWidget(self.canvas,1);columns.addLayout(left,1);columns.addLayout(right,1);cl.addLayout(columns,1)
        self.edit_status=QLabel('尚未准备');self.edit_status.setWordWrap(True);cl.addWidget(self.edit_status)
        fit=QPushButton('适应窗口');fit.clicked.connect(lambda:(self.raw_view.fit_image(),self.canvas.fit_image()));cl.addWidget(fit)
        page=QWidget();ml=QVBoxLayout(page);self.tabs.addTab(page,'测量与输入提醒')
        self.metrics=QTableWidget(0,3);self.metrics.setHorizontalHeaderLabels(['指标','原始FPGA','当前修订预览'])
        self.metrics.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.metrics.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        ml.addWidget(self.metrics,1)
        self.quality_label=QLabel('质量检查尚未完成');self.quality_label.setWordWrap(True);self.quality_label.setTextFormat(Qt.TextFormat.PlainText);ml.addWidget(self.quality_label)
        page=QWidget();form=QFormLayout(page);self.tabs.addTab(page,'报告审核')
        self.import_ai=QPushButton('采用同帧已有AI草稿（不调用云端）');form.addRow(self.import_ai)
        self.texts={}
        for key,label in [('observations','人工所见'),('summary','人工摘要'),('advice','复核建议')]:
            field=QPlainTextEdit();field.setAccessibleName(label);field.textChanged.connect(self.mark_dirty);self.texts[key]=field;form.addRow(label,field)
        self.operator=QLineEdit();self.operator.setMaxLength(80);self.operator.setAccessibleName('复核人（用户填写）');form.addRow('复核人',self.operator)
        self.note=QLineEdit();self.note.setMaxLength(2000);form.addRow('修订说明',self.note)
        self.operator.textChanged.connect(self.mark_dirty);self.note.textChanged.connect(self.mark_dirty)
        row=QHBoxLayout();self.save_button=QPushButton('保存复核并生成PDF');self.save_button.setProperty('fluentAppearance','primary')
        self.reopen_button=QPushButton('打开同帧复核记录');self.pdf_button=QPushButton('打开复核PDF');self.folder_button=QPushButton('打开复核目录')
        self.regenerate_button=QPushButton('重导已保存PDF')
        for b in (self.save_button,self.reopen_button,self.regenerate_button,self.pdf_button,self.folder_button):row.addWidget(b)
        layout.addLayout(row);self.status=QLabel('正在准备独立复核工作区；未发送硬件或云端请求。');self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.status)
        for b in self.findChildren(QPushButton):b.setAutoDefault(False)
        self.mode.currentIndexChanged.connect(self.tool_changed);self.brush.valueChanged.connect(lambda v:setattr(self.canvas,'radius',v))
        self.canvas.edited.connect(self.changed);self.undo_button.clicked.connect(lambda:self.history(False));self.redo_button.clicked.connect(lambda:self.history(True))
        self.reset_button.clicked.connect(self.reset);self.feret.toggled.connect(self.render);self.axes.toggled.connect(self.render)
        self.save_button.clicked.connect(self.save);self.reopen_button.clicked.connect(self.reopen);self.import_ai.clicked.connect(self.adopt_ai)
        self.regenerate_button.clicked.connect(self.regenerate)
        self.pdf_button.clicked.connect(lambda:self.open_file(False));self.folder_button.clicked.connect(lambda:self.open_file(True))
        for keys,fn in [('Ctrl+S',self.save),('Ctrl+Z',lambda:self.history(False)),('Ctrl+Y',lambda:self.history(True))]:
            shortcut=QShortcut(QKeySequence(keys),self if keys=='Ctrl+S' else self.canvas)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut);shortcut.activated.connect(fn)
        pair=self.pair
        def action(cancel,_progress):
            from .records import load_record
            from .storage import save_result
            target=None
            if source:
                candidate=load_record(Path(source))
                if result_identity((candidate.request,candidate.result))==self.identity:target=Path(source)
            if target is None:
                root=default_output()/'review_records';root.mkdir(parents=True,exist_ok=True);target=save_result(root,*pair)
            return prepare(target,cancel=cancel)
        self.start_job('prepare',action)

    def tool_changed(self):
        self.canvas.mode=self.mode.currentData();self.canvas.setCursor(Qt.CursorShape.CrossCursor if self.canvas.mode!='pan' else Qt.CursorShape.OpenHandCursor)

    def refresh(self):
        ready=self.editor is not None and not self.busy
        for w in (self.tabs,self.save_button,self.reopen_button):w.setEnabled(ready)
        self.undo_button.setEnabled(ready and bool(self.editor.undo_stack))
        self.redo_button.setEnabled(ready and bool(self.editor.redo_stack))
        from .review_pdf import valid_review_pdf
        self.pdf_button.setEnabled(not self.busy and self.saved is not None and valid_review_pdf(self.saved))
        self.regenerate_button.setEnabled(not self.busy and self.saved is not None and not self.dirty)
        self.folder_button.setEnabled(not self.busy and self.saved is not None)

    def mark_dirty(self,*_):
        if self.editor is not None:
            self.dirty=True
            self.regenerate_button.setEnabled(False)

    def changed(self):
        if not self.editor or self.busy:return
        self.mark_dirty();mask=self.editor.mask.copy();rgb=np.ascontiguousarray(self.pair[0].image_bgr[:,:,::-1])
        source='manual_preview' if self.editor.changed_pixels else 'raw_fpga'
        self.start_job('measure',lambda _c,_p:measure(rgb,mask,mask_source=source))

    def history(self,redo):
        if self.editor and not self.busy:
            if (self.editor.redo() if redo else self.editor.undo()):self.changed()

    def reset(self):
        if self.editor and not self.busy and self.editor.replace(self.editor.original):self.changed()

    def render(self,*_):
        if not self.editor or not self.current_measurement:return
        self.raw_view.set_image(measurement_overlay(self.pair[0].image_bgr,self.editor.original,self.original_measurement,feret=self.feret.isChecked(),axes=self.axes.isChecked()))
        self.canvas.present(measurement_overlay(self.pair[0].image_bgr,self.editor.mask,self.current_measurement,feret=self.feret.isChecked(),axes=self.axes.isChecked()))
        self.edit_status.setText(f'与原始Mask相比修改 {self.editor.changed_pixels} 个网格像素；'+('当前有未保存复核内容。' if self.dirty else '原始结果保留。')+' 矩轴不是Feret径；近圆形方向不稳定。')
        rows=[]
        for label,key in [('面积（px²）','area_px2'),('最大Feret径（px）','max_feret_px')]:rows.append((label,*[m[key] for m in (self.original_measurement,self.current_measurement)]))
        for label,get in [('周长（px）',lambda m:m['perimeter']['value_px']),('圆度',lambda m:m['circularity']['value']),('矩长轴（px）',lambda m:m['axes'].get('major_length_px')),('矩短轴（px）',lambda m:m['axes'].get('minor_length_px')),('矩轴比',lambda m:m['axes'].get('axis_ratio'))]:
            rows.append((label,*[get(m) for m in (self.original_measurement,self.current_measurement)]))
        for color in ('R','G','B'):
            rows.append((color+'均值（前景内）',*[m['colors']['channels'][color]['mean'] if m['colors']['valid'] else None for m in (self.original_measurement,self.current_measurement)]))
        self.metrics.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,value in enumerate(row):self.metrics.setItem(i,j,QTableWidgetItem('不可用' if value is None else f'{value:.3f}' if isinstance(value,float) else str(value)))

    def save(self):
        if self.busy or not self.editor:return
        mask=self.editor.mask.copy();texts={key:field.toPlainText() for key,field in self.texts.items()}
        folder=self.folder;operator=self.operator.text();note=self.note.text();draft=self.ai_draft
        def action(cancel,_progress):
            saved=save_review(folder,mask,operator=operator,note=note,texts=texts,ai_draft=draft,cancel=cancel)
            from .analysis_snapshot import save_selection
            save_selection(folder,saved)
            from .review_pdf import export_review_pdf
            try:export_review_pdf(saved,cancel=cancel);return dict(folder=saved,pdf_error=None)
            except Exception:return dict(folder=saved,pdf_error='报告导出失败；复核记录已保存，可打开目录或再次生成，不需复位板卡。')
        self.start_job('save',action)

    def reopen(self):
        if self.busy:return
        if self.dirty and QMessageBox.question(self,'放弃未保存的复核修改？','打开记录会替换当前修订预览。',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        folder=QFileDialog.getExistingDirectory(self,'选择同帧review.json所在目录')
        if folder:
            expected=self.identity
            def action(*_):
                value=load_review(folder)
                if value['document']['identity']!=expected:raise ValueError('此复核记录属于另一帧')
                return value
            self.start_job('reopen',action)

    def adopt_ai(self):
        if self.busy:return
        task=QFileDialog.getExistingDirectory(self,'选择同帧已保存AI任务（不调用云端）')
        if not task:return
        def action(*_):
            from .ai_service import load_analysis
            from .ai_report_display import presentation_copy,result_summary
            meta,answer=load_analysis(task)
            if meta['identity']!=self.identity:raise ValueError('AI草稿属于另一帧')
            shown=presentation_copy(answer)
            return dict(identity=meta['identity'],task_id=meta['task_id'],answer_sha256=meta['answer_sha256'],
                        texts=dict(observations='\n'.join(shown['image_observations']),summary=result_summary(shown),advice='\n'.join(shown['review_points'])))
        self.start_job('draft',action)

    def completed(self,kind,value,error):
        if error:self.status.setText('复核任务未完成：'+error+'；原始分割保留。');return
        if kind=='prepare':
            self.folder=value['record'].directory;self.record=value['record'];self.original_measurement=self.current_measurement=value['measurement']
            self.parent().bind_analysis_record(self.folder,self.identity)
            self.editor=MaskEditor(self.pair[1].mask);self.canvas.editor=self.editor;self.canvas.base=self.pair[0].image_bgr
            q=value['quality'];scores=q['scores']
            self.quality_label.setText('实际256模型输入：'+quality_message(q)+f"\n清晰度分数 {scores['laplacian_variance']:.3f}；平均亮度 {scores['mean_luma']:.3f}；暗像素 {scores['dark_fraction']:.2%}；高亮像素 {scores['overexposed_fraction']:.2%}。镜像指标暂不启用。")
            source=('协议模拟（非实板）' if self.identity['source_kind']=='protocol_simulator' else
                    '历史实板记录（未重新推理）' if self.identity['display_mode']=='offline_replay' or self.parent().opened_record is not None else '本次FPGA成功帧')
            self.raw_caption.setText('原始分割结果（只读） · '+source)
            scope='ROI框外未处理。' if self.identity['roi'] else '整图范围。'
            self.scope.setText(f"{source} · 帧 {self.identity['frame_id']} · ROI {self.identity['roi'] or '整图'}；只编辑256模型网格。{scope}原始Mask保持不变。")
            self.status.setText('复核工作区就绪；浏览模式可拖动，选择画笔或擦除后编辑。不连接FPGA或云端。')
        elif kind=='measure':self.current_measurement=value;self.status.setText('测量已更新，等待显式保存。')
        elif kind in ('save','pdf'):
            self.saved=value['folder'];self.dirty=False;self.status.setText(value['pdf_error'] or '复核记录与PDF已保存；原始结果未覆盖。')
        elif kind=='reopen':
            self.saved=value['folder'];self.editor=MaskEditor(self.pair[1].mask);self.editor.replace(value['mask']);self.canvas.editor=self.editor
            data=value['document'];self.current_measurement=data['review_measurement'];self.ai_draft=data.get('ai_draft')
            for key in TEXT_FIELDS:self.texts[key].setPlainText(data['texts'][key])
            self.operator.setText(data['operator']);self.note.setText(data['note']);self.dirty=False;self.status.setText('同帧复核已恢复；未重新推理。')
        elif kind=='draft':
            self.ai_draft=value
            for key in TEXT_FIELDS:self.texts[key].setPlainText(value['texts'][key])
            self.dirty=True;self.status.setText('已采用同帧AI显示草稿，可人工修改；尚未保存，未调用云端。')
        self.render()

    def open_file(self,folder):
        if not folder:
            from .review_pdf import valid_review_pdf
            if not self.saved or not valid_review_pdf(self.saved):self.status.setText('复核PDF缺失或摘要不符，请重新导出已保存报告。');self.refresh();return
        if self.saved and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.saved if folder else self.saved/'review.pdf'))):self.status.setText('打开失败，请在文件管理器查看：'+str(self.saved))

    def regenerate(self):
        if not self.saved or self.busy or self.dirty:return
        folder=self.saved
        def action(cancel,_progress):
            from .review_pdf import export_review_pdf
            try:export_review_pdf(folder,cancel=cancel);return dict(folder=folder,pdf_error=None)
            except Exception:return dict(folder=folder,pdf_error='报告导出失败；复核记录保留，未请求板卡或云端。')
        self.start_job('pdf',action)

    def closeEvent(self,event):
        if self.dirty and not self._closing:
            if QMessageBox.question(self,'关闭未保存复核？','未保存的画笔和报告修改将丢弃，原始分割不受影响。',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:
                event.ignore();return
        super().closeEvent(event)
