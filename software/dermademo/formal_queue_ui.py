"""Explicit real-FPGA multi-image queue with frame-boundary pause and stop."""
from copy import deepcopy
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer, QSignalBlocker, QSize, QUrl
from PySide6.QtGui import QKeySequence, QShortcut, QIcon, QPixmap, QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QVBoxLayout, QProgressBar,QLineEdit)

from .analysis_ui import AnalysisDialog
from .device.formal_session import FormalConfig
from .formal_input import EDUCATION_NOTICE, PreparedImage, verify_preprocessing
from .formal_queue import FormalQueue, freeze_input, read_input, MAX_ITEMS, MAX_ATTEMPTS
from .hardware import PROFILE_CHOICES, DEPLOYMENT_PROFILE
from .runtime_paths import default_output
from .validation_ui import table, append_row


class FormalQueueDialog(AnalysisDialog):
    def __init__(self, parent):
        super().__init__(parent)
        from .gui import ImageView
        self.setWindowTitle('多图FPGA推理与演示')
        self.resize(1040, 790)
        self.setMinimumSize(800, 630)
        self.inputs, self.session, self.last_step = [], None, None
        self.pdf_path = None
        self.workspace = None
        self.output_parent = default_output() / 'formal_queues'
        self.pause_requested = self.stop_requested = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.next_frame)
        layout = QVBoxLayout(self)
        notice = QLabel('真实FPGA逐帧推理：开始后会连接设备；每项四窗记录保存完整才继续。\n'
                        '图片/ROI加入后冻结输入，预览不会发包；每次循环均创建新帧，不播放旧Mask。\n' + EDUCATION_NOTICE)
        notice.setWordWrap(True)
        notice.setObjectName('algorithmNotice')
        layout.addWidget(notice)
        row = QHBoxLayout()
        self.add_button = QPushButton('加入JPEG / PNG')
        self.add_current_button = QPushButton('加入工作台当前整图/ROI输入')
        self.remove_button, self.up_button, self.down_button = QPushButton('移除'), QPushButton('上移'), QPushButton('下移')
        for button in (self.add_button, self.add_current_button, self.remove_button, self.up_button, self.down_button):
            button.setAutoDefault(False)
            row.addWidget(button)
        layout.addLayout(row)
        self.queue_table, self.queue_model = table(self, ['图片（冻结快照）', '输入范围', '状态', '帧号', '图像E2E', '记录/错误'])
        self.queue_table.setAccessibleName('正式FPGA推理队列；双击已完成项打开四窗记录')
        self.queue_table.setIconSize(QSize(40, 40))
        self.queue_table.verticalHeader().setDefaultSectionSize(48)
        layout.addWidget(self.queue_table, 2)
        mid = QHBoxLayout()
        self.preview = ImageView('选择一项查看实际256×256模型输入')
        self.preview.setAccessibleName('队列实际输入预览，尚未发送FPGA')
        self.preview.setMinimumSize(210, 180)
        mid.addWidget(self.preview, 1)
        form = QFormLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem('单轮分析', False)
        self.mode_combo.addItem('循环演示 · 每轮重新推理', True)
        form.addRow('运行方式', self.mode_combo)
        self.dwell_spin = QDoubleSpinBox()
        self.dwell_spin.setRange(0, 60)
        self.dwell_spin.setValue(1)
        self.dwell_spin.setSuffix(' s')
        self.dwell_spin.setAccessibleName('帧间演示停留，不是推理耗时')
        form.addRow('帧间停留（非FPS）', self.dwell_spin)
        self.telemetry_combo = QComboBox()
        self.telemetry_combo.addItem('常规演示 · 3页总计', 'summary')
        self.telemetry_combo.addItem('完整诊断 · 249页逐层', 'full')
        form.addRow('性能统计', self.telemetry_combo)
        from .cpu_runtime import selected_python
        self.classify_checkbox=QCheckBox('分割保存成功后自动整图分类');self.classify_checkbox.setChecked(True)
        form.addRow('PC分类 · 教学候选V2',self.classify_checkbox)
        self.class_python=QLineEdit(selected_python(parent.preferences));self.class_python.setAccessibleName('队列整图分类CPU Python完整路径')
        runtime=QHBoxLayout();runtime.addWidget(self.class_python,1)
        self.class_runtime=QPushButton('保存 / 更换');self.class_runtime.setAutoDefault(False);runtime.addWidget(self.class_runtime)
        self.class_runtime.clicked.connect(lambda:parent.open_cpu_settings(self.class_python.text()))
        parent.cpu_runtime_changed.connect(self.class_python.setText);form.addRow('共享CPU Python',runtime)
        self.hardware_combo = QComboBox()
        for label, key in PROFILE_CHOICES:
            self.hardware_combo.addItem(label, key)
        self.hardware_combo.setAccessibleName('本队列用户声明的硬件版本，非自动识别')
        form.addRow('硬件声明', self.hardware_combo)
        self.hardware_combo.setCurrentIndex(self.hardware_combo.findData(DEPLOYMENT_PROFILE))
        form.setRowVisible(self.hardware_combo, False)
        endpoints = QLabel('PC 192.168.0.3:6102 → FPGA 192.168.0.2:5000\n网卡、PC MAC及当前位流请按已有交接配置。')
        endpoints.setWordWrap(True)
        form.addRow('现有FCP1端点', endpoints)
        self.output_button = QPushButton('选择保存位置')
        self.output_button.setAutoDefault(False)
        form.addRow('逐帧自动保存', self.output_button)
        mid.addLayout(form, 2)
        layout.addLayout(mid, 2)
        self.location = QLabel(str(self.output_parent))
        self.location.setTextFormat(Qt.TextFormat.PlainText)
        self.location.setWordWrap(True)
        layout.addWidget(self.location)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        actions = QHBoxLayout()
        self.run_button = QPushButton('开始真实FPGA队列')
        self.run_button.setProperty('fluentAppearance', 'primary')
        self.pause_button = QPushButton('本帧完成后暂停')
        self.resume_button = QPushButton('继续 / 核对后恢复')
        self.stop_button = QPushButton('本帧完成后停止')
        self.cancel_button = QPushButton('中断PC等待')
        self.new_button = QPushButton('建立新队列')
        for button in (self.run_button, self.pause_button, self.resume_button, self.stop_button, self.cancel_button, self.new_button):
            button.setAutoDefault(False)
            actions.addWidget(button)
        layout.addLayout(actions)
        report_row = QHBoxLayout()
        self.pdf_attachments = QCheckBox('PDF附增强图、Mask及ROI细节')
        layout.addWidget(self.pdf_attachments)
        self.pdf_button = QPushButton('导出本批PDF')
        self.pdf_view_button = QPushButton('查看PDF')
        self.pdf_folder_button = QPushButton('打开所在文件夹')
        self.pdf_history_button = QPushButton('历史批次PDF')
        for button in (self.pdf_button,self.pdf_view_button,self.pdf_folder_button,self.pdf_history_button):
            button.setAutoDefault(False)
            report_row.addWidget(button)
        layout.addLayout(report_row)
        self.pdf_status = QLabel('批次完成后自动生成PDF；失败/部分完成也可导出。')
        self.pdf_status.setWordWrap(True)
        self.pdf_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.pdf_status)
        self.pdf_button.clicked.connect(self.export_pdf)
        self.pdf_history_button.clicked.connect(self.export_history_pdf)
        self.pdf_view_button.clicked.connect(lambda: self.open_pdf(False))
        self.pdf_folder_button.clicked.connect(lambda: self.open_pdf(True))
        self.status = QLabel('就绪 · 未连接设备；首次加载并校验模型，后续成功帧可复用。')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.add_button.clicked.connect(self.choose_inputs)
        self.add_current_button.clicked.connect(self.add_current)
        self.remove_button.clicked.connect(self.remove_selected)
        self.up_button.clicked.connect(lambda: self.move_selected(-1))
        self.down_button.clicked.connect(lambda: self.move_selected(1))
        self.output_button.clicked.connect(self.choose_output)
        self.run_button.clicked.connect(self.start_queue)
        self.pause_button.clicked.connect(self.pause_queue)
        self.resume_button.clicked.connect(self.resume_queue)
        self.stop_button.clicked.connect(self.stop_queue)
        self.cancel_button.clicked.connect(self.cancel_wait)
        self.new_button.clicked.connect(self.new_queue)
        self.queue_table.selectionModel().currentRowChanged.connect(lambda *_: self.preview_selected())
        self.queue_table.doubleClicked.connect(self.open_selected_record)
        for seq, callback in ((Qt.Key.Key_Return, self.start_queue), (Qt.Key.Key_Enter, self.start_queue), ('Ctrl+S', self.save_current)):
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(callback)
        self.refresh()

    def ensure_workspace(self):
        if self.workspace is None:
            self.workspace = self.output_parent / ('queue_' + uuid4().hex)
            self.workspace.mkdir(parents=True, exist_ok=False)
        return self.workspace

    def refresh(self):
        editable = not self.busy and self.session is None
        for control in (self.add_button, self.add_current_button, self.remove_button, self.up_button, self.down_button,
                        self.mode_combo, self.dwell_spin, self.telemetry_combo, self.hardware_combo,
                        self.classify_checkbox,self.class_python,self.class_runtime):
            control.setEnabled(editable)
        self.output_button.setEnabled(editable and not self.inputs)
        self.run_button.setEnabled(editable and bool(self.inputs))
        running = self.session is not None and self.session.status in ('ready', 'running')
        self.pause_button.setEnabled(running and not self.pause_requested and not self.stop_requested)
        self.stop_button.setEnabled(running or (self.session is not None and self.session.status in ('paused', 'failed')))
        self.resume_button.setEnabled(not self.busy and self.session is not None and self.session.status in ('paused', 'failed'))
        self.cancel_button.setEnabled(self.busy and self._job_kind == 'frame')
        self.pdf_button.setEnabled(not self.busy and self.session is not None and self.session.status not in ('ready','running') and not self.timer.isActive())
        self.pdf_history_button.setEnabled(not self.busy and not self.timer.isActive() and not running)
        self.pdf_view_button.setEnabled(not self.busy and self.pdf_path is not None)
        self.pdf_folder_button.setEnabled(not self.busy and self.pdf_path is not None)
        self.new_button.setEnabled(not self.busy and (self.session is None or self.session.finished or self.session.status in ('paused', 'failed')))

    def export_pdf(self):
        if self.busy or self._closing or self.session is None or self.timer.isActive(): return
        if self.session.status in ('ready','running'): return
        self._export_pdf(deepcopy(self.session.summary()), self.session.directory/'batch_report.pdf', False)

    def export_history_pdf(self):
        if self.busy or self._closing or self.timer.isActive(): return
        name,_=QFileDialog.getOpenFileName(self,'选择历史队列索引','','队列索引 (queue.json)')
        if not name: return
        target,_=QFileDialog.getSaveFileName(self,'另存历史批次PDF',str(Path(name).parent/'historical_batch_report.pdf'),'PDF (*.pdf)')
        if not target: return
        attachments=self.pdf_attachments.isChecked()
        def action(cancel,_progress):
            path=Path(name)
            if path.stat().st_size>32*1024*1024: raise ValueError('队列索引过大')
            data=json.loads(path.read_text('utf-8'))
            if data.get('schema')!='alienhome.formal_queue.v1': raise ValueError('不支持的批次索引')
            from .batch_pdf import export_batch_pdf
            return export_batch_pdf(data,target,historical=True,cancel=cancel,include_attachments=attachments,unified=True)
        self.start_job('pdf',action)

    def _export_pdf(self, snapshot, target, historical):
        from .batch_pdf import export_batch_pdf
        self.pdf_path = None
        self.pdf_status.setText('正在生成PDF；已保存的推理结果继续保留。')
        attachments=self.pdf_attachments.isChecked()
        session=self.session
        def action(cancel,_progress):
            if session and session.finished:session.close_classifier()
            return export_batch_pdf(snapshot,target,historical=historical,cancel=cancel,include_attachments=attachments,unified=True)
        self.start_job('pdf',action)

    def schedule_pdf(self):
        if not self._closing:
            QTimer.singleShot(0,self.export_pdf)

    def open_pdf(self, folder):
        if self.pdf_path and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.pdf_path.parent if folder else self.pdf_path))):
            self.pdf_status.setText('无法打开，请从文件管理器查看：'+str(self.pdf_path))

    def choose_output(self):
        if self.busy or self.inputs or self.session:
            return
        name = QFileDialog.getExistingDirectory(self, '选择队列自动保存父目录')
        if name:
            self.output_parent, self.workspace = Path(name), None
            self.location.setText(name)

    def choose_inputs(self):
        if self.busy or self.session:
            return
        names, _ = QFileDialog.getOpenFileNames(self, '按选择顺序加入JPEG/PNG', '', '图片 (*.jpg *.jpeg *.png)')
        if names:
            self.add_paths(names)

    def add_paths(self, names):
        if self.busy or self.session:
            return
        if len(self.inputs) + len(names) > MAX_ITEMS:
            self.status.setText('每个队列最多256个输入项。')
            return
        workspace = self.ensure_workspace()
        def action(cancel, progress):
            verify_preprocessing()
            added, failures = [], []
            for name in names:
                if cancel.is_set():
                    break
                progress('冻结正式预处理输入', len(added))
                try:
                    added.append(freeze_input(name, workspace))
                except Exception as exc:
                    failures.append(f'{Path(name).name}: {exc}')
            return added, failures
        self.start_job('add', action)

    def add_current(self):
        parent = self.parent()
        if self.busy or self.session or len(self.inputs) >= MAX_ITEMS:
            return
        if parent.request is None or parent.result is None or parent.request.source_bytes is None:
            self.status.setText('工作台需要已有正式整图/ROI结果及原始图片，固定RAW样例不能当JPEG/PNG输入。')
            return
        from .analysis_identity import result_identity
        try:
            result_identity((parent.request, parent.result))
            context = parent.result.metadata['input_context']
            prepared = PreparedImage(parent.request.source_path, parent.request.source_bytes,
                                     np.ascontiguousarray(parent.request.image_bgr[:, :, ::-1]).tobytes(), context)
        except Exception as exc:
            self.status.setText(f'当前输入不可用：{exc}')
            return
        workspace = self.ensure_workspace()
        self.start_job('add', lambda *_: ([freeze_input(prepared.path, workspace, prepared=prepared)], []))

    def redraw_inputs(self):
        self.queue_model.removeRows(0, self.queue_model.rowCount())
        for item in self.inputs:
            append_row(self.queue_model, [item.filename, 'ROI ' + str(list(item.roi)) if item.roi else '整图', '排队', '—', '—', item.item_id[:8]])
            index = self.queue_model.rowCount()-1
            # Preview is generated from the immutable actual RGB, not display enhancement.
            self.queue_model.item(index, 0).setIcon(QIcon(str(item.folder/'preview.png')))
            self.queue_model.item(index, 0).setToolTip(item.filename + '\n输入项 ' + item.item_id)

    def selected_index(self):
        index = self.queue_table.currentIndex()
        return index.row() if index.isValid() else -1

    def remove_selected(self):
        index = self.selected_index()
        if not self.busy and self.session is None and index >= 0:
            self.inputs.pop(index)
            self.redraw_inputs()
            self.refresh()

    def move_selected(self, offset):
        index = self.selected_index()
        target = index + offset
        if not self.busy and self.session is None and 0 <= index < len(self.inputs) and 0 <= target < len(self.inputs):
            self.inputs[index], self.inputs[target] = self.inputs[target], self.inputs[index]
            self.redraw_inputs()
            self.queue_table.selectRow(target)

    def preview_selected(self):
        index = self.selected_index()
        if self.busy or not 0 <= index < len(self.inputs) or self.timer.isActive():
            return
        item = self.inputs[index]
        self.start_job('preview', lambda *_: (item.item_id, read_input(item)))

    def start_queue(self):
        if self.busy or self.session or not self.inputs or self._closing:
            return
        config = FormalConfig(telemetry_mode=self.telemetry_combo.currentData(), hardware_profile=DEPLOYMENT_PROFILE)
        try:
            self.session = FormalQueue(self.inputs, config, self.ensure_workspace()/('run_'+uuid4().hex[:8]), loop=self.mode_combo.currentData(),
                classification_enabled=self.classify_checkbox.isChecked(),classification_python=self.class_python.text())
        except Exception as exc:
            self.status.setText(f'建立队列失败，尚未连接设备：{exc}')
            return
        self.pause_requested = self.stop_requested = False
        self.next_frame()

    def next_frame(self):
        if self.busy or self._closing or self.session is None or self.session.status not in ('ready', 'running'):
            return
        if self.stop_requested:
            self.session.stop()
            self.refresh()
            return
        if self.pause_requested:
            self.session.pause()
            self.status.setText('已在帧间暂停；没有提交下一帧。')
            self.refresh()
            return
        if self.parent().busy:
            self.session.pause()
            self.status.setText('工作台仍在保存或读取，已在帧间暂停；完成后可继续。')
            self.refresh()
            return
        self._frame_started = perf_counter()
        self.status.setText('正在提交真实新帧；本帧参数已固定。')
        index = self.session.index
        with QSignalBlocker(self.queue_table.selectionModel()):
            self.queue_table.selectRow(index)
        preview = QPixmap(str(self.inputs[index].folder/'preview.png'))
        if not preview.isNull() and preview.width() == preview.height() == 256:
            self.preview.set_pixmap(preview)
        self.queue_model.item(index, 2).setText('处理中')
        self.start_job('frame', lambda cancel, progress: self.session.step(cancel=cancel, progress=progress))

    def pause_queue(self):
        self.pause_requested = True
        if not self.busy:
            self.timer.stop()
            try:
                self.session.pause()
                self.status.setText('已在帧间暂停。')
            except Exception as exc:
                self.status.setText(f'已暂停后续提交；队列索引保存失败：{exc}')
        else:
            self.status.setText('本帧继续校验与保存，完成后暂停。')
        self.refresh()

    def stop_queue(self):
        self.stop_requested = True
        self.timer.stop()
        if not self.busy and self.session:
            try:
                self.session.stop()
                self.status.setText('已停止后续提交；已完成记录保留。')
                self.schedule_pdf()
            except Exception as exc:
                self.status.setText(f'已停止后续提交；队列索引保存失败：{exc}')
        else:
            self.status.setText('本帧完成后停止，不再提交下一张。')
        self.refresh()

    def resume_queue(self):
        if self.busy or self.session is None:
            return
        recovery = self.session.status == 'failed'
        if recovery:
            row = self.session.rows[-1] if self.session.rows else {}
            state = '合法FINISH已确认，先处理本地文件问题。' if row.get('hardware_finish_confirmed') else '请先与硬件成员确认板卡状态；程序不会自动恢复或复位。'
            answer = QMessageBox.question(self, '确认后恢复', state + '\n是否已完成核对？后续提交使用新帧号并重新加载/校验模型；已保存成功帧不回退重发。',
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            self.session.resume(recovery_confirmed=recovery)
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self.pause_requested = self.stop_requested = False
        if self.session.finished:
            self.status.setText('帧记录均已完成；先前显示观察异常仍保留在队列索引中，可双击记录重开。')
            self.refresh()
            return
        self.next_frame()

    def cancel_wait(self):
        if self.busy and self._job_kind == 'frame':
            self.pause_requested = True
            self._thread.cancel.set()
            self.status.setText('正在中断PC等待；不能确认板卡停止，失败后先核对再恢复。')

    def new_queue(self):
        if self.busy:
            return
        if self.session and self.session._classifier is not None:
            session=self.session;self.start_job('new_cleanup',lambda *_:session.close_classifier());return
        self.timer.stop()
        if self.session and not self.session.finished:
            try:
                self.session.stop()
            except Exception as exc:
                self.status.setText(f'旧队列索引保存失败，请保留原目录排查：{exc}')
                return
        self.inputs, self.session, self.last_step, self.workspace = [], None, None, None
        self.pdf_path = None
        self.pause_requested = self.stop_requested = False
        self.preview.clear_image()
        self.redraw_inputs()
        self.status.setText('已建立空队列，旧目录和记录保留。')
        self.refresh()

    def completed(self, kind, value, error):
        if kind=='new_cleanup':
            if error:self.status.setText('分类进程清理失败：'+error);return
            self.new_queue();return
        if kind=='close_cleanup':return
        if kind == 'pdf':
            if error:
                self.pdf_status.setText('报告导出失败：'+error+'；已有推理结果保留，无需因此复位板卡。')
            else:
                self.pdf_path=Path(value['pdf'])
                self.pdf_status.setText(f"PDF已生成，{value['pages']}页；记录读取异常{len(value['record_errors'])}项：{self.pdf_path}")
                from .device.formal_session import write_json
                try: write_json(self.pdf_path.with_suffix('.pdf.json'),value)
                except Exception as exc: self.pdf_status.setText('PDF已生成，报告索引保存失败：'+str(exc))
            return
        if error:
            self.timer.stop()
            if kind == 'frame' and self.session:
                self.session.status, self.session.error = 'failed', error
            self.status.setText(f'任务失败，队列已暂停：{error}；未自动重发或恢复板卡。')
            return
        if kind == 'add':
            added, failures = value
            self.inputs.extend(added)
            self.redraw_inputs()
            self.location.setText(str(self.workspace))
            self.status.setText(f'已冻结{len(added)}项；尚未发送。' + ('\n未加入：' + '\n'.join(failures) if failures else ''))
            if self.inputs and not self._closing:
                QTimer.singleShot(0, self._select_last)
        elif kind == 'preview':
            item_id, prepared = value
            selected = self.selected_index()
            if selected >= 0 and self.inputs[selected].item_id == item_id:
                self.preview.set_image(cv2.cvtColor(np.frombuffer(prepared.rgb, np.uint8).reshape(256,256,3), cv2.COLOR_RGB2BGR))
                roi = prepared.context.get('roi')
                self.queue_model.item(selected, 1).setText('ROI ' + str(roi['bounds']) if roi else '整图')
        elif kind == 'open':
            self.parent().commit_record(value)
            self.status.setText('已打开历史四窗记录；本次未发送FPGA。')
        elif kind == 'frame':
            self.last_step = value
            index = next(i for i, item in enumerate(self.inputs) if item.item_id == value.item_id)
            row = value.row
            self.queue_model.item(index, 2).setText('成功' if value.success else '失败 · 已暂停')
            classification=row.get('classification',{})
            if value.success and classification.get('status') in ('failed','cancelled'):
                self.queue_model.item(index,2).setText('分割成功 / 分类'+('失败' if classification['status']=='failed' else '取消'))
            elif value.success and classification.get('status')=='success':self.queue_model.item(index,2).setText('分割 / 分类成功')
            self.queue_model.item(index, 3).setText(str(row.get('frame_id') or '—'))
            ms = row.get('image_E2E_ms')
            self.queue_model.item(index, 4).setText(f'{ms:.2f} ms' if ms is not None else '不可用')
            self.queue_model.item(index, 5).setText(row['record_directory'] or row['error'] or '不可用')
            if not value.success:
                self.timer.stop()
                state = '合法FINISH已确认；本地保存/处理失败，与板卡未释放区分。' if row['hardware_finish_confirmed'] else '板卡释放状态未确认，请先核对。' if row.get('manual_recovery_required') else '本地预检失败，尚无待恢复板卡事务。'
                self.status.setText(f"队列已暂停：{row['error']}\n{state}\n诊断：{row['capture_directory'] or self.session.directory}")
                self.schedule_pdf()
                return
            try:
                commit_clock = perf_counter()
                self.parent().commit_formal_result(value.pair)
                self.parent().bind_analysis_record(row['record_directory'],row['identity'])
                commit_ms = (perf_counter()-commit_clock)*1000
                visible_ms = (perf_counter()-self._frame_started)*1000
                value.pair[1].metadata['user_visible_wait_ms'] = visible_ms
                self.session.save_presentation(value, visible_ms=visible_ms, display_commit_ms=commit_ms)
            except Exception as exc:
                message = f'显示或观察记录失败：{exc}'
                try:
                    self.session.note_presentation_failure(message)
                except Exception as persist_error:
                    self.session.error = message + f'；队列索引也未保存：{persist_error}'
                self.status.setText(self.session.error + '；原始帧记录保留，暂停后续任务。')
                return
            count = self.session.summary()['success_count']
            self.progress_bar.setRange(0, len(self.inputs) if not self.session.loop else MAX_ATTEMPTS)
            self.progress_bar.setValue(count)
            self.status.setText(f'已保存{count}帧 · 本帧图像E2E {ms:.2f} ms · 操作至界面提交 {visible_ms:.2f} ms\n'
                                '首轮模型加载、统计、本地保存及显示分别记录；帧间停留不是FPS。')
            if classification.get('status')=='failed':self.status.setText(self.status.text()+'\n本项分类失败，分割记录保留：'+classification.get('error',''))
            if self.stop_requested or self._closing:
                self.session.stop()
                self.schedule_pdf()
            elif self.pause_requested:
                self.session.pause()
                self.status.setText(self.status.text() + '\n已在帧间暂停。')
            elif not self.session.finished:
                self.timer.start(int(self.dwell_spin.value()*1000))
            else:
                rate = self.session.summary()['task_completion_rate_per_s']
                note = '已达1000次尝试上限，请建立新队列。' if self.session.status == 'limit_reached' else '队列完成；双击成功项重开。'
                self.schedule_pdf()
                self.status.setText(self.status.text() + f'\n任务完成率 {rate:.3f} 项/s（含加载/保存/停留/暂停，非持续推理FPS）。' + note)

    def _select_last(self):
        if not self._closing and self.inputs:
            self.queue_table.selectRow(len(self.inputs)-1)

    def open_selected_record(self, _index=None):
        if self.busy or self.timer.isActive() or self.session is None:
            return
        index = self.selected_index()
        if index < 0:
            return
        item = self.inputs[index]
        row = next((r for r in reversed(self.session.rows) if r['item_id'] == item.item_id and r['status'] == 'success'), None)
        if row:
            from .records import load_record
            self.start_job('open', lambda *_: load_record(row['record_directory']))

    def save_current(self):
        if not self.busy and self.parent().result is not None:
            # Per-item records are already durable. Ctrl+S keeps the original save action.
            if self.session and self.session.status in ('ready', 'running'):
                self.pause_queue()
            self.parent().choose_export()

    def closeEvent(self, event):
        self.timer.stop()
        self.stop_requested = True
        if self.session and not self.busy and not self.session.finished:
            try:
                self.session.stop()
            except Exception as exc:
                self.status.setText(f'停止索引保存失败：{exc}；既有帧目录保留。')
        if self.session and not self.busy and self.session._classifier is not None:
            session=self.session;self._closing=False
            self.start_job('close_cleanup',lambda *_:session.close_classifier());self._closing=True;event.ignore();return
        super().closeEvent(event)
