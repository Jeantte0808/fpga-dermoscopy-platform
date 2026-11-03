"""Fluent-themed native diagnostics UI with a non-blocking export worker."""

import json
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QUrl, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog,
                               QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QMenu)

from ._fluent.style import set_fluent_property
from .diagnostic_bundle import export_diagnostics, version_info
from .diagnostic_logging import service, logged_task, error_for


class DiagnosticWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, directory, images, parent):
        super().__init__(parent)
        self.directory, self.images = directory, images
        self.job_id = __import__("uuid").uuid4().hex

    def run(self):
        try:
            with logged_task("diagnostic_export", self.job_id):
                result = export_diagnostics(self.directory, images=self.images)
            self.completed.emit(result)
        except Exception as exc:
            suffix = error_for(self.job_id)
            self.failed.emit(f"{exc}" + (f" · 错误编号 {suffix}" if suffix else ""))


class DiagnosticsDialog(QDialog):
    def __init__(self, parent, images=None):
        super().__init__(parent)
        self.setWindowTitle("关于与诊断")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(640, 510)
        self._images = images or {}
        self._thread = self._completion = None
        self._closing = False
        self.last_export = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        heading = QLabel("关于与诊断")
        heading.setObjectName("panelTitle")
        layout.addWidget(heading)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("应用版本与日志状态")
        layout.addWidget(self.details, 1)
        self.include_images = QCheckBox("附带当前图片快照（可能包含敏感影像）")
        self.include_images.setEnabled(bool(self._images))
        layout.addWidget(self.include_images)
        self.image_list = QLabel("默认仅导出版本、资源摘要与本会话最近 200 条脱敏事件。")
        self.image_list.setWordWrap(True)
        self.image_list.setObjectName("muted")
        layout.addWidget(self.image_list)
        self.include_images.toggled.connect(self._update_image_list)
        self.status = QLabel("ZIP 只保存到您选择的本地文件夹。")
        self.status.setWordWrap(True)
        self.status.setObjectName("statusMessage")
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.copy_button = QPushButton("复制版本信息")
        self.logs_button = QPushButton("打开日志目录")
        self.export_button = QPushButton("导出诊断 ZIP")
        self.close_button = QPushButton("关闭")
        self.maintenance_button = QPushButton('维护工具')
        self.maintenance_button.setAccessibleName('维护与参考算法工具')
        menu = QMenu(self.maintenance_button)
        for caption, tool in (('通信协议自检（本机模拟）', 'communication'),
                              ('RGB 链路检查', 'rgb'),
                              ('参考图像处理（阈值算法）', 'reference'),
                              ('合成图自检', 'sample')):
            menu.addAction(caption).triggered.connect(lambda _checked=False, key=tool: self.open_tool(key))
        self.maintenance_button.setMenu(menu)
        layout.addWidget(self.maintenance_button)
        for button in (self.copy_button, self.logs_button, self.export_button, self.close_button):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(json.dumps(version_info(), ensure_ascii=False, indent=2)))
        self.logs_button.clicked.connect(self.open_logs)
        self.export_button.clicked.connect(self.choose_export)
        self.close_button.clicked.connect(self.close)
        self.refresh_status()

    @property
    def busy(self):
        return self._thread is not None

    def open_tool(self, tool):
        if self.busy:
            return
        parent = self.parentWidget()
        self.close()
        QTimer.singleShot(0, parent, lambda: parent.open_maintenance_tool(tool))

    def _update_image_list(self, checked):
        self.image_list.setText("将附带：" + "、".join(f"images/{name}.png" for name in sorted(self._images))
                                if checked else "默认仅导出版本、资源摘要与本会话最近 200 条脱敏事件。")

    def refresh_status(self):
        current = service()
        snapshot = current.snapshot() if current else {}
        info = version_info()
        self.details.setPlainText(
            f"皮损影像工作台\n版本：{info['version']}\n"
            f"系统：{info['system']} {info['system_release']} / {info['architecture']}\n"
            f"运行方式：{'独立发布包' if info['frozen'] else 'Python 源码'}\n"
            f"构建提交：{info.get('build', {}).get('git_commit', '源码工作区')}\n"
            f"日志：{'文件记录中' if snapshot.get('persistent') else '仅内存 / 未初始化'}\n"
            f"降级状态：{snapshot.get('storage_error') or '无'}\n"
            f"丢弃事件：{snapshot.get('dropped_events', 0)}\n"
            f"退出未排空：{snapshot.get('flush_incomplete', False)}\n"
            "近期错误编号：" + ("、".join(e['error_id'] for e in snapshot.get('events', []) if 'error_id' in e)[-500:] or "无"))
        self.logs_button.setEnabled(bool(current and current.directory and current.directory.is_dir()))

    def open_logs(self):
        current = service()
        if current and current.directory:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(current.directory)))

    def choose_export(self):
        if self.busy:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择诊断 ZIP 保存文件夹")
        if directory:
            self.export_to(Path(directory))

    def export_to(self, directory):
        if self.busy or self._closing:
            return
        images = self._images if self.include_images.isChecked() else None
        self._completion = None
        self._thread = DiagnosticWorker(directory, images, self)
        self._thread.completed.connect(lambda value: self._capture(True, value))
        self._thread.failed.connect(lambda message: self._capture(False, message))
        self._thread.finished.connect(self._finished)
        self.export_button.setEnabled(False)
        self.maintenance_button.setEnabled(False)
        self.include_images.setEnabled(False)
        self.status.setText("正在导出诊断快照…")
        self._thread.start()

    def _capture(self, success, value):
        self._completion = success, value

    def _finished(self):
        thread, self._thread = self._thread, None
        if thread:
            thread.deleteLater()
        success, value = self._completion or (False, "导出线程未返回结果")
        if success:
            self.last_export = value
        self.status.setText(f"诊断已保存：{value}" if success else f"导出失败：{value}")
        set_fluent_property(self.status, "fluentSeverity", "info" if success else "danger")
        self.export_button.setEnabled(True)
        self.maintenance_button.setEnabled(True)
        self.include_images.setEnabled(bool(self._images))
        self.refresh_status()
        if self._closing:
            self.close()

    def reject(self):
        self.close()

    def closeEvent(self, event):
        if self.busy:
            self._closing = True
            self.export_button.setEnabled(False)
            self.status.setText("正在完成当前导出，完成后关闭。")
            event.ignore()
        else:
            event.accept()
