"""Select a saved revision and export one all-analysis report without cloud calls."""
from pathlib import Path
import json
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,QCheckBox
from .analysis_ui import AnalysisDialog
from .analysis_identity import frozen_pair,result_identity
from .runtime_paths import default_output
from .analysis_snapshot import build_snapshot,save_selection


class ReportDialog(AnalysisDialog):
    def __init__(self,parent):
        super().__init__(parent);self.setWindowTitle('统一总报告');self.resize(830,370)
        self.pair=frozen_pair((parent.request,parent.result));self.identity=result_identity(self.pair)
        self.folder=None;self.pdf=None;layout=QVBoxLayout(self)
        notice=QLabel('汇总本图原图、实际输入、原始分割、ROI/质量提醒、原始与所选修订测量、分类、匹配版本的AI和人工审核文字。未运行项目明确标注。查看或重导不会调用AI。')
        notice.setWordWrap(True);layout.addWidget(notice)
        self.revision=QComboBox();self.revision.setAccessibleName('统一报告所选人工修订版本');layout.addWidget(self.revision)
        self.attachments=QCheckBox('附展示增强与ROI裁剪细节');layout.addWidget(self.attachments)
        self.details=QLabel('正在核对对应原记录。');self.details.setWordWrap(True);layout.addWidget(self.details)
        row=QHBoxLayout();self.generate=QPushButton('生成 / 更新统一总报告');self.generate.setProperty('fluentAppearance','primary')
        self.open_pdf=QPushButton('打开PDF');self.open_directory=QPushButton('打开目录')
        for b in (self.generate,self.open_pdf,self.open_directory):b.setAutoDefault(False);row.addWidget(b)
        layout.addLayout(row);self.status=QLabel('准备过程不调用FPGA或云端。');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.generate.clicked.connect(self.export);self.open_pdf.clicked.connect(lambda:self.open_file(False));self.open_directory.clicked.connect(lambda:self.open_file(True))
        self.revision.currentIndexChanged.connect(self.selection_changed)
        source=parent.analysis_record();pair=self.pair;identity=self.identity
        self.historical=parent.opened_record is not None or identity['display_mode']=='offline_replay'
        def action(cancel,_):
            from .records import load_record
            from .storage import save_result
            folder=None
            if source:
                r=load_record(Path(source))
                if result_identity((r.request,r.result))==identity:folder=Path(source)
            if folder is None:
                root=default_output()/'report_records';root.mkdir(parents=True,exist_ok=True);folder=save_result(root,*pair)
            value=build_snapshot(folder);available=[]
            from .review import load_review
            for candidate in sorted((folder/'reviews').glob('review_*')):
                try:
                    d=load_review(candidate)['document']
                    if d['identity']==identity:available.append((d['saved_at']+' · '+d['review_id']+' · 修改'+str(d['changed_pixels'])+'px',candidate.relative_to(folder).as_posix()))
                except Exception:pass
            value['available_reviews']=available;return value
        self.start_job('prepare',action)

    def refresh(self):
        self.revision.setEnabled(not self.busy and self.folder is not None)
        self.generate.setEnabled(not self.busy and self.folder is not None)
        from .unified_report import valid_report
        self.open_pdf.setEnabled(not self.busy and self.pdf is not None and valid_report(self.pdf))
        self.open_directory.setEnabled(not self.busy and self.pdf is not None)

    def selection_changed(self,*_):
        self.pdf=None
        if hasattr(self,'generate'):self.refresh()

    def export(self):
        if self.busy or self.folder is None:return
        folder=self.folder;selection=self.revision.currentData();attachments=self.attachments.isChecked();historical=self.historical
        def action(cancel,_):
            from .unified_report import export_single
            save_selection(folder,selection)
            return export_single(folder,review=selection,cancel=cancel,include_attachments=attachments,historical=historical)
        self.start_job('pdf',action)

    def completed(self,kind,value,error):
        if error:self.status.setText('报告导出失败：'+error+'；原分割、分类和复核记录保留。');return
        if kind=='prepare':
            self.folder=value['folder'];selected=value['context']['manual'].get('selection')
            self.parent().bind_analysis_record(self.folder,self.identity)
            self.revision.addItem('原始FPGA结果；人工修订不纳入',None)
            for text,relative in value['available_reviews']:self.revision.addItem(text,relative)
            if selected:self.revision.setCurrentIndex(max(0,self.revision.findData(selected)))
            c=value['context'];self.details.setText('帧 '+str(self.identity['frame_id'])+'；ROI '+str(self.identity['roi'] or '整图')+
                '\n分类：'+c['classification']['status']+'；AI：'+value['ai']['status']+'；仅采用对应版本数据。')
            self.status.setText('就绪。选择修订版本后生成本地统一报告，无需API Key。')
        else:self.pdf=Path(value['pdf']);self.status.setText('统一总报告已生成，共'+str(value['pages'])+'页；未调用云端。')

    def open_file(self,folder):
        from .unified_report import valid_report
        if not folder and self.pdf and not valid_report(self.pdf):self.status.setText('报告缺失或摘要不符，请重新生成。');self.refresh();return
        if self.pdf and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.pdf.parent if folder else self.pdf))):self.status.setText('无法打开，请在文件管理器查看：'+str(self.pdf))
