"""Teaching evidence page, independent of the active inference transaction."""
import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer,QSignalBlocker
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QTableView, QTabWidget, QVBoxLayout, QWidget, QPlainTextEdit,QComboBox)

from .analysis_ui import AnalysisDialog
from .analysis_identity import frozen_pair, result_identity
from .formal_input import EDUCATION_NOTICE
from .formal_results import processing_pair
from .device.formal_capture import load_capture
from .resources import resource_path
from .records import load_record
from .validation import bundled_reference, historical_p7_comparison, load_reference, mask_comparison, performance_snapshot, TIMING_ROWS
from .reference_library import references


def table(parent, columns):
    view = QTableView(parent)
    model = QStandardItemModel(0, len(columns), view)
    model.setHorizontalHeaderLabels(columns)
    view.setModel(model)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    view.horizontalHeader().setStretchLastSection(True)
    view.setAlternatingRowColors(True)
    return view, model


def append_row(model, values):
    model.appendRow([QStandardItem(str(value)) for value in values])


class ValidationDialog(AnalysisDialog):
    def __init__(self, parent):
        super().__init__(parent)
        from .gui import ImageView
        self.setWindowTitle('教学验证与性能对照')
        self.resize(1030, 760)
        self.setMinimumSize(760, 580)
        self.pair = self.comparison = self.reference = None
        self.references={}
        layout = QVBoxLayout(self)
        self.notice = QLabel('读取当前或历史结果进行对照；本页不发送FPGA、不把参考Mask替代实时结果。\n' + EDUCATION_NOTICE)
        self.notice.setWordWrap(True)
        self.notice.setObjectName('algorithmNotice')
        layout.addWidget(self.notice)
        row = QHBoxLayout()
        self.current_button = QPushButton('读取工作台当前结果')
        self.open_button = QPushButton('打开历史记录/采集目录')
        self.reference_button = QPushButton('导入整数参考/标注声明')
        for button in (self.current_button, self.open_button, self.reference_button):
            button.setAutoDefault(False)
            row.addWidget(button)
        layout.addLayout(row)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName('教学验证和性能证据')
        correct = QWidget()
        cl = QVBoxLayout(correct)
        self.identity_label = QLabel('尚无正式结果；可读取工作台或打开历史记录。')
        self.identity_label.setTextFormat(Qt.TextFormat.PlainText)
        self.identity_label.setWordWrap(True)
        self.identity_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cl.addWidget(self.identity_label)
        self.reference_type=QComboBox();self.reference_type.addItem('INT8整数参考 · 验证硬件实现一致性','int8')
        self.reference_type.addItem('人工标注 · 评价原始分割质量','annotation')
        self.reference_type.setAccessibleName('选择INT8整数参考或人工标注对比')
        cl.addWidget(self.reference_type);self.reference_type.currentIndexChanged.connect(self.select_reference)
        images = QHBoxLayout()
        self.views = []
        for title in ('原始FPGA Mask', '整数参考/人工标注（对照）', '差异：红=预测独有，蓝=参考独有'):
            group = QVBoxLayout()
            label = QLabel(title)
            label.setWordWrap(True)
            group.addWidget(label)
            view = ImageView('不可用', smooth=False)
            view.setMinimumHeight(180)
            view.setAccessibleName(title)
            group.addWidget(view, 1)
            self.views.append(view)
            images.addLayout(group, 1)
        cl.addLayout(images, 1)
        self.comparison_label = QLabel('缺少对应参考时，不生成一致性或精度结论。')
        self.comparison_label.setTextFormat(Qt.TextFormat.PlainText)
        self.comparison_label.setWordWrap(True)
        cl.addWidget(self.comparison_label)
        self.tabs.addTab(correct, '正确性与标注对照')
        perf = QWidget()
        pl = QVBoxLayout(perf)
        self.performance_label = QLabel('板内图含LUT/CNN/DDR等待；图像E2E止于合法FINISH。减少统计不算CNN提速。')
        self.performance_label.setWordWrap(True)
        pl.addWidget(self.performance_label)
        self.timing_table, self.timing_model = table(perf, ['本帧耗时', '数值'])
        pl.addWidget(self.timing_table, 2)
        self.stage_label = QLabel('逐层统计不可用')
        pl.addWidget(self.stage_label)
        self.stage_table, self.stage_model = table(perf, ['阶段', 'cycles', '阶段时间 ms'])
        pl.addWidget(self.stage_table, 1)
        self.tabs.addTab(perf, '本帧分项与逐层统计')
        history = QWidget()
        hl = QVBoxLayout(history)
        self.history_button = QPushButton('读取并重算P7历史实板证据')
        self.history_button.setAutoDefault(False)
        hl.addWidget(self.history_button)
        self.history_table, self.history_model = table(history, ['指标', '旧Flash V6 R1', 'P7候选', '耗时缩短'])
        hl.addWidget(self.history_table)
        self.history_note = QPlainTextEdit()
        self.history_note.setReadOnly(True)
        self.history_note.setAccessibleName('历史对照条件、来源和原始帧索引')
        hl.addWidget(self.history_note, 1)
        self.tabs.addTab(history, '旧Flash / P7历史对照')
        layout.addWidget(self.tabs, 1)
        self.status = QLabel('就绪 · 只读验证，不连接设备。')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.current_button.clicked.connect(self.read_current)
        self.open_button.clicked.connect(self.choose_record)
        self.reference_button.clicked.connect(self.choose_reference)
        self.history_button.clicked.connect(lambda: self.start_job('history', lambda *_: historical_p7_comparison()))
        QTimer.singleShot(0, self.read_current)
        self.refresh()

    def refresh(self):
        self.current_button.setEnabled(not self.busy)
        self.open_button.setEnabled(not self.busy)
        self.reference_button.setEnabled(not self.busy and self.pair is not None)
        self.history_button.setEnabled(not self.busy)
        self.reference_type.setEnabled(not self.busy and self.pair is not None)

    def inspect(self, pair):
        try:
            frozen = frozen_pair(pair)
        except Exception as exc:
            self.status.setText(f'当前结果不可用：{exc}')
            return
        def action(_cancel, _progress):
            identity = result_identity(frozen)
            return frozen, identity, references(frozen), performance_snapshot(frozen)
        self.start_job('inspect', action)

    def read_current(self):
        if self._closing or self.busy:
            return
        parent = self.parent()
        if parent.request is None or parent.result is None:
            return
        self.inspect((parent.request, parent.result))

    def choose_record(self):
        if self.busy:
            return
        name = QFileDialog.getExistingDirectory(self, '选择四窗记录或正式采集目录')
        if not name:
            return
        def action(_cancel, _progress):
            folder = Path(name)
            if (folder / 'record.json').exists():
                rec = load_record(folder)
                pair = (rec.request, rec.result)
                pair[1].metadata.update(recorded_display_mode=pair[1].metadata.get('display_mode'),
                                        display_mode='offline_replay', live_run_performed=False)
            else:
                capture = load_capture(folder, resource_path('formal_root')/'assets/manifest.json', require_profile=False)
                pair = processing_pair(capture, capture_directory=folder)
            return pair, result_identity(pair), references(pair), performance_snapshot(pair)
        self.start_job('inspect', action)

    def choose_reference(self):
        if self.busy or self.pair is None:
            return
        name, _ = QFileDialog.getOpenFileName(self, '导入绑定当前输入的对照声明', '', '参考声明 (*.json)')
        if name:
            pair = self.pair
            self.start_job('reference', lambda *_: load_reference(name, pair))

    def completed(self, kind, value, error):
        if error:
            if kind == 'reference':
                self.references[self.reference_type.currentData()]=None
                self.show_reference(None)
            self.status.setText('对照不可用 · ' + error + '；未更改工作台结果。')
            return
        if kind == 'history':
            self.history_model.removeRows(0, self.history_model.rowCount())
            for label, key in (('板内图执行 ms', 'graph_ms'), ('图像E2E ms', 'e2e_ms')):
                v = value['values'][key]
                append_row(self.history_model, [label, f"{v['before']:.5f}", f"{v['after']:.5f}", f"{v['reduction_percent']:.2f}%"])
            self.history_note.setPlainText(value['note'] + '\n\n证据包SHA256：' + value['archive_sha256'] + '\n' +
                                          '\n'.join(f"{r['group']} frame {r['frame_id']} · {r['path']}\n  report SHA {r['report_sha256']}"
                                                    for r in value['frames']))
            self.status.setText('已重算历史各5帧；未连接设备，未新增测试成绩。')
            return
        if kind == 'inspect':
            pair, identity, reference, perf = value
            self.pair = pair
            self.identity_label.setText(f"{perf['origin']} · frame {identity['frame_id']} / generation {identity['generation']}\n"
                                       f"模型 {identity['model_version']} · ROI {identity['roi']}\nRGB SHA {identity['input_rgb_sha256']}")
            self.views[0].set_image(pair[1].mask)
            self.timing_model.removeRows(0, self.timing_model.rowCount())
            for label, key in TIMING_ROWS:
                v = perf['values'].get(key)
                append_row(self.timing_model, [label, '不可用' if v is None else f'{v:.3f} ms'])
            hardware = perf['hardware_declaration'] or {}
            self.performance_label.setText('板内图包含LUT/CNN/DDR等待；各项不强行相加。\n'
                                           + f"统计模式：{perf['telemetry_mode']}；BUSY/重试：{json.dumps(perf['diagnostics'], ensure_ascii=False)}\n"
                                           + '硬件为原帧声明，非自动识别：' + str(hardware.get('candidate', '未记录')))
            self.performance_label.setToolTip(json.dumps(hardware, ensure_ascii=False, indent=2))
            self.stage_model.removeRows(0, self.stage_model.rowCount())
            for row in perf['stages']:
                append_row(self.stage_model, [row['stage'], row['cycles'], f"{row['cycles']/100000:.5f}"])
            self.stage_label.setText('完整82阶段；计数器非互斥，不以求和替代总图耗时。' if perf['stages'] else '逐层统计不可用（未采集或缺失）；不填0。')
            self.references=reference
            self.select_reference()
        elif kind == 'reference':
            self.references[value['kind']]=value
            with QSignalBlocker(self.reference_type):self.reference_type.setCurrentIndex(self.reference_type.findData(value['kind']))
            self.select_reference()
        self.status.setText('只读对照已更新；工作台原始结果保持不变。')

    def select_reference(self,*_):
        self.show_reference(self.references.get(self.reference_type.currentData()))

    def show_reference(self, reference):
        self.reference = reference
        self.comparison = None
        if reference is None:
            for view in self.views[1:]:
                view.clear_image()
            kind=self.reference_type.currentData();reason=self.references.get('errors',{}).get(kind,'无匹配参考：原图/RGB SHA、模型、预处理版本及ROI必须一致。任意ROI不能套用整图参考。')
            self.comparison_label.setText(('整数一致性' if kind=='int8' else '人工标注Dice/IoU')+'不可用；'+reason)
            return
        result = mask_comparison(self.pair[1].mask, reference['mask'], kind=reference['kind'])
        self.comparison = result
        self.views[1].set_image(reference['mask'])
        self.views[2].set_image(result['difference_bgr'])
        if reference['kind'] == 'int8':
            text = f"原始FPGA Mask与INT8整数参考：差异 {result['different_pixels']} 字节；逐字节一致：{'是' if result['byte_exact'] else '否'}。验证硬件实现，不代表与人工标注的精度。"
        else:
            text = f"原始FPGA Mask与人工标注：Dice {result['dice']:.6f}；IoU {result['iou']:.6f}。双方空Mask约定为1；评价分割质量，不能代替硬件整数一致性。"
        self.comparison_label.setText(text + '\n' + reference['note'])
