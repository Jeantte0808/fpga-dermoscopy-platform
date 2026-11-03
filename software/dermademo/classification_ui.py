"""Explicit whole-original classifier controls; worker result cannot follow a changed source."""
from pathlib import Path
from hashlib import sha256
from PySide6.QtCore import Qt,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QFileDialog
from .analysis_ui import AnalysisDialog
from .classification import ClassifierClient,NOTICE,run,load,attach
from .cpu_baseline import default_python
from .runtime_paths import default_output
from .cpu_runtime import selected_python

class ClassificationDialog(AnalysisDialog):
    def __init__(self,parent):
        super().__init__(parent);self.setWindowTitle('整图分类教学候选V2');self.resize(800,560)
        self.client=None;self.raw=None;self.name=None;self.record=None;self.folder=None;self.pending_pair=None
        layout=QVBoxLayout(self)
        for text in (NOTICE,'内部保留测试136张：准确率84.6%；黑色素瘤漏判16/68，非黑色素瘤误报5/68。不是本次图片的正确率。'):
            label=QLabel(text);label.setWordWrap(True);layout.addWidget(label)
        row=QHBoxLayout();self.python=QLineEdit(selected_python(parent.preferences))
        self.python.setAccessibleName('独立分类Python解释器');select=QPushButton('选择CPU Python')
        row.addWidget(self.python,1);row.addWidget(select);layout.addLayout(row)
        select.clicked.connect(self.pick_python)
        self.runtime_save=QPushButton('检查 / 保存共享配置');row.addWidget(self.runtime_save)
        self.runtime_save.clicked.connect(lambda:parent.open_cpu_settings(self.python.text()))
        parent.cpu_runtime_changed.connect(self.python.setText)
        row=QHBoxLayout();self.choose=QPushButton('选择原始整图JPEG/PNG');self.current=QPushButton('采用当前整张原图');self.reopen=QPushButton('重开分类记录')
        for b in (self.choose,self.current,self.reopen):row.addWidget(b)
        layout.addLayout(row);self.choose.clicked.connect(self.pick);self.current.clicked.connect(self.use_current);self.reopen.clicked.connect(self.open_saved)
        self.source=QLabel('尚未选择原图；不使用分割256图、增强图或ROI裁剪。');self.source.setWordWrap(True);layout.addWidget(self.source)
        self.result=QLabel('分类尚未运行。');self.result.setWordWrap(True);self.result.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.result)
        self.status=QLabel('就绪。整图分类独立运行，不发送FPGA命令。');self.status.setWordWrap(True);layout.addWidget(self.status)
        row=QHBoxLayout();self.start=QPushButton('运行整图分类');self.pdf=QPushButton('生成分类报告PDF');self.directory=QPushButton('打开保存目录')
        for b in (self.start,self.pdf,self.directory):b.setAutoDefault(False);row.addWidget(b)
        layout.addLayout(row);self.start.clicked.connect(self.infer);self.pdf.clicked.connect(self.export_pdf)
        self.directory.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder))))
        self.refresh()
        if parent.preferences.settings.value('cpu/python','') and not self.python.text():self.status.setText('已保存的Python路径失效，请重新选择并检查共享CPU配置。')

    def pick_python(self):
        path,_=QFileDialog.getOpenFileName(self,'选择已安装分类依赖的Python','','Python (python.exe)')
        if path:self.python.setText(path)

    def set_source(self,raw,name,record=None):
        self.raw,self.name,self.record=raw,name,record;self.folder=None;self.pending_pair=None
        self.result.setText('分类尚未运行。');self.source.setText(name+' · 整张原图（独立中心裁剪，不分类任意ROI）');self.refresh()

    def pick(self):
        path,_=QFileDialog.getOpenFileName(self,'选择原始整图','','原图 (*.jpg *.jpeg *.png)')
        if path:self.set_source(Path(path).read_bytes(),Path(path).name)

    def use_current(self):
        parent=self.parent();request=parent.request
        if request is not None and request.source_bytes:
            folder=parent.analysis_record()
            self.set_source(request.source_bytes,request.source_path.name,folder)
            if folder is None and parent.result is not None and parent.result.capabilities.key=='fpga_formal_v5':
                from .analysis_identity import frozen_pair
                self.pending_pair=frozen_pair((parent.request,parent.result))
        elif parent.source_path and not parent.is_synthetic:
            path=Path(parent.source_path);self.set_source(path.read_bytes(),path.name)
        else:self.status.setText('当前没有可追溯原图，请单独选择原始JPEG/PNG。')

    def refresh(self):
        for b in (self.choose,self.current,self.reopen,self.python,self.runtime_save):b.setEnabled(not self.busy)
        self.start.setEnabled(not self.busy and self.raw is not None)
        self.pdf.setEnabled(not self.busy and self.folder is not None)
        self.directory.setEnabled(not self.busy and self.folder is not None)

    def infer(self):
        if self.busy or self.raw is None:return
        python=self.python.text();raw,name=self.raw,self.name;record=self.record;pair=self.pending_pair
        from .cpu_runtime import validate_path
        try:validate_path(python)
        except ValueError as exc:self.status.setText(str(exc));return
        # One resident model per interpreter; retained across repeated inferences below.
        def action(cancel,_):
            if getattr(self,'client_python',None)!=python:
                if self.client:self.client.close()
                self.client=None
            self.client_python=python
            if self.client is None:self.client=ClassifierClient(python)
            client=self.client
            folder=run(client,raw,name,default_output()/'classification',cancel=cancel)
            warning=None;linked=record;linked_identity=None
            if linked is None and pair is not None:
                from .storage import save_result
                target=default_output()/'classification_records';target.mkdir(parents=True,exist_ok=True);linked=save_result(target,*pair)
            if linked:
                try:linked_identity=attach(linked,folder)
                except Exception:warning='分类已单独保存；原图与分割记录不匹配或关联保存失败，未附加。'
            return dict(folder=folder,result=load(folder),warning=warning,record=linked if linked_identity else None,linked_identity=linked_identity)
        self.start_job('classify',action)

    def completed(self,kind,value,error):
        if error:self.status.setText('分类失败：'+error+'；分割结果保留。');return
        if kind=='pdf':
            self.status.setText('分类报告已生成。');QDesktopServices.openUrl(QUrl.fromLocalFile(str(value)));return
        self.folder=Path(value['folder']);result=value['result']
        self.record=Path(value['record']) if value.get('record') else None
        if value.get('record') and value.get('linked_identity'):
            self.record=Path(value['record']);self.parent().bind_analysis_record(self.record,value['linked_identity'])
        scores=result['class_scores'];label='黑色素瘤' if result['predicted_class']=='melanoma' else '非黑色素瘤组（不等于良性）'
        self.result.setText(f"教学候选预测：{label}\n非黑色素瘤组分数 {scores[0]['score']:.6f}；黑色素瘤分数 {scores[1]['score']:.6f}\n模型：{result['model_version']}\nCPU前向 {result['timing']['forward_ms']:.3f} ms；首次模型加载 {result['timing']['model_load_ms']:.3f} ms（不与FPGA分割算加速比）。")
        self.status.setText(value.get('warning') or '已保存原图、实际224输入、分类结果；等待人工审核。')

    def open_saved(self):
        path=QFileDialog.getExistingDirectory(self,'选择classification_*分类记录')
        if not path:return
        try:
            result=load(path);name=result['identity']['source_filename']
            self.set_source((Path(path)/name).read_bytes(),name)
            self.completed('load',dict(folder=path,result=result),None);self.refresh()
        except Exception as exc:self.status.setText('记录不可用：'+str(exc))

    def export_pdf(self):
        folder=self.folder
        if folder and not self.busy:
            record=self.record;historical=self.parent().opened_record is not None or bool(self.parent().result and self.parent().result.metadata.get('display_mode')=='offline_replay')
            def action(cancel,_):
                if record:
                    from .unified_report import export_single
                    return Path(export_single(record,cancel=cancel,historical=historical)['pdf'])
                from .classification_pdf import export
                return export(folder,cancel=cancel)
            self.start_job('pdf',action)

    def closeEvent(self,event):
        if not self.busy and self.client:self.client.close()
        super().closeEvent(event)
