"""Explicit check-and-save controls for a local interpreter, never an API URL."""
from PySide6.QtWidgets import QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QFileDialog
from .analysis_ui import AnalysisDialog
from .cpu_runtime import selected_python,probe_python,save_checked


class CPURuntimeDialog(AnalysisDialog):
    def __init__(self,parent,initial=None):
        super().__init__(parent);self.setWindowTitle('CPU Python配置');self.resize(760,280)
        self.checked=None;layout=QVBoxLayout(self)
        notice=QLabel('保存本机python.exe完整路径，供单图分类、多图队列分类和CPU对照共用。不是网址，也不包含API Key。检查只运行本地CPU依赖，不连接FPGA或云端。')
        notice.setWordWrap(True);layout.addWidget(notice)
        row=QHBoxLayout();self.path=QLineEdit(initial if initial is not None else selected_python(parent.preferences))
        self.path.setAccessibleName('本机CPU Python完整路径');self.path.setPlaceholderText(r'D:\cpu-env\Scripts\python.exe')
        self.choose=QPushButton('选择 / 更换');row.addWidget(self.path,1);row.addWidget(self.choose);layout.addLayout(row)
        row=QHBoxLayout();self.check=QPushButton('检查依赖');self.save=QPushButton('保存本机配置');self.remove=QPushButton('清除已保存路径')
        for b in (self.check,self.save,self.remove):b.setAutoDefault(False);row.addWidget(b)
        layout.addLayout(row);self.status=QLabel('选择后先检查依赖，成功后才能保存。保存路径不会运行分类或分割。');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.choose.clicked.connect(self.select);self.check.clicked.connect(self.inspect);self.save.clicked.connect(self.persist);self.remove.clicked.connect(self.clear)
        self.path.textChanged.connect(self.changed);self.refresh()
        if parent.preferences.settings.value('cpu/python','') and not self.path.text():
            self.status.setText('已保存的Python路径失效，请重新选择本机python.exe并检查依赖。')

    def changed(self,*_):self.checked=None;self.refresh()

    def refresh(self):
        for w in (self.path,self.choose,self.check,self.remove):w.setEnabled(not self.busy)
        self.save.setEnabled(not self.busy and self.checked is not None)

    def select(self):
        name,_=QFileDialog.getOpenFileName(self,'选择本机CPU Python','','Python (python.exe)')
        if name:self.path.setText(name)

    def inspect(self):
        value=self.path.text();self.checked=None;self.status.setText('正在本地检查CPU依赖，最长60秒；未调用模型或云端。')
        self.start_job('probe',lambda cancel,_:probe_python(value,cancel=cancel))

    def completed(self,kind,value,error):
        if error:self.checked=None;self.status.setText('检查未通过：'+error);return
        self.checked=value;versions=value['probe']['versions']
        self.status.setText('依赖检查通过：'+', '.join(k+' '+str(v) for k,v in versions.items())+'。可保存供下次启动使用。')

    def persist(self):
        if self.busy or self.checked is None:return
        try:
            value=save_checked(self.parent().preferences,self.checked)
            self.parent().cpu_runtime_changed.emit(value)
            self.status.setText('已保存本机Python完整路径；单图分类、队列分类及CPU对照共用，下次启动自动读取。')
        except Exception as exc:self.status.setText(str(exc))

    def clear(self):
        if self.parent().preferences._write({'cpu/python':'','cpu/probe':''}):
            self.path.clear();self.parent().cpu_runtime_changed.emit('');self.status.setText('已清除保存路径；需要时请重新选择并检查。')
