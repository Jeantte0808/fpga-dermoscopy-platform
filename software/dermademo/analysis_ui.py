"""Small common asynchronous dialog lifecycle; Qt widgets stay on GUI thread."""
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QDialog

from .rgb_device_ui import RGBWorker


class AnalysisDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._thread = self._completion = None
        self._closing = False

    @property
    def busy(self):
        return self._thread is not None

    def start_job(self, kind, action):
        if self.busy or self._closing:
            return
        self._job_kind, self._completion = kind, None
        self._thread = RGBWorker(action, self)
        self._thread.succeeded.connect(self._succeeded)
        self._thread.failed.connect(self._failed)
        self._thread.progress.connect(self._progress)
        self._thread.finished.connect(self._finished)
        self.refresh()
        self._thread.start()

    @Slot(object)
    def _succeeded(self, value):
        self._completion = value, None

    @Slot(str)
    def _failed(self, message):
        self._completion = None, message

    @Slot(str, int)
    def _progress(self, stage, count):
        self.status.setText(f'{stage} · {count}')

    @Slot()
    def _finished(self):
        thread, self._thread = self._thread, None
        thread.deleteLater()
        value, error = self._completion or (None, '工作任务未返回结果')
        try:
            self.completed(self._job_kind, value, error)
        except Exception as exc:
            self.status.setText(f'失败 · {exc}；保留上一次成功结果。')
        self.refresh()
        if self._closing:
            self.close()

    def reject(self):
        self.close()

    def closeEvent(self, event):
        self._closing = True
        if self.busy:
            self._thread.cancel.set()
            self.status.setText('正在结束PC任务；不能据此确认板卡已停止。')
            event.ignore()
        else:
            event.accept()
