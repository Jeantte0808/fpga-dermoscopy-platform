"""Native RGB UDP diagnostic window; does not manufacture a segmentation result."""

from pathlib import Path
from threading import Event

import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ._fluent.style import set_fluent_property
from .device.imaging import prepare_lab_input
from .device.rgb_protocol import RGBMode
from .device.rgb_storage import save_rgb_outcome
from .device.rgb_udp import HANDOFF_ROOT, RGBRequest, RGBUDPConfig, run_rgb_udp
from .diagnostic_logging import logged_task


class RGBWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(str, int)

    def __init__(self, action, parent):
        super().__init__(parent)
        self.action, self.cancel = action, Event()

    def run(self):
        try:
            with logged_task("rgb_job"):
                value = self.action(self.cancel, self.progress.emit)
            self.succeeded.emit(value)
        except Exception as exc:
            self.failed.emit(str(exc))


class RGBDeviceDialog(QDialog):
    def __init__(self, parent, image=None):
        super().__init__(parent)
        from .gui import ImageView

        self.setWindowTitle("FPGA RGB UDP 联调")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(760, 600)
        available = parent.screen().availableGeometry()
        self.resize(min(940, int(available.width() * 0.9)), min(800, int(available.height() * 0.9)))
        self._image = None if image is None else image.copy()
        if self._image is not None:
            self._image.setflags(write=False)
        self._thread = self._completion = self._job_kind = None
        self._closing = False
        self.outcome = self.displayed_outcome = self.last_export = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 14)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(10)
        notice = QLabel("RGB 前端联调 · 无 CNN / Mask。按 Enter 发送一帧，板卡自动处理；选项修改后等待再次运行才更新图片。")
        notice.setObjectName("algorithmNotice")
        notice.setWordWrap(True)
        body.addWidget(notice)
        form = QFormLayout()
        self.source_combo = QComboBox()
        self.source_combo.addItem("交接包实测输入 · 原始 RGB 字节", "handoff")
        self.source_combo.addItem("当前原图快照 · 实验缩放，不增强", "snapshot")
        self.source_combo.model().item(1).setEnabled(image is not None)
        self.source_combo.setAccessibleName("RGB 联调输入")
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("RGB 旁路 · 逐字节核对", RGBMode.BYPASS.value)
        self.mode_combo.addItem("可选高斯 · 整数参考核对", RGBMode.GAUSSIAN.value)
        self.mode_combo.setAccessibleName("RGB 前端模式")
        self.geometry_combo = QComboBox()
        self.geometry_combo.addItem("拉伸 256×256 · 非正式模型规则", "stretch")
        self.geometry_combo.addItem("等比补边 256×256 · 非正式模型规则", "letterbox")
        self.geometry_combo.setAccessibleName("RGB 快照缩放规则")
        form.addRow("发送输入", self.source_combo)
        form.addRow("前端模式", self.mode_combo)
        form.addRow("快照缩放", self.geometry_combo)
        body.addLayout(form)
        network = QGridLayout()
        self.bind_edit = QLineEdit("192.168.0.3")
        self.board_edit = QLineEdit("192.168.0.2")
        self.local_port = QSpinBox()
        self.local_port.setRange(1, 65535)
        self.local_port.setValue(6102)
        self.board_port = QSpinBox()
        self.board_port.setRange(1, 65535)
        self.board_port.setValue(5000)
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(0.01, 5)
        self.timeout_spin.setValue(1)
        self.timeout_spin.setSuffix(" 秒")
        self.deadline_spin = QDoubleSpinBox()
        self.deadline_spin.setRange(0.01, 300)
        self.deadline_spin.setValue(30)
        self.deadline_spin.setSuffix(" 秒")
        for row, entries in enumerate((("PC IPv4", self.bind_edit, "本地端口", self.local_port),
                                        ("板卡 IPv4", self.board_edit, "板卡端口", self.board_port),
                                        ("单次超时", self.timeout_spin, "事务总期限", self.deadline_spin))):
            for column, (caption, field) in enumerate(((entries[0], entries[1]), (entries[2], entries[3]))):
                label = QLabel(caption)
                label.setBuddy(field)
                field.setAccessibleName(caption)
                network.addWidget(label, row, column * 2)
                network.addWidget(field, row, column * 2 + 1)
        network.setColumnStretch(1, 1)
        network.setColumnStretch(3, 1)
        body.addLayout(network)
        hint = QLabel("使用交接包位流与有线网络；PC 网卡、临时邻居和防火墙需按交接说明配置。中途取消只停止 PC，板卡可能需按 S0 清理。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        body.addWidget(hint)
        previews = QHBoxLayout()
        self.input_view = ImageView("成功后显示本次发送图")
        self.output_view = ImageView("成功后显示 RGB 返回，不显示 Mask")
        for caption, view in (("发送 · RGB888", self.input_view), ("返回 · RGB888", self.output_view)):
            column = QVBoxLayout()
            label = QLabel(caption)
            label.setObjectName("panelTitle")
            view.setMinimumHeight(150)
            view.setAccessibleName(caption)
            view.double_clicked.connect(view.fit_image)
            column.addWidget(label)
            column.addWidget(view)
            previews.addLayout(column, 1)
        body.addLayout(previews)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(130)
        self.detail.setMaximumHeight(160)
        self.detail.setAccessibleName("RGB 联调诊断报告")
        self.detail.setPlainText("尚未运行。历史实板样例仅作为输入和参考，不代表本次实板已通过。")
        body.addWidget(self.detail)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self.status = QLabel("就绪 · Enter 发送；Ctrl+S 导出最近一次尝试，包括失败诊断。")
        self.status.setObjectName("statusMessage")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 384)
        self.progress.setValue(0)
        self.progress.setAccessibleName("RGB 上传和读取进度")
        outer.addWidget(self.progress)
        row = QHBoxLayout()
        self.run_button = QPushButton("发送一帧 RGB")
        self.run_button.setProperty("fluentAppearance", "primary")
        self.cancel_button = QPushButton("停止等待")
        self.save_button = QPushButton("导出诊断")
        self.save_button.setToolTip("保存最近一次已完成的尝试，包括失败报告；不保存待运行选项。")
        self.close_button = QPushButton("关闭")
        for button in (self.run_button, self.cancel_button, self.save_button, self.close_button):
            button.setAutoDefault(False)
            row.addWidget(button)
        outer.addLayout(row)
        self.run_button.clicked.connect(self.start_transfer)
        self.cancel_button.clicked.connect(self.cancel_transfer)
        self.save_button.clicked.connect(self.choose_export)
        self.close_button.clicked.connect(self.close)
        self.source_combo.currentIndexChanged.connect(self._refresh_controls)
        for sequence, callback in ((Qt.Key.Key_Return, self.run_button.click), (Qt.Key.Key_Enter, self.run_button.click),
                                   ("Ctrl+S", self.save_button.click)):
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
        for field in (self.source_combo, self.mode_combo, self.bind_edit, self.board_edit, self.local_port,
                      self.board_port, self.timeout_spin, self.deadline_spin, self.run_button):
            field.setEnabled(idle)
        self.geometry_combo.setEnabled(idle and self.source_combo.currentData() == "snapshot")
        self.save_button.setEnabled(idle and self.outcome is not None)
        self.cancel_button.setEnabled(self.busy and self._job_kind == "transfer" and not self._thread.cancel.is_set())
        self.run_button.setText("正在收发…" if self.busy and self._job_kind == "transfer" else "发送一帧 RGB")

    def _start_job(self, kind, action):
        self._job_kind, self._completion = kind, None
        self._thread = RGBWorker(action, self)
        self._thread.succeeded.connect(self._success)
        self._thread.failed.connect(self._failure)
        self._thread.progress.connect(self._progress)
        self._thread.finished.connect(self._finished)
        self._refresh_controls()
        self._thread.start()

    @Slot(object)
    def _success(self, value):
        self._completion = True, value

    @Slot(str)
    def _failure(self, error):
        self._completion = False, error

    @Slot(str, int)
    def _progress(self, phase, count):
        self.progress.setValue(count + (192 if phase == "receive" else 0))
        if not self._closing:
            self.status.setText(f"{'上传确认' if phase == 'upload' else '读取结果'} · {count}/192")

    @Slot()
    def _finished(self):
        thread, kind, completion = self._thread, self._job_kind, self._completion
        self._thread = self._job_kind = None
        thread.deleteLater()
        try:
            if completion is None:
                raise RuntimeError("RGB 任务未返回状态")
            success, value = completion
            if not success:
                raise RuntimeError(value)
            if kind == "export":
                self.last_export = value
                self.status.setText(f"诊断已导出 · {value}")
                set_fluent_property(self.status, "fluentSeverity", "info")
            else:
                self.outcome = value
                report = value.report
                self.detail.setPlainText(
                    f"来源：{report['origin']} · profile：{report['protocol_profile']}\n"
                    f"模式：{report['mode']} · frame_id：{report['frame_id']} · 状态：{report['status']}\n"
                    f"板卡：{value.config.board_ip}:{value.config.board_port} · PC：{report['actual_local_endpoint']}\n"
                    f"输入确认：{report['input_packets']}/192 · 结果块：{report['output_packets']}/192\n"
                    f"重传：{report['retransmissions']} · BUSY：{report['busy_polls']} · 忽略：{report['ignored_packets']}\n"
                    f"事务耗时：{report['transaction_ms']:.2f} ms（含 PC、网络和等待，非纯 FPGA 计算）\n"
                    f"整帧 CRC：{report.get('output_crc32', '不可用')} · 差异字节：{report.get('nonzero_bytes', '不可用')}\n"
                    f"错误：{report['error'] or '无'}\n无模型 / Mask；无设备身份握手证明。")
                if value.complete:
                    rgb = np.frombuffer(value.request.rgb, np.uint8).reshape(256, 256, 3)
                    returned = np.frombuffer(value.returned, np.uint8).reshape(256, 256, 3)
                    incoming = self.input_view.prepare_pixmap(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
                    outgoing = self.output_view.prepare_pixmap(cv2.cvtColor(returned, cv2.COLOR_RGB2BGR))
                    self.input_view.set_pixmap(incoming)
                    self.output_view.set_pixmap(outgoing)
                    self.displayed_outcome = value
                    self.progress.setValue(384)
                    self.status.setText("RGB 数据验证通过 · 192 输入 / 192 输出、CRC 与逐字节参考一致；无 CNN / Mask。")
                    set_fluent_property(self.status, "fluentSeverity", "info")
                else:
                    recovery = " 板卡可能需按 S0 清理后再试。" if report["manual_reset_may_be_needed"] else ""
                    self.status.setText(f"{report['status']} · {report['error']}。保留上次成功图片，可导出失败诊断。{recovery}")
                    set_fluent_property(self.status, "fluentSeverity", "info" if report["status"] == "cancelled" else "danger")
        except Exception as exc:
            self.status.setText(f"失败 · {exc}；上次成功图片保留。")
            set_fluent_property(self.status, "fluentSeverity", "danger")
        self._refresh_controls()
        if self._closing:
            self.close()

    def start_transfer(self):
        if self.busy or self._closing:
            return
        try:
            for spin in (self.local_port, self.board_port, self.timeout_spin, self.deadline_spin):
                spin.interpretText()
            config = RGBUDPConfig(self.bind_edit.text().strip(), self.local_port.value(), self.board_edit.text().strip(),
                                  self.board_port.value(), self.timeout_spin.value(), self.deadline_spin.value())
        except ValueError as exc:
            self.status.setText(f"参数无效 · {exc}")
            set_fluent_property(self.status, "fluentSeverity", "danger")
            return
        mode = RGBMode(self.mode_combo.currentData())
        image = self._image if self.source_combo.currentData() == "snapshot" else None
        geometry = self.geometry_combo.currentData()
        def action(cancel, progress):
            prepared = prepare_lab_input(image, geometry) if image is not None else None
            raw = prepared.rgb if prepared else (HANDOFF_ROOT / "IMAGE.RAW").read_bytes()
            return run_rgb_udp(RGBRequest(raw, mode, "workbench-snapshot" if prepared else "handoff-raw", prepared),
                               config, cancel, progress)
        self.status.setText("正在发送 RGB · 参数已冻结，无单独 START 命令。")
        set_fluent_property(self.status, "fluentSeverity", "info")
        self.progress.setValue(0)
        self._start_job("transfer", action)

    def cancel_transfer(self):
        if self.busy and self._job_kind == "transfer":
            self._thread.cancel.set()
            self.status.setText("正在停止 PC 收发并关闭 socket · 板卡状态需人工确认。")
            self._refresh_controls()

    def choose_export(self):
        if self.busy or self.outcome is None or self._closing:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择 RGB 诊断导出目录")
        if directory:
            self.export_to(Path(directory))

    def export_to(self, directory):
        if self.busy or self.outcome is None or self._closing:
            return
        outcome = self.outcome
        self.status.setText("正在导出最近一次 RGB 尝试的完整诊断…")
        self._start_job("export", lambda _cancel, _progress: save_rgb_outcome(Path(directory), outcome))

    def reject(self):
        self.close()

    def closeEvent(self, event):
        if self.busy:
            self._closing = True
            self.cancel_transfer()
            self._refresh_controls()
            event.ignore()
        else:
            event.accept()
