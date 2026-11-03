"""Explicit consent, OS credentials and saved AI report controls."""
from pathlib import Path
import json
from PySide6.QtCore import Qt,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel,QLineEdit,QCheckBox,QPushButton,QVBoxLayout,QHBoxLayout,QMessageBox,QFileDialog,QComboBox
from .analysis_ui import AnalysisDialog
from .analysis_identity import frozen_pair,result_identity
from .credentials import CredentialStore
from .ai_service import MODEL,DEFAULT_BASE,bundle,run_analysis,regenerate_pdf,load_analysis,load_display_task,recover_saved_response
from .runtime_paths import default_output
from .storage import save_result

class AIDialog(AnalysisDialog):
    def __init__(self,parent):
        super().__init__(parent)
        self.setWindowTitle('AI教学分析');self.resize(780,540)
        self.pair=frozen_pair((parent.request,parent.result));self.identity=result_identity(self.pair)
        self.task=None;self.record_folder=parent.analysis_record();self.store=CredentialStore();self._pending_cloud=None
        self.historical=parent.opened_record is not None or self.identity['display_mode']=='offline_replay'
        if self.record_folder is None and getattr(parent,'last_export',None):
            try:
                from .records import load_record
                saved=load_record(parent.last_export)
                if result_identity((saved.request,saved.result))==self.identity:self.record_folder=saved.directory
            except Exception:pass
        layout=QVBoxLayout(self)
        notice=QLabel(f"{self.pair[0].source_path.name} · frame {self.identity['frame_id']} · ROI {self.identity['roi']}\n"
                      f"来源 {'历史记录' if self.historical else '当前结果'} · {self.identity['source_kind']} / {self.identity['display_mode']}\n"
                      '仅作结果解释与教学分析；未经专业复核，不替代疾病分类或临床诊断。')
        notice.setWordWrap(True);notice.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(notice)
        self.review_choice=QComboBox();self.review_choice.addItem('按本图当前已保存复核选择分析（含分类与质量）','auto');self.review_choice.addItem('只分析原始FPGA Mask（含整图分类与质量）',None)
        self.review_choice.setAccessibleName('AI分析对应的修订版本；具体版本在上传前确认');layout.addWidget(self.review_choice)
        self.base=QLineEdit(DEFAULT_BASE);self.base.setAccessibleName('百炼北京官方API Host');layout.addWidget(self.base)
        self.key=QLineEdit();self.key.setEchoMode(QLineEdit.EchoMode.Password);self.key.setAccessibleName('API Key（密码输入）')
        self.key.setText(parent.ai_session_key);layout.addWidget(self.key)
        self.remember=QCheckBox('在本机安全保存（Windows凭据管理器，当前Windows用户）');layout.addWidget(self.remember)
        row=QHBoxLayout();self.save_key_button=QPushButton('保存 / 更换密钥');self.delete_key_button=QPushButton('删除已保存密钥')
        row.addWidget(self.save_key_button);row.addWidget(self.delete_key_button);layout.addLayout(row)
        self.credential_status=QLabel('已加载本机会话密钥；更换、删除和保存均不会调用云端。' if self.key.text() else '密钥只保存在内存；勾选后才能写入Windows凭据管理器。');layout.addWidget(self.credential_status)
        row=QHBoxLayout();self.run_button=QPushButton('AI分析（上传及计费确认）');self.load_button=QPushButton('打开已有AI记录')
        self.regenerate_button=QPushButton('仅重新生成PDF');row.addWidget(self.run_button);row.addWidget(self.load_button);row.addWidget(self.regenerate_button);layout.addLayout(row)
        self.pdf_attachments=QCheckBox('PDF附增强图、Mask及ROI细节（默认仅主报告）');layout.addWidget(self.pdf_attachments)
        row=QHBoxLayout();self.pdf_button=QPushButton('打开PDF');self.folder_button=QPushButton('打开目录');row.addWidget(self.pdf_button);row.addWidget(self.folder_button);layout.addLayout(row)
        self.recover_button=QPushButton('离线重新处理已保存响应（不调用AI）')
        self.recover_button.setAutoDefault(False);layout.addWidget(self.recover_button)
        self.recover_button.clicked.connect(self.recover_response)
        self.status=QLabel('就绪。未发送云端请求；打开报告与重新生成PDF均不调用AI。');self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.status)
        self.save_key_button.clicked.connect(self.save_key);self.delete_key_button.clicked.connect(self.delete_key)
        self.run_button.clicked.connect(self.analyze);self.regenerate_button.clicked.connect(self.regenerate);self.load_button.clicked.connect(self.load_existing)
        self.pdf_button.clicked.connect(lambda:self.open_file(False));self.folder_button.clicked.connect(lambda:self.open_file(True))
        for b in (self.run_button,self.load_button,self.regenerate_button,self.pdf_button,self.folder_button,self.save_key_button,self.delete_key_button):b.setAutoDefault(False)
        refs=list(self.pair[1].metadata.get('ai_tasks',[]))
        if self.record_folder is not None:
            try:
                index=json.loads((self.record_folder/'ai_index.json').read_text('utf-8'))
                if index['identity']==self.identity:refs.extend(index['tasks'])
            except Exception:pass
        for path in reversed(refs):
            try:
                meta,_=load_display_task(path)
                if meta['identity']==self.identity:
                    self.task=Path(path)
                    self.status.setText('已加载已有AI记录 '+self.task.name+'；本次未调用云端。')
                    break
            except Exception:pass
        self.refresh()
    def refresh(self):
        for w in (self.key,self.base,self.remember,self.save_key_button,self.delete_key_button,self.run_button,self.load_button,self.recover_button,self.review_choice):w.setEnabled(not self.busy)
        self.regenerate_button.setEnabled(not self.busy and self.task is not None)
        valid=False
        if self.task:
            try:
                meta,_=load_display_task(self.task)
                from hashlib import sha256
                pdf=self.task/'analysis.pdf'
                valid=meta.get('pdf_complete') and pdf.is_file() and sha256(pdf.read_bytes()).hexdigest()==meta.get('pdf_sha256')
            except Exception:pass
        self.pdf_button.setEnabled(not self.busy and bool(valid));self.folder_button.setEnabled(not self.busy and self.task is not None)
    def save_key(self):
        if not self.remember.isChecked():self.credential_status.setText('请先勾选“在本机安全保存”；未写入凭据。');return
        try:
            self.store.save(self.key.text());self.parent().ai_session_key=self.key.text().strip()
            self.credential_status.setText('已保存/更换Windows凭据；保存密钥不会调用云端。')
        except Exception:self.credential_status.setText('Windows凭据保存失败；未写明文配置。')
    def delete_key(self):
        try:
            self.store.delete();self.key.clear();self.parent().ai_session_key=''
            self.credential_status.setText('本机已保存凭据及当前窗口密钥已删除；不会调用云端。')
        except Exception:self.credential_status.setText('Windows凭据删除失败，请稍后重试。')
    def analyze(self):
        if self.busy or self._closing:return
        key=self.key.text().strip();base=self.base.text()
        if not key:self.status.setText('请填写API Key；未发起请求。');return
        pair=self.pair;folder=self.record_folder;review=self.review_choice.currentData();historical=self.historical
        self._pending_cloud=dict(key=key,base=base,historical=historical)
        def action(cancel,progress):
            if folder is None:
                root=default_output()/'ai_records';root.mkdir(parents=True,exist_ok=True);record=save_result(root,*pair)
            else:record=folder
            prepared=bundle(record,analysis_context='historical_record' if historical else 'current_result',include_analysis=True,review=review)
            return dict(record=record,prepared=prepared)
        self.start_job('prepare_cloud',action)
    def recover_response(self):
        if self.busy:return
        path=QFileDialog.getExistingDirectory(self,'选择含response.json的原失败AI任务目录')
        if not path:return
        source=self.record_folder
        if source is None:
            source=QFileDialog.getExistingDirectory(self,'选择对应完整四窗原记录（record.json）')
            if not source:return
        self.status.setText('离线核对来源并生成新草稿任务；不读取Key、不访问云端。')
        identity=self.identity
        self.start_job('recovery',lambda cancel,_:recover_saved_response(path,source,cancel=cancel,expected_identity=identity))

    def regenerate(self):
        if self.task and not self.busy:
            task=self.task;attachments=self.pdf_attachments.isChecked()
            self.start_job('pdf',lambda cancel,_:regenerate_pdf(task,cancel=cancel,include_attachments=attachments))
    def load_existing(self):
        path=QFileDialog.getExistingDirectory(self,'选择已保存AI任务目录')
        if not path:return
        def action(*_):
            meta,_=load_display_task(path)
            if meta['identity']!=self.identity:raise ValueError('此AI记录属于另一帧，请先打开对应图像记录')
            return dict(task=path,meta=meta)
        self.start_job('load',action)
    def completed(self,kind,value,error):
        if error:self._pending_cloud=None;self.status.setText('任务未完成；原分割保留，未自动重试。'+error);return
        if kind=='prepare_cloud':
            if self._closing:self._pending_cloud=None;return
            self.record_folder=Path(value['record']);self.parent().bind_analysis_record(self.record_folder,self.identity)
            prepared=value['prepared'];analyses=prepared['facts']['analyses'];manual=analyses['manual'];pending=self._pending_cloud
            text=('本次上传：实际256模型RGB、原始二值Mask，以及对应分类、质量和修订数据；如有改动的人工版本，再上传该版本Mask。审核文字可能上传，不含复核人姓名；不上传高分辨率原图或展示增强图。\n'
                  f"帧：{self.identity['frame_id']}；ROI：{self.identity['roi']}\n整图分类：{analyses['classification']['status']}；复核版本：{manual.get('review_id','原始结果')}\n"
                  f"图像数量：{3 if 'revision_png' in prepared else 2}；云端模型：{MODEL}。\n按量计费，AI内容待人工复核。确认本次上传及可能产生的费用？")
            if QMessageBox.question(self,'确认上传与计费',text,QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:
                self._pending_cloud=None;self.status.setText('已取消上传；本地原记录保留，未调用云端。');return
            self.task=None
            if self.remember.isChecked():self.save_key()
            self.parent().ai_session_key=pending['key'];self._pending_cloud=None;record=self.record_folder
            self.start_job('cloud',lambda cancel,progress:run_analysis(record,pending['base'],pending['key'],prepared=prepared,
                historical=pending['historical'],cancel=cancel,progress=progress));return
        if value.get('preparation_error'):
            self.status.setText('本地准备或接口配置无效；原分割保留，未自动重试。');return
        self.task=Path(value['task']);meta=value['meta'];self.record_folder=Path(meta['source_record'])
        try:
            parent=self.parent()
            if parent.result is not None and result_identity((parent.request,parent.result))==self.identity:
                refs=parent.result.metadata.setdefault('ai_tasks',[])
                if str(self.task) not in refs:refs.append(str(self.task))
        except Exception:pass
        # This sidecar belongs to the saved source record; original record.json stays untouched.
        from .device.formal_session import write_json
        try:write_json(self.record_folder/'ai_index.json',dict(identity=self.identity,tasks=[str(p) for p in sorted((self.record_folder/'ai').glob('analysis_*'))]))
        except Exception:pass
        if not meta.get('cloud_complete'):
            self.status.setText((meta.get('error') or 'AI未完成')+('；基础统一报告已生成，原结果保留。' if meta.get('pdf_complete') else '；基础结果保留，可单独导出统一报告。'))
        else:self.status.setText(('AI草稿统一报告已生成，待人工复核；本地记录是数值来源。' if meta.get('content_policy_version') else '报告已生成，可打开PDF。') if meta.get('pdf_complete') else meta.get('pdf_error') or meta.get('error') or 'AI结果可读取；可仅重新生成PDF。')
    def open_file(self,folder):
        if self.task and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.task if folder else self.task/'analysis.pdf'))):self.status.setText('无法打开，请在文件管理器查看：'+str(self.task))
    def closeEvent(self,event):
        self.key.clear();self._pending_cloud=None;super().closeEvent(event)
