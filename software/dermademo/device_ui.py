"""Window-modal software communication lab; leaves main processing state intact."""

from pathlib import Path
from threading import Event

import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout)

from ._fluent.style import set_fluent_property
from .device.diagnostics import save_lab_result
from .device.imaging import diagnostic_pattern, prepare_lab_input
from .device.lab import LabMode, LabRequest, run_loopback
from .device.transport import TransferCancelled
from .diagnostic_logging import logged_task


class LabWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str, bool)

    def __init__(self, action, parent):
        super().__init__(parent)
        self.action = action
        self.cancel = Event()

    def run(self):
        try:
            with logged_task("communication_lab"):
                value = self.action(self.cancel)
            self.succeeded.emit(value)
        except TransferCancelled as exc:
            self.failed.emit(str(exc), True)
        except Exception as exc:
            self.failed.emit(str(exc), False)


class DeviceDialog(QDialog):
    def __init__(self, parent, image=None):
        super().__init__(parent)
        from .gui import ImageView  # Reuse the existing pan/zoom display after GUI module initialization.

        self.setWindowTitle("本机通信实验")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(820, 680)
        self.setMinimumSize(680, 560)
        self._image = None if image is None else image.copy()
        if self._image is not None:
            self._image.setflags(write=False)
        self._thread = None
        self._closing = False
        self._job_kind = None
        self._completion = None
        self.result = None
        self.last_export = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)
        notice = QLabel("协议仍是草案。此处只运行本机软件收发；真实 FPGA 接入待队友提供已验证的协议和程序。")
        notice.setObjectName("algorithmNotice")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        form = QFormLayout()
        self.source_combo = QComboBox()
        self.source_combo.addItem("非对称行列诊断图", "pattern")
        self.source_combo.addItem("打开窗口时的原图快照（不增强）", "snapshot")
        self.source_combo.model().item(1).setEnabled(self._image is not None)
        self.source_combo.setAccessibleName("通信测试输入")
        self.mode_combo = QComboBox()
        for caption, mode in (("RGB 原样旁路 · 软件实验", LabMode.RGB_BYPASS),
                              ("3×3 高斯旁路 · 软件实验", LabMode.GAUSSIAN),
                              ("固定方块 Mask · 协议夹具，非分割模型", LabMode.MASK_FIXTURE)):
            self.mode_combo.addItem(caption, mode)
        self.mode_combo.setAccessibleName("本机通信测试模式")
        self.geometry_combo = QComboBox()
        self.geometry_combo.addItem("拉伸到 256×256 · 实验规则", "stretch")
        self.geometry_combo.addItem("等比补边到 256×256 · 实验规则", "letterbox")
        self.geometry_combo.setAccessibleName("实验缩放规则")
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(0.1, 30)
        self.timeout_spin.setValue(5)
        self.timeout_spin.setSuffix(" 秒")
        self.timeout_spin.setAccessibleName("本机通信总期限")
        form.addRow("输入", self.source_combo)
        form.addRow("模式", self.mode_combo)
        form.addRow("原图缩放", self.geometry_combo)
        form.addRow("事务总期限", self.timeout_spin)
        layout.addLayout(form)
        previews = QHBoxLayout()
        self.input_view = ImageView("测试完成后显示发送的 RGB888")
        self.output_view = ImageView("测试完成后显示本机返回数据")
        self.input_view.setAccessibleName("本机测试输入 RGB")
        self.output_view.setAccessibleName("本机测试返回数据")
        for caption, view in (("发送 · 256×256 RGB", self.input_view), ("返回 · 软件实验", self.output_view)):
            column = QVBoxLayout()
            label = QLabel(caption)
            label.setObjectName("panelTitle")
            column.addWidget(label)
            column.addWidget(view, 1)
            previews.addLayout(column, 1)
            view.double_clicked.connect(view.fit_image)
        layout.addLayout(previews, 1)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(150)
        self.detail.setAccessibleName("通信诊断详情")
        self.detail.setPlainText("尚未运行。无连接、无 FPGA 模型结果。\n切换选项只修改待运行配置，按 Enter 后才更新预览。")
        layout.addWidget(self.detail)
        self.status = QLabel("就绪 · Enter 运行本机测试，Ctrl+S 导出最近一次成功的诊断")
        self.status.setObjectName("statusMessage")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        self.run_button = QPushButton("运行本机测试")
        self.run_button.setProperty("fluentAppearance", "primary")
        self.cancel_button = QPushButton("取消测试")
        self.save_button = QPushButton("导出诊断")
        self.close_button = QPushButton("关闭")
        for button in (self.run_button, self.cancel_button, self.save_button, self.close_button):
            button.setAutoDefault(False)
            row.addWidget(button)
        layout.addLayout(row)
        self.run_button.clicked.connect(self.start_test)
        self.cancel_button.clicked.connect(self.cancel_test)
        self.save_button.clicked.connect(self.choose_export)
        self.close_button.clicked.connect(self.close)
        self.source_combo.currentIndexChanged.connect(self._refresh_controls)
        for sequence, callback in ((Qt.Key.Key_Return, self.run_button.click),
                                   (Qt.Key.Key_Enter, self.run_button.click), ("Ctrl+S", self.save_button.click)):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(callback)
        self._refresh_controls()

    @property
    def busy(self):
        return self._thread is not None

    def _refresh_controls(self, *_args):
        idle = not self.busy and not self._closing
        self.run_button.setEnabled(idle)
        self.source_combo.setEnabled(idle)
        self.mode_combo.setEnabled(idle)
        self.geometry_combo.setEnabled(idle and self.source_combo.currentData() == "snapshot")
        self.timeout_spin.setEnabled(idle)
        self.cancel_button.setEnabled(self.busy and self._job_kind == "test" and not self._thread.cancel.is_set())
        self.save_button.setEnabled(idle and self.result is not None)
        self.run_button.setText("正在测试…" if self.busy and self._job_kind == "test" else "运行本机测试")
        self.progress.setRange(0, 0 if self.busy else 1)
        if idle:
            self.progress.setValue(1 if self.result is not None else 0)

    def _start_job(self, kind, action):
        if self.busy or self._closing:
            return
        self._job_kind, self._completion = kind, None
        self._thread = LabWorker(action, self)
        self._thread.succeeded.connect(self._success)
        self._thread.failed.connect(self._failure)
        self._thread.finished.connect(self._finished)
        self._refresh_controls()
        self._thread.start()

    @Slot(object)
    def _success(self, value):
        self._completion = (True, value)

    @Slot(str, bool)
    def _failure(self, message, cancelled):
        self._completion = (False, (message, cancelled))

    @Slot()
    def _finished(self):
        thread, kind, completion = self._thread, self._job_kind, self._completion
        self._thread = self._job_kind = None
        thread.deleteLater()
        try:
            if completion is None:
                raise RuntimeError("通信任务结束但未返回状态")
            succeeded, value = completion
            if not succeeded:
                message, cancelled = value
                self.status.setText(("已取消 · " if cancelled else "失败 · ") + message + "；上次成功诊断仍保留。")
                set_fluent_property(self.status, "fluentSeverity", "info" if cancelled else "danger")
            elif kind == "test":
                result = value
                rgb = np.frombuffer(result.request.rgb, np.uint8).reshape(256, 256, 3)
                mask = result.request.mode == LabMode.MASK_FIXTURE
                returned = np.frombuffer(result.returned, np.uint8).reshape((256, 256) if mask else (256, 256, 3))
                input_pixmap = self.input_view.prepare_pixmap(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
                output_pixmap = self.output_view.prepare_pixmap(returned if mask else cv2.cvtColor(returned, cv2.COLOR_RGB2BGR))
                self.input_view.set_pixmap(input_pixmap)
                self.output_view.smooth = not mask
                self.output_view.set_pixmap(output_pixmap)
                self.result = result
                timings = dict(result.timings_ms)
                self.detail.setPlainText(
                    f"来源：protocol_simulator · 软件实验 · 非 FPGA\n模式：{result.request.mode.name}\n"
                    f"端点：{result.endpoint[0]}:{result.endpoint[1]}（已断开）\n"
                    f"会话：{result.session_id} · 帧：{result.frame_id} · 重试：0\n"
                    f"发送 RGB：{len(result.request.rgb)} 字节 · 返回：{len(result.returned)} 字节\n"
                    f"上传含确认：{timings['upload_ms']:.2f} ms · 软件 RUN 往返：{timings['run_wait_ms']:.2f} ms\n"
                    f"接收含校验：{timings['receive_ms']:.2f} ms · 事务：{timings['transaction_ms']:.2f} ms\n"
                    "以上是本机软件计时，不能作为 FPGA 性能。固定方块 Mask 不代表分割效果。")
                self.status.setText("本机测试通过 · 完整性已校验，连接已关闭；Ctrl+S 可导出诊断。")
                set_fluent_property(self.status, "fluentSeverity", "success")
            else:
                self.last_export = value
                self.status.setText(f"诊断已导出 · {value}")
                set_fluent_property(self.status, "fluentSeverity", "success")
        except Exception as exc:
            self.status.setText(f"显示或导出失败 · {exc}")
            set_fluent_property(self.status, "fluentSeverity", "danger")
        self._refresh_controls()
        if self._closing:
            self.close()

    def start_test(self):
        if self.busy or self._closing:
            return
        self.timeout_spin.interpretText()
        mode, timeout = LabMode(self.mode_combo.currentData()), self.timeout_spin.value()
        image = self._image if self.source_combo.currentData() == "snapshot" else None
        geometry = self.geometry_combo.currentData()
        def action(cancel):
            prepared = prepare_lab_input(image, geometry) if image is not None else None
            request = LabRequest(prepared.rgb if prepared else diagnostic_pattern(), mode, timeout, prepared)
            return run_loopback(request, cancel)
        self.status.setText("正在运行本机收发与完整性测试 · 可取消；未连接真实设备。")
        set_fluent_property(self.status, "fluentSeverity", "info")
        self._start_job("test", action)

    def cancel_test(self):
        if self.busy and self._job_kind == "test":
            self._thread.cancel.set()
            self.status.setText("正在取消本机测试并关闭连接…")
            self._refresh_controls()

    def choose_export(self):
        if self.busy or self.result is None or self._closing:
            return
        parent = QFileDialog.getExistingDirectory(self, "选择本机通信诊断的导出目录")
        if parent:
            self.export_to(Path(parent))

    def export_to(self, parent):
        if self.busy or self.result is None or self._closing:
            return
        snapshot = self.result
        self.status.setText("正在导出最近一次成功诊断 · 不包含待运行选项。")
        self._start_job("export", lambda _cancel: save_lab_result(Path(parent), snapshot))

    def reject(self):
        # Esc must take the same cooperative cleanup path as the title-bar close.
        self.close()

    def closeEvent(self, event):
        if self.busy:
            self._closing = True
            self.cancel_test()
            self._refresh_controls()
            self.status.setText("正在结束后台任务，完成资源清理后自动关闭…")
            event.ignore()
        else:
            event.accept()
