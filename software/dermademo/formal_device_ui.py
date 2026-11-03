"""Explicit formal Mask actions; successful results commit to the existing four views."""

from pathlib import Path
from time import perf_counter

import cv2
import numpy as np

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
                               QScrollArea, QWidget)

from ._fluent.style import set_fluent_property
from .device.formal_protocol import assets
from .device.formal_session import FormalConfig, run_formal
from .formal_results import live_pair, replay_pair
from .resources import resource_path
from .rgb_device_ui import RGBWorker
from .runtime_paths import default_output
from .formal_input import prepare_image, verify_preprocessing, resource_summary, EDUCATION_NOTICE
from .hardware import PROFILE_CHOICES, DEPLOYMENT_PROFILE, hardware_declaration


class FormalMaskDialog(QDialog):
    result_ready = Signal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('FPGA 分割')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumWidth(620)
        self._thread = self._completion = None
        self._closing = False
        self.outcome = None
        self.last_pair = None
        self.last_export = None
        self.raw_path = None
        self.prepared_image = None
        self._raw_snapshot = None
        self._job_kind = None
        self.record_directory = None
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        layout.setContentsMargins(20, 18, 20, 18)
        notice = QLabel('确认输入后开始分割，成功结果将更新工作台。\n'
                        '实时推理需要已配置的 FPGA 与有线网络；离线回放不连接设备。\n'
                        + EDUCATION_NOTICE)
        notice.setObjectName('algorithmNotice')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        form = QFormLayout()
        self.form = form
        self.mode_combo = QComboBox()
        self.mode_combo.addItem('离线回放 · 已保存实板记录', 'replay')
        self.mode_combo.addItem('实时推理 · 本次连接 FPGA', 'live')
        self.mode_combo.setAccessibleName('正式 Mask 来源模式')
        form.addRow('运行方式', self.mode_combo)
        self.telemetry_combo = QComboBox()
        self.telemetry_combo.addItem('常规演示 · 3页总计（逐层不可用）', 'summary')
        self.telemetry_combo.addItem('完整诊断 · 249页逐层统计', 'full')
        self.telemetry_combo.setAccessibleName('板内统计采集模式')
        self.telemetry_combo.setToolTip('减少统计查询不代表CNN或图像E2E提速；统计均在FINISH之后查询。')
        form.addRow('性能统计', self.telemetry_combo)
        self.hardware_combo = QComboBox()
        for label, key in PROFILE_CHOICES:
            self.hardware_combo.addItem(label, key)
        self.hardware_combo.setAccessibleName('用户确认实际烧录的硬件版本，非自动识别')
        self.hardware_combo.setToolTip('只改变下一次实时推理的声明，不烧录、不检测位流；回放使用原记录标签。')
        form.addRow('硬件声明', self.hardware_combo)
        self.hardware_combo.setCurrentIndex(self.hardware_combo.findData(DEPLOYMENT_PROFILE))
        form.setRowVisible(self.hardware_combo, False)
        self.source_combo = QComboBox()
        self.source_combo.addItem('设备校验输入 · 原始 RGB888', 'sample')
        self.source_combo.addItem('自选 256×256 RGB888 原始文件', 'raw')
        self.source_combo.addItem('JPEG / PNG 图片', 'image')
        self.source_combo.setAccessibleName('正式模型输入来源')
        form.addRow('模型输入', self.source_combo)
        self.raw_button = QPushButton('选择原始 RGB 文件')
        form.addRow('', self.raw_button)
        self.image_button = QPushButton('选择 JPEG / PNG 并预览实际输入')
        self.image_button.setAccessibleName('选择正式模型原始 JPEG 或 PNG 图片')
        form.addRow('', self.image_button)
        threshold = QLabel('固定整数 logits：q ≥ 1 → 255，否则 0（模型端；不可调）')
        threshold.setWordWrap(True)
        form.addRow('分割阈值', threshold)
        self.record_button = QPushButton('选择实板记录目录（默认随包第一帧）')
        form.addRow('回放记录', self.record_button)
        self.reuse_checkbox = QCheckBox('复用本次上电已验证的模型（首次运行请取消勾选）')
        form.addRow('模型加载', self.reuse_checkbox)
        self.seed_edit = QLineEdit()
        self.seed_edit.setPlaceholderText('可选：旧客户端最新 transaction_highwater.json')
        self.seed_edit.setAccessibleName('事务高水位迁移文件')
        self.seed_button = QPushButton('选择高水位文件')
        seed_row = QHBoxLayout()
        seed_row.addWidget(self.seed_edit, 1)
        seed_row.addWidget(self.seed_button)
        form.addRow('状态迁移', seed_row)
        self.clip_spin = QDoubleSpinBox()
        self.clip_spin.setRange(.5, 8)
        self.clip_spin.setValue(2)
        self.clip_spin.setSingleStep(.5)
        self.clip_spin.setAccessibleName('仅展示的 CLAHE 强度')
        form.addRow('展示增强', self.clip_spin)
        layout.addLayout(form)
        from .gui import ImageView
        self.preview_label = QLabel('实际发送输入 · 256×256 RGB888 · 尚未发送 FPGA')
        self.preview_label.setWordWrap(True)
        self.preview_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.preview_label)
        self.preview = ImageView('选择图片或 RGB 文件，检查实际输入后按 Enter 提交')
        self.preview.setAccessibleName('FPGA 实际输入预览，256乘256 RGB888')
        self.preview.setMinimumHeight(160)
        self.preview.setMaximumHeight(180)
        layout.addWidget(self.preview)
        hint = QLabel('默认端点：PC 192.168.0.3:6102 → FPGA 192.168.0.2:5000\n'
                      '正式位流绑定 PC MAC 10-7C-61-11-AB-09，换电脑需与硬件成员确认。\n'
                      '原图：方向校正→拉伸→固定 JPEG 编解码→RGB；已有 RGB 不再处理。\n'
                      '不做主机归一化或输入增强；CLAHE 仅展示，Mask 不做区域筛选。\n'
                      '停止仅结束 PC 等待，不自动 FINISH、UPDATE、复位或修改网卡。')
        hint.setWordWrap(True)
        hint.setObjectName('muted')
        layout.addWidget(hint)
        resource = self.resource_label = QLabel(resource_summary())
        resource.setObjectName('muted')
        resource.setWordWrap(True)
        layout.addWidget(resource)
        self.status = QLabel('就绪 · Enter 运行所选模式；Ctrl+S 保存最近一次成功的四窗记录。')
        self.status.setWordWrap(True)
        self.status.setObjectName('statusMessage')
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(self.status)
        row = QHBoxLayout()
        self.run_button = QPushButton('加载实板记录')
        self.run_button.setProperty('fluentAppearance', 'primary')
        self.cancel_button = QPushButton('停止等待')
        self.save_button = QPushButton('保存四窗结果')
        self.close_button = QPushButton('关闭')
        for button in (self.run_button, self.cancel_button, self.save_button, self.close_button):
            button.setAutoDefault(False)
            row.addWidget(button)
        outer.addLayout(row)
        self.run_button.clicked.connect(self.start)
        self.cancel_button.clicked.connect(self.cancel_transfer)
        self.close_button.clicked.connect(self.close)
        self.save_button.clicked.connect(self.choose_export)
        self.raw_button.clicked.connect(self.choose_raw)
        self.image_button.clicked.connect(self.choose_image)
        self.record_button.clicked.connect(self.choose_record)
        self.seed_button.clicked.connect(self.choose_seed)
        self.mode_combo.currentIndexChanged.connect(self._refresh)
        self.hardware_combo.currentIndexChanged.connect(self._refresh)
        self.source_combo.currentIndexChanged.connect(self._source_changed)
        for sequence, callback in ((Qt.Key.Key_Return, self.run_button.click), (Qt.Key.Key_Enter, self.run_button.click),
                                   ('Ctrl+S', self.save_button.click)):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(callback)
        available = self.screen().availableGeometry()
        self.resize(min(800, available.width() - 40), min(860, available.height() - 60))
        self._source_changed()

    @property
    def busy(self):
        return self._thread is not None

    def _refresh(self, *_args):
        idle, live = not self.busy and not self._closing, self.mode_combo.currentData() == 'live'
        for field in (self.mode_combo, self.clip_spin, self.run_button):
            field.setEnabled(idle)
        for field in (self.source_combo, self.reuse_checkbox, self.seed_edit, self.seed_button, self.telemetry_combo, self.hardware_combo):
            field.setEnabled(idle and live)
        self.resource_label.setText(resource_summary(hardware_declaration(self.hardware_combo.currentData()))
                                   if live else '回放沿用原记录硬件声明，不使用当前选项覆盖；成功加载后见工作台资源卡。')
        self.raw_button.setEnabled(idle and live and self.source_combo.currentData() == 'raw')
        self.image_button.setEnabled(idle and live and self.source_combo.currentData() == 'image')
        self.form.setRowVisible(self.raw_button, live and self.source_combo.currentData() == 'raw')
        self.form.setRowVisible(self.image_button, live and self.source_combo.currentData() == 'image')
        self.form.setRowVisible(self.record_button, not live)
        self.preview.setVisible(live)
        self.preview_label.setVisible(live)
        self.record_button.setEnabled(idle and not live)
        self.cancel_button.setEnabled(self.busy and not self._thread.cancel.is_set())
        self.cancel_button.setText('停止预处理' if self._job_kind in ('prepare_image', 'prepare_raw') else '停止等待')
        self.save_button.setEnabled(idle and self.last_pair is not None)
        self.run_button.setText('正在处理…' if self.busy else '发送正式模型输入' if live else '加载实板记录')

    def _set_preview(self, rgb, label):
        from .gui import ImageView
        from .review import quality_bytes,quality_message
        bgr = cv2.cvtColor(np.frombuffer(rgb, np.uint8).reshape(256, 256, 3), cv2.COLOR_RGB2BGR)
        self.preview.set_pixmap(ImageView.prepare_pixmap(bgr))
        self.preview_label.setText(label + '\n实际发送输入 · 256×256 RGB888 · 尚未发送 FPGA\n输入提醒：'+quality_message(quality_bytes(rgb)))
        self.preview_label.setWordWrap(True)
        self.parent().show_input_quality(rgb,label,pending=True)

    def _source_changed(self, *_args):
        source = self.source_combo.currentData()
        if source == 'sample':
            self._set_preview((resource_path('formal_root') / 'assets/sample.rgb').read_bytes(), '随包公开 ISIC 固定样例')
        elif source == 'image' and self.prepared_image is not None:
            self._set_preview(self.prepared_image.rgb, self.prepared_image.path.name)
        elif source == 'raw' and self._raw_snapshot is not None:
            self._set_preview(self._raw_snapshot, self.raw_path.name)
        else:
            self.preview.clear_image()
            self.preview_label.setText('请先选择输入并检查预览；主界面保留上一次成功结果。')
        self._refresh()

    def choose_image(self):
        name, _ = QFileDialog.getOpenFileName(self, '选择正式模型原图（8位不透明 RGB JPEG / PNG）', '',
                                             '图片 (*.jpg *.jpeg *.png);;所有文件 (*)')
        if name:
            self.prepare_selected_image(Path(name))

    def prepare_selected_image(self, path):
        if self.busy or self._closing:
            return
        self.parent().input_quality_banner.setText('待处理输入 · '+Path(path).name+'\n正在准备正式256输入并检查质量；尚未发送FPGA。')
        self.parent().input_quality_banner.show()
        self.prepared_image = None
        self._source_changed()
        def action(cancel, progress):
            verify_preprocessing()
            if cancel.is_set():
                raise InterruptedError('已停止预处理')
            prepared = prepare_image(path)
            if cancel.is_set():
                raise InterruptedError('已停止预处理')
            return prepared
        self.status.setText('正在校验预处理金标准并生成实际输入；尚未连接 FPGA。')
        self._start_job(action, kind='prepare_image')

    def choose_raw(self):
        name, _ = QFileDialog.getOpenFileName(self, '选择紧密 RGB888（196608 字节）', '', 'RGB 原始文件 (*.rgb *.raw);;所有文件 (*)')
        if name:
            self.raw_path = Path(name)
            self._raw_snapshot = None
            path = self.raw_path
            def action(cancel, progress):
                raw = path.read_bytes()
                if len(raw) != 196608:
                    raise ValueError('RGB888 必须为 196608 字节')
                if cancel.is_set():
                    raise InterruptedError('已停止预览')
                return raw
            self._source_changed()
            self._start_job(action, kind='prepare_raw')

    def choose_record(self):
        name = QFileDialog.getExistingDirectory(self, '选择包含 report.json / profile.json / input.rgb / mask.raw 的目录')
        if name:
            self.record_directory = Path(name)
            self.record_button.setText(self.record_directory.name)

    def choose_seed(self):
        name, _ = QFileDialog.getOpenFileName(self, '选择旧客户端最新高水位', '', 'JSON (*.json)')
        if name:
            self.seed_edit.setText(name)

    def start(self):
        if self.busy or self._closing:
            return
        live = self.mode_combo.currentData() == 'live'
        clip, folder = self.clip_spin.value(), self.record_directory
        source = self.source_combo.currentData()
        prepared = self.prepared_image if source == 'image' else None
        raw_snapshot = self._raw_snapshot
        if live and source == 'raw' and raw_snapshot is None:
            self.status.setText('请先选择 196608 字节的原始 RGB 文件；不会自动转换当前图片。')
            return
        if live and source == 'image' and prepared is None:
            self.status.setText('请先选择支持的 JPEG / PNG 并检查实际输入预览，再按 Enter 处理。')
            return
        config = FormalConfig(reuse_model=self.reuse_checkbox.isChecked(), telemetry_mode=self.telemetry_combo.currentData(),
                              hardware_profile=DEPLOYMENT_PROFILE)
        seed = Path(self.seed_edit.text().strip()) if self.seed_edit.text().strip() else None
        def action(cancel, progress):
            if not live:
                return replay_pair(folder, clip), None
            _manifest, _blob, sample = assets()
            raw = prepared.rgb if prepared else raw_snapshot if source == 'raw' else sample
            extra = {'input_context': prepared.context, 'source_bytes': prepared.source_bytes,
                     'cpu_preprocessing_ms': prepared.cpu_preprocessing_ms} if prepared else {}
            outcome = run_formal(raw, config, default_output(), source_state=seed, cancel=cancel, progress=progress, **extra)
            return (live_pair(outcome, clip) if outcome.report['complete'] else None), outcome
        self.status.setText('正在连接 FPGA；本次参数已固定。' if live else '正在离线校验实板记录；不建立网络连接。')
        self._start_job(action)

    def _start_job(self, action, *, kind='run'):
        self._action_started = perf_counter()
        self._job_kind = kind
        self._completion = None
        self._thread = RGBWorker(action, self)
        self._thread.succeeded.connect(self._succeeded)
        self._thread.failed.connect(self._failed)
        self._thread.progress.connect(self._progress)
        self._thread.finished.connect(self._finished)
        self._refresh()
        self._thread.start()

    def _set_completion(self, success, value):
        self._completion = success, value

    @Slot(object)
    def _succeeded(self, value):
        self._set_completion(True, value)

    @Slot(str)
    def _failed(self, error):
        self._set_completion(False, error)

    @Slot(str, int)
    def _progress(self, stage, count):
        self.status.setText(f'{stage} · {count}（可停止 PC 等待）')

    @Slot()
    def _finished(self):
        thread = self._thread
        kind, self._job_kind = self._job_kind, None
        self._thread = None
        thread.deleteLater()
        try:
            if self._completion is None or not self._completion[0]:
                raise RuntimeError(self._completion[1] if self._completion else '任务未返回')
            if kind in ('prepare_image', 'prepare_raw'):
                if kind == 'prepare_image':
                    self.prepared_image = self._completion[1]
                    self.image_button.setText('更换图片 · ' + self.prepared_image.path.name)
                else:
                    self._raw_snapshot = self._completion[1]
                    self.raw_button.setText(self.raw_path.name)
                self._source_changed()
                self.status.setText('实际输入预览已就绪；尚未发送 FPGA。按 Enter 提交新帧；主界面图片保持原结果。')
                if self._closing:
                    self.close()
                return
            pair, outcome = self._completion[1]
            self.outcome = outcome
            if pair is None:
                if outcome.report.get('hardware_finish_confirmed'):
                    state = '合法 FINISH 已确认，板卡本帧已释放；请处理本地保存故障，不需因此盲目复位。'
                elif outcome.report.get('manual_recovery_required'):
                    state = '板卡释放状态未确认，请先共同核对状态，勿盲目重发或复位。'
                else:
                    state = '尚无需要恢复板卡的已发送会话；请检查本地配置或保存路径。'
                raise RuntimeError(f"{outcome.report['error']}\n{state}\n失败诊断：{outcome.directory}")
            # Main window prepares display resources before replacing its previous result.
            self.parent().commit_formal_result(pair)
            if pair[1].metadata.get('live_run_performed'):
                pair[1].metadata['user_visible_wait_ms'] = (perf_counter()-self._action_started)*1000
            self.last_pair = pair
            self.result_ready.emit(pair)
            label = '本机协议模拟' if pair[1].metadata['source_kind'] == 'protocol_simulator' else (
                '本次实时结果' if pair[1].metadata['live_run_performed'] else '离线实板记录回放')
            profile_note = ' · 板内统计不可用' if 'fpga_graph' not in pair[1].timings_ms else ''
            self.status.setText(f'{label}已更新主界面四窗{profile_note}；可关闭窗口查看或 Ctrl+S 保存。')
            from .performance import timing_text
            self.status.setText(self.status.text()+'\n'+timing_text(pair[1]))
            set_fluent_property(self.status, 'fluentSeverity', 'info')
        except Exception as exc:
            self.status.setText(f'失败 · {exc}\n保留上一次成功图片；未自动恢复板卡。')
            if kind in ('prepare_image','prepare_raw'):
                self.parent().input_quality_banner.setText('待处理输入预处理失败，质量检查不可用；未发送FPGA，请重新选择图片。')
            set_fluent_property(self.status, 'fluentSeverity', 'danger')
        self._refresh()
        if self._closing:
            self.close()

    def choose_export(self):
        if self.busy or self.last_pair is None:
            return
        directory = QFileDialog.getExistingDirectory(self, '选择四窗结果保存目录')
        if directory:
            # Route through the original asynchronous save path and shortcut semantics.
            self.parent().export_to(Path(directory))
            self.close()

    def cancel_transfer(self):
        if self.busy:
            self._thread.cancel.set()
            self.status.setText('正在停止本地预处理；尚未连接 FPGA。' if self._job_kind in ('prepare_image', 'prepare_raw')
                                else '正在停止 PC 等待；FPGA 可能仍在运行，请共同确认恢复。')
            self._refresh()

    def reject(self):
        self.close()

    def closeEvent(self, event):
        if self.busy:
            self._closing = True
            self.cancel_transfer()
            event.ignore()
        else:
            event.accept()
