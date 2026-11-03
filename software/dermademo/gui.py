from dataclasses import asdict, replace
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Callable
from uuid import uuid4

import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, QSignalBlocker, Signal, Slot
from PySide6.QtGui import QCloseEvent, QFontDatabase, QImage, QKeySequence, QPainter, QPixmap, QShortcut, QTransform
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFrame, QGraphicsScene,
    QGraphicsView, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget, QInputDialog, QCheckBox, QSlider, QPlainTextEdit, QSizePolicy, QMenu,
)

from .registry import create_registry
from .contracts import BackendConfig, ProcessingBackend, ProcessingConfig, ProcessingRequest, ProcessingResult
from .imaging import LoadedImage, load_candidate
from .storage import save_result
from .preferences import Preferences, parse_config
from .timing_view import TimingDialog
from .theme import install_theme
from ._fluent.style import set_fluent_property
from .records import LoadedRecord, load_record
from .overlay_view import OverlayPresentation
from .linked_views import LinkedViews
from .resources import resource_path
from .formal_input import EDUCATION_NOTICE, resource_summary
from .diagnostic_logging import logged_task, record_exception, error_for


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_PATH = resource_path("sample")
TEMPORARY_NOTICE = "参考阈值算法，仅用于对照；模型分割请使用 FPGA 分割。"




class ImageView(QGraphicsView):
    double_clicked = Signal()
    image_changed = Signal()
    zoom_changed = Signal(float)
    navigation_changed = Signal(object)

    def __init__(self, empty_text: str, smooth: bool = True):
        super().__init__()
        self.smooth = smooth
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.placeholder = QLabel(empty_text, self.viewport())
        self.placeholder.setObjectName("placeholder")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.has_image = False
        self.fit_mode = True
        self.pixmap: QPixmap | None = None
        self._overlay_item = self._overlay_base = None
        for scrollbar in (self.horizontalScrollBar(), self.verticalScrollBar()):
            scrollbar.sliderMoved.connect(lambda _value: self._navigation_changed())

    def navigation_state(self):
        return (self.transform().m11(), self.mapToScene(self.viewport().rect().center()), self.fit_mode)

    def _navigation_changed(self):
        if self.has_image:
            self.navigation_changed.emit(self.navigation_state())

    def apply_navigation(self, state):
        scale, center, fit = state
        if fit:
            self.fit_image()
        else:
            self.fit_mode = False
            self.setTransform(QTransform.fromScale(scale, scale))
            self.centerOn(center)
            self.zoom_changed.emit(scale)

    def show_overlay_layers(self, background, layer, visible, opacity):
        # pixmap remains the canonical result used to initialize big viewers.
        # Appearance lives in scene items and never mutates the result pixels.
        if self._overlay_item is None:
            self.scene.clear()
            self._overlay_base = self.add_pixmap(background)
            self._overlay_item = self.add_pixmap(layer)
        else:
            self._overlay_base.setPixmap(background)
            self._overlay_item.setPixmap(layer)
        self._overlay_item.setVisible(visible)
        self._overlay_item.setOpacity(opacity)

    def add_pixmap(self, pixmap, scene=None):
        item = (scene if scene is not None else self.scene).addPixmap(pixmap)
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation if self.smooth
                                   else Qt.TransformationMode.FastTransformation)
        return item

    @staticmethod
    def prepare_pixmap(array: np.ndarray) -> QPixmap:
        if array.ndim == 2:
            pixels = np.ascontiguousarray(array)
            image = QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0],
                           QImage.Format.Format_Grayscale8).copy()
        else:
            pixels = cv2.cvtColor(array, cv2.COLOR_BGR2RGB)
            image = QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0],
                           QImage.Format.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            raise ValueError("无法创建图片显示资源")
        return pixmap

    def set_image(self, array: np.ndarray) -> None:
        self.set_pixmap(self.prepare_pixmap(array))

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self.pixmap = QPixmap(pixmap)
        self._overlay_item = self._overlay_base = None
        self.scene.clear()
        self.add_pixmap(self.pixmap)
        self.scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())
        self.has_image = True
        self.placeholder.hide()
        self.fit_image()
        self.image_changed.emit()

    def clear_image(self) -> None:
        self._overlay_item = self._overlay_base = None
        self.scene.clear()
        self.pixmap = None
        self.has_image = False
        self.resetTransform()
        self.placeholder.show()
        self.image_changed.emit()

    def fit_image(self) -> None:
        self.fit_mode = True
        if self.has_image:
            self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            self.zoom_changed.emit(self.transform().m11())
            self._navigation_changed()

    def zoom(self, factor: float) -> None:
        if not self.has_image or factor <= 0:
            return
        current = self.transform().m11()
        target = max(0.01, min(24.0, current * factor))
        self.fit_mode = False
        self.scale(target / current, target / current)
        self.zoom_changed.emit(self.transform().m11())
        self._navigation_changed()

    def actual_size(self) -> None:
        if self.has_image:
            self.fit_mode = False
            self.resetTransform()
            self.centerOn(self.scene.sceneRect().center())
            self.zoom_changed.emit(1.0)
            self._navigation_changed()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if self.has_image and event.buttons() & Qt.MouseButton.LeftButton:
            self.fit_mode = False
            self._navigation_changed()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.has_image and event.button() == Qt.MouseButton.LeftButton and not self.fit_mode:
            self._navigation_changed()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.placeholder.setGeometry(self.viewport().rect())
        if self.fit_mode:
            self.fit_image()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if not self.has_image or delta == 0:
            event.ignore()
            return
        self.zoom(1.2 if delta > 0 else 1 / 1.2)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if self.has_image and event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        event.accept()


class ImageViewer(QDialog):
    """A separate view of display pixels; never changes the measurement Mask."""

    def __init__(self, title: str, pixmap: QPixmap, parent: QWidget, smooth: bool = True):
        super().__init__(parent)
        self.setWindowTitle(f"{title} · 大图查看")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(640, 480)
        available = parent.screen().availableGeometry()
        self.resize(min(1160, int(available.width() * 0.9)),
                    min(850, int(available.height() * 0.9)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        toolbar = QHBoxLayout()
        heading = QLabel(title)
        heading.setObjectName("panelTitle")
        toolbar.addWidget(heading)
        toolbar.addStretch()
        self.zoom_label = QLabel()
        self.zoom_label.setMinimumWidth(65)
        toolbar.addWidget(self.zoom_label)
        self.zoom_out_button = QPushButton("− 缩小")
        self.zoom_in_button = QPushButton("＋ 放大")
        self.actual_button = QPushButton("100%")
        self.fit_button = QPushButton("适应窗口")
        for button in (self.zoom_out_button, self.zoom_in_button, self.actual_button, self.fit_button):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.view = ImageView("", smooth=smooth)
        layout.addWidget(self.view, 1)
        hint = QLabel("滚轮缩放 · 按住左键拖拽平移 · 双击适应窗口 · Esc 关闭")
        hint.setObjectName("muted")
        layout.addWidget(hint)
        self.view.zoom_changed.connect(lambda scale: self.zoom_label.setText(f"{scale * 100:.1f}%"))
        self.zoom_out_button.clicked.connect(lambda: self.view.zoom(1 / 1.2))
        self.zoom_in_button.clicked.connect(lambda: self.view.zoom(1.2))
        self.actual_button.clicked.connect(self.view.actual_size)
        self.fit_button.clicked.connect(self.view.fit_image)
        self.view.double_clicked.connect(self.view.fit_image)
        self.view.set_pixmap(pixmap)


class ImagePanel(QFrame):
    viewer_opened = Signal(object)

    def __init__(self, title: str, subtitle: str, empty: str, smooth: bool = True):
        super().__init__()
        self.title = title
        self.viewer: ImageViewer | None = None
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title_label = QLabel(title)
        self.title_label = title_label
        title_label.setObjectName("panelTitle")
        header.addWidget(title_label)
        header.addStretch()
        fit = QPushButton("适应窗口")
        fit.setObjectName("fit")
        header.addWidget(fit)
        layout.addLayout(header)
        self.view = ImageView(empty, smooth=smooth)
        self.view.setToolTip("双击打开大图 · 滚轮缩放 · 拖拽平移")
        layout.addWidget(self.view, 1)
        detail = QLabel(subtitle)
        self.detail_label = detail
        detail.setObjectName("muted")
        layout.addWidget(detail)
        # The Chinese heading identifies the image; explanations stay available
        # on hover without taking space away from the four image views.
        detail.hide()
        self.title_label.setToolTip(subtitle)
        fit.clicked.connect(self.view.fit_image)
        self.view.double_clicked.connect(self.open_viewer)
        self.view.image_changed.connect(self.close_viewer)

    def set_detail(self, text: str) -> None:
        self.detail_label.setText(text)
        self.title_label.setToolTip(text)

    def open_viewer(self) -> None:
        if self.view.pixmap is None:
            return
        if self.viewer is None:
            self.viewer = ImageViewer(self.title, self.view.pixmap, self.window(), smooth=self.view.smooth)
            self.viewer.finished.connect(self._viewer_finished)
            self.viewer_opened.emit(self.viewer)
        self.viewer.show()
        self.viewer.raise_()
        self.viewer.activateWindow()

    def close_viewer(self) -> None:
        if self.viewer is not None:
            self.viewer.close()

    def _viewer_finished(self, _result: int) -> None:
        self.viewer = None


class TaskThread(QThread):
    succeeded = Signal(str, str, object)
    failed = Signal(str, str, str)

    def __init__(self, kind: str, task_id: str, action: Callable, parent=None):
        super().__init__(parent)
        self.kind, self.task_id, self.action = kind, task_id, action

    def run(self) -> None:
        try:
            with logged_task(self.kind, self.task_id):
                value = self.action()
            self.succeeded.emit(self.kind, self.task_id, value)
        except Exception as exc:
            self.failed.emit(self.kind, self.task_id, str(exc))


class MainWindow(QMainWindow):
    cpu_runtime_changed=Signal(str)
    def __init__(self, backend: ProcessingBackend | None = None, preferences: Preferences | None = None,
                 *, delivery_mode: bool = False):
        super().__init__()
        self.delivery_mode = delivery_mode
        self.reference_mode = not delivery_mode
        self.backend = backend or create_registry().open("demo_cpu")
        self.preferences = preferences if preferences is not None else Preferences()
        self.image: np.ndarray | None = None
        self.source_path: Path | None = None
        self.is_synthetic = False
        self.request: ProcessingRequest | None = None
        self.result: ProcessingResult | None = None
        self._thread: TaskThread | None = None
        self._active_job_id: str | None = None
        self._import_display_ms: float | None = None
        self._submitted_clock: float | None = None
        self._applying_config = False
        self.last_export: Path | None = None
        self.timing_dialog: TimingDialog | None = None
        self.device_dialog = None
        self.rgb_device_dialog = None
        self.formal_dialog = None
        self.cpu_dialog = None
        self.classification_dialog = None
        self.roi_dialog = None
        self.queue_dialog = None
        self.validation_dialog = None
        self.ai_dialog = None
        self.review_dialog = None
        self.cpu_settings_dialog = None
        self.report_dialog = None
        self.analysis_record_directory = None
        from .credentials import CredentialStore
        try:self.ai_session_key=CredentialStore().read()
        except Exception:self.ai_session_key=""
        self._formal_view = False
        self.diagnostics_dialog = None
        self.opened_record: LoadedRecord | None = None
        self.record_view_only = False
        self._editing_before_record = None
        self.overlay_presentation: OverlayPresentation | None = None
        self.setWindowTitle("皮损影像工作台")
        self.setMinimumSize(1060, 740)
        self.resize(1400, 920)
        self._build_ui()
        self.linked_views = LinkedViews(self._all_image_views, self)
        for panel in self.panels:
            self.linked_views.register(panel.view)
        self.apply_config(self.preferences.config, self.preferences.manual_draft)
        self._rebuild_presets()
        self._restore_geometry()
        self._install_shortcuts(self)
        self._refresh_controls()
        if self.delivery_mode:
            self.notice.clear()
            self.notice.hide()
        self.set_status("就绪 · 导入图片并确认模型输入，或打开已有记录。" if self.delivery_mode else
                        "参考图像处理 · 导入图片后开始处理。")
        self._show_settings_warning()

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 18, 22, 14)
        layout.setSpacing(12)

        header = QHBoxLayout()
        brand = QLabel("D")
        brand.setObjectName("brand")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setFixedSize(48, 48)
        header.addWidget(brand)
        heading = QVBoxLayout()
        heading.setSpacing(3)
        title = QLabel("皮损影像工作台")
        title.setObjectName("title")
        heading.addWidget(title)
        subtitle = QLabel("图像增强 · 分割预览 · 形态量化")
        subtitle.setObjectName("subtitle")
        heading.addWidget(subtitle)
        header.addLayout(heading)
        header.addStretch()
        self.formal_button = QPushButton("FPGA 分割")
        self.formal_button.setAccessibleName("正式 FPGA Mask 与实板记录回放")
        self.formal_button.clicked.connect(self.open_formal_device)
        header.addWidget(self.formal_button)
        self.cpu_button = QPushButton('CPU FP32 对照')
        self.cpu_button.setAccessibleName('同模型CPU FP32推理和FPGA实际输入对照')
        self.cpu_button.clicked.connect(self.open_cpu_baseline)
        header.addWidget(self.cpu_button)
        self.classification_button=QPushButton('整图分类 · 教学候选')
        self.classification_button.clicked.connect(self.open_classification)
        self.roi_button = QPushButton('矩形 ROI')
        self.roi_button.setAccessibleName('矩形ROI框选、裁剪预览、原图叠加与记录')
        self.roi_button.clicked.connect(self.open_roi_device)
        header.addWidget(self.roi_button)
        self.analysis_button = QPushButton('分析与演示')
        self.analysis_button.setAccessibleName('多图FPGA队列、教学验证与性能证据')
        self.analysis_menu = QMenu(self.analysis_button)
        self.analysis_menu.addAction('多图 FPGA 推理队列', self.open_formal_queue)
        self.analysis_menu.addAction('教学验证与性能对照', self.open_validation)
        self.analysis_menu.addAction('硬件对照实验台', self.open_hardware_lab)
        self.analysis_menu.addAction('分割复核与测量', self.open_review)
        self.analysis_menu.addAction('CPU Python配置', self.open_cpu_settings)
        self.analysis_menu.addAction('统一总报告', self.open_report)
        self.analysis_button.setMenu(self.analysis_menu)
        header.addWidget(self.analysis_button)
        self.diagnostics_button = QPushButton("关于与诊断")
        self.diagnostics_button.clicked.connect(self.open_diagnostics)
        header.addWidget(self.diagnostics_button)
        layout.addLayout(header)

        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        row = QHBoxLayout(toolbar)
        row.setContentsMargins(12, 10, 12, 10)
        self.import_button = QPushButton("导入图片")
        self.open_record_button = QPushButton("打开记录")
        self.sample_button = QPushButton("载入合成样例")
        self.device_button = QPushButton("通信自检")
        self.device_button.setAccessibleName("本机通信实验")
        self.device_button.setToolTip("AHFP 候选协议的本机实验；实际 RGB UDP 请使用 FPGA RGB 入口。")
        self.rgb_device_button = QPushButton("FPGA RGB")
        self.rgb_device_button.setAccessibleName("FPGA RGB UDP 联调")
        self.rgb_device_button.setToolTip("成员一实际 UDP RGB 协议；仅旁路/高斯，无 CNN 或 Mask。")
        self.backend_combo = QComboBox()
        self.backend_combo.addItem(self.backend.capabilities.name, self.backend.capabilities.key)
        self.backend_combo.setMinimumWidth(175)
        self.backend_combo.setToolTip("此处选择 CPU 流程；正式模型通过右上角 FPGA Mask 入口运行。")
        self.theme_combo = QComboBox()
        self.theme_combo.setAccessibleName("界面主题")
        for caption, mode in (("跟随系统", "system"), ("浅色", "light"), ("深色", "dark"), ("高对比", "high-contrast")):
            self.theme_combo.addItem(caption, mode)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(QApplication.instance().fluent_theme.mode.value))
        self.theme_combo.setToolTip("切换界面主题，不改变图片或处理参数。")
        self.process_button = QPushButton("开始处理")
        self.process_button.setObjectName("primary")
        self.process_button.setProperty("fluentAppearance", "primary")
        self.process_button.setToolTip("开始处理（Enter / 小键盘回车）")
        self.ai_button = QPushButton('AI分析')
        self.ai_button.setEnabled(False)
        self.ai_button.setToolTip('教学结果解读；每次上传与计费均需确认。')
        self.ai_button.clicked.connect(self.open_ai)
        row.addWidget(self.ai_button)
        self.review_button=QPushButton('复核与测量')
        self.review_button.setAccessibleName('原始Mask复核、独立人工修订、测量与输入质量提醒')
        self.review_button.clicked.connect(self.open_review)
        row.addWidget(self.review_button)
        self.report_button=QPushButton('统一总报告');self.report_button.clicked.connect(self.open_report);row.addWidget(self.report_button)
        row.addWidget(self.classification_button)
        self.save_button = QPushButton("保存结果")
        self.return_to_fpga_button = QPushButton("返回 FPGA 工作台")
        self.return_to_fpga_button.clicked.connect(self.leave_reference_mode)
        for widget in (self.import_button, self.open_record_button, self.sample_button, self.backend_combo,
                       self.device_button, self.rgb_device_button):
            row.addWidget(widget)
        row.addStretch()
        row.addWidget(self.theme_combo)
        row.addWidget(self.return_to_fpga_button)
        row.addWidget(self.save_button)
        row.addWidget(self.process_button)
        layout.addWidget(toolbar)

        self.notice = QLabel(TEMPORARY_NOTICE)
        self.notice.setWordWrap(True)
        self.notice.setObjectName("algorithmNotice")
        layout.addWidget(self.notice)
        self.input_quality_banner=QLabel('尚未选择图片，未检查输入质量。')
        self.input_quality_banner.setWordWrap(True)
        self.input_quality_banner.setTextFormat(Qt.TextFormat.PlainText)
        self.input_quality_banner.setAccessibleName('首页输入质量提醒，基于待发送的正式256模型输入')
        self.input_quality_banner.setProperty('fluentRole','messageBar')
        self.input_quality_banner.hide();layout.addWidget(self.input_quality_banner)
        self.reference_row=QWidget();reference_layout=QHBoxLayout(self.reference_row);reference_layout.setContentsMargins(0,0,0,0)
        self.reference_summary=QLabel();self.reference_summary.setWordWrap(True);self.reference_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.reference_summary.setAccessibleName('原始FPGA Mask的整数一致性及人工标注Dice与IoU')
        self.reference_compare_button=QPushButton('查看对比');self.reference_compare_button.setAutoDefault(False)
        self.reference_compare_button.clicked.connect(self.open_validation)
        reference_layout.addWidget(self.reference_summary,1);reference_layout.addWidget(self.reference_compare_button)
        self.reference_row.hide();layout.addWidget(self.reference_row)
        self.edge_reminder = QLabel()
        self.edge_reminder.setObjectName('roiEdgeReminder');self.edge_reminder.setWordWrap(True)
        self.edge_reminder.setTextFormat(Qt.TextFormat.PlainText)
        self.edge_reminder.setAccessibleName('当前成功ROI结果的预测触边提醒')
        self.edge_reminder.setProperty('fluentRole','messageBar')
        self.edge_reminder.hide();layout.addWidget(self.edge_reminder)

        record_row = QHBoxLayout()
        self.record_label = QLabel()
        self.record_label.setObjectName("recordOrigin")
        self.record_label.setWordWrap(True)
        self.record_label.setTextFormat(Qt.TextFormat.PlainText)
        self.continue_button = QPushButton("使用原图继续处理")
        self.continue_button.setToolTip("退出记录查看状态，使用当前后端；按 Enter 才开始新处理。")
        record_row.addWidget(self.record_label, 1)
        record_row.addWidget(self.continue_button)
        layout.addLayout(record_row)

        comparison = QHBoxLayout()
        self.link_checkbox = QCheckBox("联动查看")
        self.link_checkbox.setToolTip("同步四图和已打开大图的缩放与位置；关闭后可独立查看。")
        self.overlay_checkbox = QCheckBox("显示轮廓/填充")
        self.overlay_checkbox.setChecked(True)
        self.overlay_background = QComboBox()
        self.overlay_background.addItems(["原图背景", "增强图背景"])
        self.overlay_background.setAccessibleName("叠加背景")
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.setAccessibleName("轮廓与填充不透明度")
        self.opacity_label = QLabel("100%")
        self.opacity_label.setMinimumWidth(42)
        self.fit_all_button = QPushButton("全部适应窗口")
        comparison.addWidget(self.link_checkbox)
        comparison.addWidget(self.overlay_checkbox)
        comparison.addWidget(self.overlay_background)
        comparison.addWidget(QLabel("不透明度"))
        comparison.addWidget(self.opacity_slider, 1)
        comparison.addWidget(self.opacity_label)
        comparison.addWidget(self.fit_all_button)
        layout.addLayout(comparison)

        body = QHBoxLayout()
        body.setSpacing(14)
        images = QGridLayout()
        images.setSpacing(12)
        descriptions = [
            ("01  原始影像", "显示经方向校正的原图", "导入 PNG / JPEG 图片"),
            ("02  增强影像", "局部对比度增强，仅用于展示", "处理后显示增强结果"),
            ("03  分割结果", "二值结果：白色为前景，黑色为背景", "处理后显示分割结果"),
            ("04  轮廓叠加", "原图背景与最终测量边界", "处理后显示边界与原图对照"),
        ]
        self.panels = [ImagePanel(*description, smooth=index != 2) for index, description in enumerate(descriptions)]
        for index, panel in enumerate(self.panels):
            panel.viewer_opened.connect(self._install_shortcuts)
            panel.viewer_opened.connect(self._viewer_opened)
            images.addWidget(panel, index // 2, index % 2)
        images.setRowStretch(0, 1)
        images.setRowStretch(1, 1)
        images.setColumnStretch(0, 1)
        images.setColumnStretch(1, 1)
        body.addLayout(images, 1)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 18, 18, 16)
        side.setSpacing(6)
        self._section(side, "本次测量")
        self.area_label = self._metric(side, "前景面积", "px²")
        self.diameter_label = self._metric(side, "最大 Feret 径", "px")
        self.time_label = self._metric(side, "端到端处理耗时", "ms")
        self.fpga_time_label = QLabel()
        self.fpga_time_label.setObjectName("muted")
        self.fpga_time_label.setWordWrap(True)
        self.fpga_time_label.hide()
        side.addWidget(self.fpga_time_label)
        self.timing_button = QPushButton("耗时详情")
        side.addWidget(self.timing_button)
        calibration = self.calibration_label = QLabel("物理尺度：未标定\n指标来源：PC / 最终 Mask")
        calibration.setObjectName("muted")
        calibration.setWordWrap(True)
        side.addWidget(calibration)
        self.threshold_info = QLabel("实际分割阈值：—")
        self.threshold_info.setObjectName("muted")
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setObjectName("divider")
        side.addWidget(separator)
        self.parameters_widget = QWidget()
        side.addWidget(self.parameters_widget)
        sidebar_layout = side
        side = QVBoxLayout(self.parameters_widget)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(6)
        self._section(side, "处理参数")
        side.addWidget(QLabel("参数预设"))
        self.preset_combo = QComboBox()
        self.preset_combo.setAccessibleName("参数预设")
        side.addWidget(self.preset_combo)
        preset_row = QHBoxLayout()
        self.save_preset_button = QPushButton("保存为预设")
        self.delete_preset_button = QPushButton("删除预设")
        preset_row.addWidget(self.save_preset_button)
        preset_row.addWidget(self.delete_preset_button)
        side.addLayout(preset_row)

        side.addWidget(QLabel("增强强度（CLAHE）"))
        self.clip_spin = QDoubleSpinBox()
        self.clip_spin.setRange(0.5, 8.0)
        self.clip_spin.setSingleStep(0.5)
        self.clip_spin.setValue(2.0)
        side.addWidget(self.clip_spin)
        side.addWidget(QLabel("分割模式"))
        self.threshold_mode = QComboBox()
        self.threshold_mode.addItems(["自动阈值（Otsu）", "手动阈值（暗区域）"])
        side.addWidget(self.threshold_mode)
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(0, 255)
        self.threshold_spin.setValue(110)
        self.threshold_spin.setPrefix("阈值：")
        side.addWidget(self.threshold_spin)
        side.addWidget(QLabel("最小区域面积（px²）"))
        self.region_spin = QSpinBox()
        self.region_spin.setRange(1, 1_000_000)
        self.region_spin.setValue(64)
        side.addWidget(self.region_spin)
        self.pending_label = QLabel("参数与当前结果一致")
        self.pending_label.setObjectName("pendingParameters")
        self.pending_label.setWordWrap(True)
        restore_row = QHBoxLayout()
        self.defaults_button = QPushButton("恢复默认")
        self.restore_button = QPushButton("恢复结果参数")
        restore_row.addWidget(self.defaults_button)
        restore_row.addWidget(self.restore_button)
        side.addLayout(restore_row)
        parameter_hint = QLabel("保留最大连通区域\n双击查看大图 · 滚轮缩放 · 拖拽平移")
        parameter_hint.setObjectName("muted")
        side.addWidget(parameter_hint)
        side = sidebar_layout
        self.technical_toggle = QPushButton("技术详情")
        self.technical_toggle.setCheckable(True)
        self.technical_toggle.setAccessibleName("展开或收起技术详情")
        self.technical_toggle.setToolTip("查看结果来源、模型版本和只读参数；不改变当前结果。")
        side.addWidget(self.technical_toggle)
        self.technical_details = QWidget()
        technical = QVBoxLayout(self.technical_details)
        technical.setContentsMargins(0, 0, 0, 0)
        technical.setSpacing(6)
        self.technical_origin = QLabel()
        self.technical_origin.setWordWrap(True)
        self.technical_origin.setTextFormat(Qt.TextFormat.PlainText)
        self.technical_origin.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        technical.addWidget(self.technical_origin)
        technical.addWidget(self.threshold_info)
        self.generic_parameters = QPlainTextEdit()
        self.generic_parameters.setReadOnly(True)
        self.generic_parameters.setAccessibleName("记录后端参数，只读")
        self.generic_parameters.setMaximumHeight(180)
        technical.addWidget(self.generic_parameters)
        version = self.version_label = QLabel(f"后端版本\n{self.backend.capabilities.algorithm_version}\n正式模型入口：FPGA Mask")
        version.setObjectName("muted")
        version.setWordWrap(True)
        version.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        technical.addWidget(version)
        self.resource_label = QLabel(resource_summary())
        self.resource_label.setObjectName('muted')
        self.resource_label.setWordWrap(True)
        self.resource_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.resource_label.setAccessibleName('团队提供的 FPGA 静态资源参考')
        technical.addWidget(self.resource_label)
        side.addWidget(self.technical_details)
        self.technical_details.hide()
        self.technical_toggle.toggled.connect(self.technical_details.setVisible)
        side.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFixedWidth(264)
        scroll.setWidget(sidebar)
        scroll.setObjectName("sidebarScroll")
        body.addWidget(scroll)
        layout.addLayout(body, 1)

        self.file_label = QLabel("当前文件：未导入")
        self.file_label.setObjectName("muted")
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)
        layout.addWidget(self.pending_label)
        self.progress = QProgressBar()
        self.progress.setObjectName("taskProgress")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.status_label = QLabel()
        self.status_label.setObjectName("statusMessage")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status_label)
        self.settings_label = QLabel()
        self.settings_label.setObjectName("settingsWarning")
        self.settings_label.setWordWrap(True)
        self.settings_label.hide()
        layout.addWidget(self.settings_label)
        footer = QLabel("Enter 开始处理 / Ctrl+S 保存结果 · " + EDUCATION_NOTICE)
        footer.setWordWrap(True)
        footer.setObjectName("footer")
        layout.addWidget(footer)

        self.import_button.clicked.connect(self.choose_image)
        self.open_record_button.clicked.connect(self.choose_record)
        self.continue_button.clicked.connect(self.continue_from_record)
        self.link_checkbox.toggled.connect(lambda enabled: self.linked_views.set_enabled(enabled))
        self.fit_all_button.clicked.connect(self.fit_all_images)
        self.overlay_checkbox.toggled.connect(self.update_overlay_display)
        self.overlay_background.currentIndexChanged.connect(self.update_overlay_display)
        self.opacity_slider.valueChanged.connect(self.update_overlay_display)
        self.sample_button.clicked.connect(lambda: self.load_path(SAMPLE_PATH, is_synthetic=True))
        self.device_button.clicked.connect(self.open_device)
        self.rgb_device_button.clicked.connect(self.open_rgb_device)
        self.process_button.clicked.connect(self.start_processing)
        self.save_button.clicked.connect(self.choose_export)
        self.defaults_button.clicked.connect(self.restore_defaults)
        self.restore_button.clicked.connect(self.restore_result_config)
        self.preset_combo.currentIndexChanged.connect(self.select_preset)
        self.save_preset_button.clicked.connect(self.prompt_save_preset)
        self.delete_preset_button.clicked.connect(self.delete_selected_preset)
        self.timing_button.clicked.connect(self.open_timing)
        self.theme_combo.currentIndexChanged.connect(
            lambda: QApplication.instance().fluent_theme.set_mode(self.theme_combo.currentData()))
        self.threshold_mode.currentIndexChanged.connect(self.parameters_changed)
        for spin in (self.clip_spin, self.threshold_spin, self.region_spin):
            spin.valueChanged.connect(self.parameters_changed)

    def _install_shortcuts(self, widget: QWidget) -> None:
        # Window scope also covers parameter inputs, without intercepting keys
        # in the native import/save dialogs. Install separately for big viewers.
        for sequence, callback in (
            (QKeySequence(Qt.Key.Key_Return), self.process_button.click),
            (QKeySequence(Qt.Key.Key_Enter), self.process_button.click),
            (QKeySequence("Ctrl+S"), self.save_button.click),
        ):
            shortcut = QShortcut(sequence, widget)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(callback)

    def open_device(self):
        if self.busy:
            return
        if self.device_dialog is None:
            from .device_ui import DeviceDialog
            self.device_dialog = DeviceDialog(self, self.image)
            self.device_dialog.destroyed.connect(self._device_closed)
            self.device_dialog.open()
        else:
            self.device_dialog.raise_()
            self.device_dialog.activateWindow()

    def _device_closed(self, *_args):
        self.device_dialog = None

    def open_rgb_device(self):
        if self.busy:
            return
        if self.rgb_device_dialog is None:
            from .rgb_device_ui import RGBDeviceDialog
            self.rgb_device_dialog = RGBDeviceDialog(self, self.image)
            self.rgb_device_dialog.destroyed.connect(self._rgb_device_closed)
            self.rgb_device_dialog.open()
        else:
            self.rgb_device_dialog.raise_()
            self.rgb_device_dialog.activateWindow()

    def _rgb_device_closed(self, *_args):
        self.rgb_device_dialog = None

    def open_formal_device(self):
        if self.busy:
            return
        if self.formal_dialog is None:
            from .formal_device_ui import FormalMaskDialog
            self.formal_dialog = FormalMaskDialog(self)
            if self.delivery_mode:
                self.formal_dialog.mode_combo.setCurrentIndex(self.formal_dialog.mode_combo.findData('live'))
                self.formal_dialog.source_combo.setCurrentIndex(self.formal_dialog.source_combo.findData('image'))
            self.formal_dialog.destroyed.connect(self._formal_closed)
            self.formal_dialog.open()
        else:
            self.formal_dialog.raise_()
            self.formal_dialog.activateWindow()

    def _formal_closed(self, *_args):
        self.formal_dialog = None

    def open_roi_device(self):
        if self.busy:return
        if self.roi_dialog is None:
            from .roi_ui import ROIDialog
            self.roi_dialog = ROIDialog(self)
            self.roi_dialog.destroyed.connect(self._roi_closed)
            self.roi_dialog.open()
        else:
            self.roi_dialog.raise_();self.roi_dialog.activateWindow()

    def _roi_closed(self, *_args):
        self.roi_dialog = None

    def open_classification(self):
        if self.busy:return
        from .classification_ui import ClassificationDialog
        dialog=ClassificationDialog(self)
        self.classification_dialog=dialog
        dialog.open()

    def open_cpu_baseline(self):
        if self.busy:return
        if self.cpu_dialog is None:
            from .cpu_ui import CPUDialog
            self.cpu_dialog = CPUDialog(self)
            self.cpu_dialog.destroyed.connect(self._cpu_closed)
            self.cpu_dialog.open()
        else:
            self.cpu_dialog.raise_();self.cpu_dialog.activateWindow()

    def _cpu_closed(self,*_args):
        self.cpu_dialog = None

    def open_formal_queue(self):
        if self.busy:
            return
        if self.queue_dialog is None:
            from .formal_queue_ui import FormalQueueDialog
            self.queue_dialog = FormalQueueDialog(self)
            self.queue_dialog.destroyed.connect(self._queue_closed)
            self.queue_dialog.open()
        else:
            self.queue_dialog.raise_()
            self.queue_dialog.activateWindow()

    def _queue_closed(self, *_args):
        self.queue_dialog = None

    def open_ai(self):
        if self.busy or self.request is None or self.result is None:return
        if self.ai_dialog is None:
            from .ai_ui import AIDialog
            try:self.ai_dialog=AIDialog(self)
            except Exception as exc:self.set_status('当前结果无法分析：'+str(exc));return
            self.ai_dialog.destroyed.connect(self._ai_closed);self.ai_dialog.open()
        else:self.ai_dialog.raise_()

    def _ai_closed(self,*_):self.ai_dialog=None

    def open_cpu_settings(self,initial=None):
        if self.busy:return
        if self.cpu_settings_dialog is None:
            from .cpu_runtime_ui import CPURuntimeDialog
            self.cpu_settings_dialog=CPURuntimeDialog(self,initial if isinstance(initial,str) else None)
            self.cpu_settings_dialog.destroyed.connect(lambda *_:setattr(self,'cpu_settings_dialog',None))
            self.cpu_settings_dialog.open()
        else:self.cpu_settings_dialog.raise_()

    def open_report(self):
        if self.busy or self.request is None or self.result is None or self.result.capabilities.key!='fpga_formal_v5':return
        if self.report_dialog is None:
            from .report_ui import ReportDialog
            self.report_dialog=ReportDialog(self);self.report_dialog.destroyed.connect(lambda *_:setattr(self,'report_dialog',None));self.report_dialog.open()
        else:self.report_dialog.raise_()

    def analysis_record(self):
        return self.opened_record.directory if self.opened_record is not None else self.analysis_record_directory or self.last_export

    def bind_analysis_record(self,folder,identity):
        try:
            from .analysis_identity import result_identity
            if self.result is not None and result_identity((self.request,self.result))==identity:self.analysis_record_directory=Path(folder)
        except ValueError:pass

    def show_input_quality(self,raw,label,*,pending=True):
        from .review import quality_bytes,quality_message
        try:
            data=quality_bytes(raw)
            self.input_quality_banner.setText(('待处理输入' if pending else '当前结果输入')+' · '+str(label)+'\n'+quality_message(data))
            set_fluent_property(self.input_quality_banner,'fluentSeverity','warning' if data['triggered'] else 'info')
            self.input_quality_banner.show()
        except Exception:
            self.input_quality_banner.setText('输入质量检查不可用；不据此判断曝光，不影响原分割结果。');self.input_quality_banner.show()

    def open_review(self):
        if self.busy or self.request is None or self.result is None:return
        if self.result.capabilities.key!='fpga_formal_v5':
            self.set_status('请先运行正式FPGA分割或打开对应实板记录，再进入复核。');return
        if self.review_dialog is None:
            from .review_ui import ReviewDialog
            try:self.review_dialog=ReviewDialog(self)
            except Exception as exc:self.set_status('复核准备失败：'+str(exc));return
            self.review_dialog.destroyed.connect(lambda *_:setattr(self,'review_dialog',None))
            self.review_dialog.open()
        else:self.review_dialog.raise_()

    def open_validation(self):
        if self.busy:
            return
        if self.validation_dialog is None:
            from .validation_ui import ValidationDialog
            self.validation_dialog = ValidationDialog(self)
            self.validation_dialog.destroyed.connect(self._validation_closed)
            self.validation_dialog.open()
        else:
            self.validation_dialog.read_current()
            self.validation_dialog.raise_()
            self.validation_dialog.activateWindow()

    def open_hardware_lab(self):
        if self.busy:return
        dialog=getattr(self,'hardware_lab_dialog',None)
        if dialog is None:
            from .hardware_lab_ui import HardwareLabDialog
            self.hardware_lab_dialog=HardwareLabDialog(self)
            self.hardware_lab_dialog.destroyed.connect(lambda *_:setattr(self,'hardware_lab_dialog',None))
            self.hardware_lab_dialog.open()
        else:dialog.raise_();dialog.activateWindow()

    def _validation_closed(self, *_args):
        self.validation_dialog = None

    def commit_formal_result(self, pair):
        commit_clock = perf_counter()
        request, result = pair
        result.validate(request)
        pixmaps, overlay = self._prepare_result_display(request, result)
        editing = self._editing_before_record if self.record_view_only else (self.selected_config(), self.threshold_spin.value())
        self.clear_result()
        self._editing_before_record = editing
        self.image, self.source_path, self.is_synthetic = request.image_bgr, request.source_path, request.is_synthetic
        self.request = request
        self.show_input_quality(np.ascontiguousarray(request.image_bgr[:,:,::-1]).tobytes(),request.source_path.name,pending=False)
        self.panels[0].view.set_pixmap(pixmaps[0])
        self._show_result(result, pixmaps, overlay)
        self.record_view_only, self._formal_view = True, True
        if self.delivery_mode:
            self.reference_mode = False
        self.generic_parameters.setPlainText(json.dumps(dict(request.config.values), ensure_ascii=False, indent=2))
        kind = result.metadata['source_kind']
        source = ('本机协议模拟 · 非本次实板验收' if kind == 'protocol_simulator' else
                  '离线实板记录回放 · 未进行本次实时推理' if result.metadata['display_mode'] == 'offline_replay'
                  else '本次实时 FPGA 结果')
        self.notice.setText('本机协议模拟结果，非实板推理。' if kind == 'protocol_simulator' else
                            '离线回放 · 本次未连接 FPGA。' if result.metadata['display_mode'] == 'offline_replay' else '')
        details = (f"{source} · frame {result.metadata['frame_id']} / generation {result.metadata['generation']}"
                   f"\n输入 SHA256：{result.metadata['input_sha256']}"
                   '\n增强图仅用于展示，模型输入未增强；分割为原始 256×256 二值结果。')
        self.record_label.setText(source)
        self.file_label.setText("模型输入：256 × 256")
        result.metadata.setdefault('stage_timings_ms', {})['display_commit'] = (perf_counter()-commit_clock)*1000
        context = result.metadata.get('input_context')
        if context:
            prep = context['preprocessing']
            self.file_label.setText(f"自选原图：{context['source_filename']} · 方向校正后 {prep['oriented_width']} × {prep['oriented_height']}"
                                    " · 模型输入 256×256")
            self.file_label.setToolTip(json.dumps(context, ensure_ascii=False, indent=2))
            details += '\n自选图片来源未核实，不自动标记为 ISIC。'
            if context.get('schema') == 'formal_roi_v1':
                self.record_label.setText(self.record_label.text() + f" · 原图ROI {context['roi']['bounds']}")
                self.notice.setText((self.notice.text() + ' ROI 外未处理，测量仅限 256 网格。').strip())
        self.record_label.setToolTip(details)
        self.technical_origin.setText(details)
        self.set_status('正式 Mask 已显示 · Ctrl+S 保存四窗及原始输入/Mask；再次运行请打开 FPGA Mask。')
        self._refresh_controls()

    def open_diagnostics(self):
        if self.diagnostics_dialog is None:
            from .diagnostics_view import DiagnosticsDialog
            images = {}
            if self.image is not None:
                images["original"] = self.image.copy()
            if self.result is not None:
                for name, attr in (("enhanced", "enhanced_bgr"), ("mask", "mask"), ("overlay", "overlay_bgr")):
                    images[name] = getattr(self.result, attr).copy()
            for image in images.values():
                image.setflags(write=False)
            self.diagnostics_dialog = DiagnosticsDialog(self, images)
            self.diagnostics_dialog.destroyed.connect(self._diagnostics_closed)
            self.diagnostics_dialog.open()
        else:
            self.diagnostics_dialog.raise_()
            self.diagnostics_dialog.activateWindow()

    def _diagnostics_closed(self, *_args):
        self.diagnostics_dialog = None

    def open_maintenance_tool(self, tool: str) -> None:
        if self.busy:
            return
        if tool in ('reference', 'sample'):
            self.enable_reference_mode()
            if tool == 'sample':
                self.load_path(SAMPLE_PATH, is_synthetic=True)
        elif tool == 'communication':
            self.open_device()
        elif tool == 'rgb':
            self.open_rgb_device()

    def enable_reference_mode(self) -> None:
        if self.record_view_only:
            self.reference_mode = True
            self.continue_from_record()
        self.reference_mode = True
        self.notice.setText(TEMPORARY_NOTICE)
        self._refresh_controls()
        self.set_status('参考图像处理 · 阈值算法仅用于对照；可返回 FPGA 工作台。')

    def leave_reference_mode(self) -> None:
        self.reference_mode = False
        self.notice.setText('当前显示参考算法结果；使用 FPGA 分割生成模型结果。'
                            if self.result is not None and self.result.capabilities.key != 'fpga_formal_v5' else '')
        self._refresh_controls()
        self.set_status('FPGA 工作台 · 导入图片或打开 FPGA 分割。')

    @staticmethod
    def _section(layout, title: str) -> None:
        label = QLabel(title)
        label.setObjectName("section")
        layout.addWidget(label)

    @staticmethod
    def _metric(layout, caption: str, unit: str) -> QLabel:
        label = QLabel(caption)
        label.setObjectName("muted")
        layout.addWidget(label)
        row = QHBoxLayout()
        value = QLabel("—")
        value.setObjectName("metric")
        row.addWidget(value)
        units = QLabel(unit)
        units.setObjectName("muted")
        row.addWidget(units, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch()
        layout.addLayout(row)
        return value

    def set_status(self, text: str, error: bool = False) -> None:
        self.status_label.setText(text)
        set_fluent_property(self.status_label, "fluentSeverity", "danger" if error else "info")

    def _refresh_controls(self) -> None:
        idle = not self.busy
        editable = idle and not self.record_view_only
        self.import_button.setEnabled(idle)
        self.open_record_button.setEnabled(idle)
        self.sample_button.setEnabled(idle)
        self.device_button.setEnabled(idle)
        self.rgb_device_button.setEnabled(idle)
        self.formal_button.setEnabled(idle)
        self.cpu_button.setEnabled(idle)
        self.classification_button.setEnabled(idle)
        self.roi_button.setEnabled(idle)
        self.analysis_button.setEnabled(idle)
        self.ai_button.setEnabled(idle and self.result is not None and self.result.capabilities.key=='fpga_formal_v5' and self.result.metadata.get('source_kind') in ('live_FPGA_session','saved_FPGA_capture') and self.result.metadata.get('wire_report',{}).get('complete',False))
        self.review_button.setEnabled(idle and self.result is not None and self.result.capabilities.key=='fpga_formal_v5')
        self.report_button.setEnabled(idle and self.result is not None and self.result.capabilities.key=='fpga_formal_v5')
        self.reference_compare_button.setEnabled(idle and self.result is not None and self.result.capabilities.key=='fpga_formal_v5')
        self.backend_combo.setEnabled(editable)
        formal_workspace = self.delivery_mode and not self.reference_mode
        self.process_button.setEnabled(idle if formal_workspace else editable and self.image is not None)
        for widget in (self.sample_button, self.device_button, self.rgb_device_button, self.backend_combo):
            widget.setVisible(not self.delivery_mode)
        self.return_to_fpga_button.setVisible(self.delivery_mode and self.reference_mode)
        self.save_button.setEnabled(idle and self.result is not None)
        self.timing_button.setEnabled(self.result is not None)
        self.save_button.setToolTip(
            "Ctrl+S 保存当前显示的处理结果及其实际使用的参数；尚未应用的新参数不会写入此结果。"
            if self.parameters_pending else "Ctrl+S 保存当前显示的处理结果及其参数。"
        )
        self.clip_spin.setEnabled(editable)
        self.threshold_mode.setEnabled(editable)
        self.region_spin.setEnabled(editable)
        self.threshold_spin.setEnabled(editable and self.threshold_mode.currentIndex() == 1)
        self.threshold_spin.setVisible(self.threshold_mode.currentIndex() == 1)
        self.defaults_button.setEnabled(editable)
        self.preset_combo.setEnabled(editable)
        self.save_preset_button.setEnabled(editable)
        self.delete_preset_button.setEnabled(editable and any(
            p.id == self.preset_combo.currentData() for p in self.preferences.presets))
        compatible = (self.result is not None and self.result.capabilities.key == self.backend.capabilities.key
                      and isinstance(self.request.config, ProcessingConfig))
        self.parameters_widget.setVisible(not formal_workspace and
                                         (not self.record_view_only or isinstance(self.request.config, ProcessingConfig)))
        self.generic_parameters.setVisible(self.record_view_only and isinstance(self.request.config, BackendConfig))
        self.notice.setVisible(bool(self.notice.text()))
        self.restore_button.setEnabled(editable and compatible)
        self.record_label.setVisible(self.opened_record is not None or self._formal_view)
        self.continue_button.setText("继续分割" if formal_workspace else
                                     "返回参考处理" if self._formal_view else "使用原图继续处理")
        self.continue_button.setVisible(self.record_view_only and not (formal_workspace and self._formal_view))
        self.continue_button.setEnabled(idle)
        for widget in (self.overlay_checkbox, self.overlay_background, self.opacity_slider):
            widget.setEnabled(self.result is not None)
        self.fit_all_button.setEnabled(self.image is not None)
        self.pending_label.setText("参数待应用 · 按 Enter 开始处理" if self.parameters_pending else
                                   "参数与当前结果一致" if self.result else "按 Enter 应用当前参数")
        self.pending_label.setVisible(self.parameters_pending)
        self.process_button.setText("FPGA 分割" if formal_workspace else
                                   "正在处理…" if self.busy and self._thread.kind == "process" else "开始处理")
        self.save_button.setText("正在保存…" if self.busy and self._thread.kind == "save" else "保存结果")
        self.progress.setRange(0, 0 if self.busy else 1)
        if idle:
            self.progress.setValue(1 if self.result is not None else 0)

    def clear_result(self) -> None:
        self.reference_row.hide();self.reference_summary.clear()
        self.input_quality_banner.hide()
        self.analysis_record_directory=None
        self.close_timing()
        self.edge_reminder.clear();self.edge_reminder.hide()
        if self.record_view_only and self._editing_before_record is not None:
            self._set_config_fields(*self._editing_before_record)
        self._editing_before_record = None
        self.opened_record = None
        self._formal_view = False
        self.record_view_only = False
        self.overlay_presentation = None
        self.request = None
        self.result = None
        self.technical_toggle.setChecked(False)
        self.technical_origin.clear()
        self.generic_parameters.clear()
        self.record_label.setToolTip('')
        self.panels[0].title = '01  原始影像'
        self.panels[0].title_label.setText(self.panels[0].title)
        self.panels[0].set_detail('显示经方向校正的原图')
        self.file_label.setToolTip('')
        self.last_export = None
        for panel in self.panels[1:]:
            panel.view.clear_image()
        for label in (self.area_label, self.diameter_label, self.time_label):
            label.setText("—")
        self.threshold_info.setText("实际分割阈值：—")
        self.calibration_label.setText("物理尺度：未标定\n指标来源：PC / 最终 Mask")
        self.version_label.setText(f"后端版本\n{self.backend.capabilities.algorithm_version}\n正式模型入口：FPGA Mask")
        self.version_label.setToolTip('')
        self.resource_label.setText(resource_summary())
        self.resource_label.setToolTip('')
        self.notice.setText(TEMPORARY_NOTICE if self.reference_mode else '')
        self.fpga_time_label.hide()

    def _all_image_views(self):
        return [p.view for p in self.panels] + [p.viewer.view for p in self.panels if p.viewer is not None]

    def _viewer_opened(self, viewer):
        self.linked_views.register(viewer.view)
        if self.panels[3].viewer is viewer:
            self.update_overlay_display()
        self.linked_views.align()

    def fit_all_images(self):
        self.linked_views._updating = True
        try:
            for view in self._all_image_views():
                view.fit_image()
        finally:
            self.linked_views._updating = False

    def update_overlay_display(self, *_args):
        self.opacity_label.setText(f"{self.opacity_slider.value()}%")
        if self.overlay_presentation is None:
            return
        panel = self.panels[3]
        views = [panel.view] + ([panel.viewer.view] if panel.viewer is not None else [])
        for view in views:
            self.overlay_presentation.apply(view, self.overlay_background.currentIndex() == 1,
                                            self.overlay_checkbox.isChecked(), self.opacity_slider.value() / 100)

    def _prepare_result_display(self, request, result):
        pixmaps = [ImageView.prepare_pixmap(array) for array in
                   (request.image_bgr, result.enhanced_bgr, result.mask, result.overlay_bgr)]
        overlay = OverlayPresentation.prepare(request, result, pixmaps[0], pixmaps[1])
        return pixmaps, overlay

    def _show_result(self, result, pixmaps, overlay):
        self.result = result
        if result.capabilities.key=='fpga_formal_v5':
            from .analysis_identity import result_identity
            from .reference_library import brief
            comparison=result.metadata.get('reference_comparison',{})
            if comparison.get('identity')!=result_identity((self.request,result)):comparison={}
            self.reference_summary.setText('帧 '+str(result.metadata['frame_id'])+' 原始Mask · '+brief(comparison))
            self.reference_summary.setToolTip('整数参考验证实现一致性；人工标注评价分割重合程度。仅比较原始Mask，人工修订另存。\n既有验证集参考不代表新增100图实板验收。')
            self.reference_row.show()
        else:self.reference_row.hide()
        from .roi_edge import reminder_text
        text,warning=reminder_text(result.metadata,result.mask,historical=result.metadata.get('display_mode')=='offline_replay')
        self.edge_reminder.setText(text);self.edge_reminder.setVisible(bool(text))
        set_fluent_property(self.edge_reminder,'fluentSeverity','warning' if warning else 'info')
        self.overlay_presentation = overlay
        for panel, pixmap in zip(self.panels[1:], pixmaps[1:]):
            panel.view.set_pixmap(pixmap)
        self.update_overlay_display()
        self.area_label.setText(f"{result.measurements.area_px2:,}")
        diameter = result.measurements.max_feret_px
        self.diameter_label.setText(f"{diameter:.1f}" if diameter is not None else "不适用")
        self.time_label.setText(f"{result.timings_ms['end_to_end']:.1f}")
        if result.capabilities.key == 'fpga_formal_v5':
            graph = result.timings_ms.get('fpga_graph')
            self.fpga_time_label.setText('板内图执行：' + (f'{graph:.2f} ms' if graph is not None else '不可用')
                                         + '\n上方为图像收发端到端；不含模型加载/统计查询。')
            self.fpga_time_label.show()
            self.threshold_info.setText('固定整数 logits：q ≥ 1（模型端）')
            self.threshold_info.setWordWrap(True)
            hardware = result.metadata.get('hardware_declaration')
            from .hardware import hardware_label as format_hardware_label
            hardware_label = format_hardware_label(hardware)
            self.resource_label.setText(resource_summary(hardware))
            self.resource_label.setToolTip(json.dumps(hardware, ensure_ascii=False, indent=2) if hardware else '')
            identity = ('模型：' + result.metadata.get('model_version', 'HW_R1，详细版本未记录')
                        + '\n硬件：' + hardware_label)
            self.version_label.setText(identity.replace('_', '_\u200b').replace('/', '/\u200b'))
            self.version_label.setToolTip(identity)
            self.panels[0].title_label.setText('01  模型实际输入')
            self.panels[0].title = '01  模型实际输入'
            self.panels[0].set_detail('模型实际输入：256×256 RGB888，未应用展示增强；测量单位为模型网格 px')
        else:
            self.fpga_time_label.hide()
            self.resource_label.setText('当前为软件参考结果；不展示 FPGA 资源声明。')
            self.resource_label.setToolTip('')
            threshold = result.metadata.get("effective_threshold")
            self.threshold_info.setText(f"实际分割阈值：{threshold:g}" if threshold is not None else "实际分割阈值：—")
            self.version_label.setText(f"结果后端\n{result.capabilities.name}\n{result.capabilities.algorithm_version}")
            self.panels[0].title_label.setText('01  原始影像')
            self.panels[0].title = '01  原始影像'
            self.panels[0].set_detail('显示经方向校正的原图')
        self.calibration_label.setText(f"物理尺度：未标定\n指标来源：{result.measurements.source}")
        self.linked_views.align()

    def choose_record(self):
        if self.busy:
            return
        folder = QFileDialog.getExistingDirectory(self, "打开处理记录目录（包含 record.json）",
                                                   self.preferences.directory(False))
        if folder:
            self.start_open_record(Path(folder))

    def start_open_record(self, directory):
        if self.busy:
            return False
        directory = Path(directory)
        self.set_status(f"正在打开记录：{directory.name} · 校验成功后替换当前内容。")
        self._start_task("record", uuid4().hex, lambda: load_record(directory))
        return True

    def commit_record(self, candidate: LoadedRecord):
        candidate.validate()
        pixmaps, overlay = self._prepare_result_display(candidate.request, candidate.result)
        # All four scenes and Qt display resources are prepared before clearing old state.
        scenes = []
        for panel, pixmap in zip(self.panels, pixmaps):
            scene = QGraphicsScene(panel.view)
            panel.view.add_pixmap(pixmap, scene)
            scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())
            scenes.append(scene)
        editing = self._editing_before_record if self.record_view_only else (self.selected_config(), self.threshold_spin.value())
        self.clear_result()
        self.image = candidate.request.image_bgr
        self.source_path = candidate.request.source_path
        self.is_synthetic = candidate.request.is_synthetic
        self.request, self.result = candidate.request, candidate.result
        self.show_input_quality(np.ascontiguousarray(candidate.request.image_bgr[:,:,::-1]).tobytes(),candidate.request.source_path.name,pending=False)
        self.linked_views._updating = True
        try:
            for panel, scene, pixmap in zip(self.panels, scenes, pixmaps):
                view = panel.view
                previous = view.scene
                view._overlay_item = view._overlay_base = None
                view.scene = scene
                view.setScene(scene)
                previous.deleteLater()
                view.pixmap, view.has_image = pixmap, True
                view.placeholder.hide()
                view.fit_image()
                view.image_changed.emit()
            self._show_result(candidate.result, pixmaps, overlay)
        finally:
            self.linked_views._updating = False
        self.opened_record = candidate
        from .roi_edge import reminder_text
        text,warning=reminder_text(candidate.result.metadata,candidate.result.mask,historical=True)
        self.edge_reminder.setText(text);self.edge_reminder.setVisible(bool(text))
        set_fluent_property(self.edge_reminder,'fluentSeverity','warning' if warning else 'info')
        self._editing_before_record = editing
        config = candidate.request.config
        if isinstance(config, ProcessingConfig):
            self._set_config_fields(config, self.threshold_spin.value())
        self._rebuild_presets()
        self.record_view_only = True
        backend = candidate.result.capabilities
        self.notice.setText("记录查看：显示保存时的结果、参数与耗时，不重新运行算法。")
        if isinstance(config, BackendConfig):
            parameters = f"配置版本 {config.config_version} · 参数详见技术详情和 record.json"
            self.generic_parameters.setPlainText(json.dumps(dict(config.values), ensure_ascii=False, indent=2))
        else:
            parameters = (f"CLAHE {config.clahe_clip:g} · 阈值 {config.threshold if config.threshold is not None else '自动'}"
                          f" · 最小区域 {config.min_region_pixels}")
        self.record_label.setText(f"记录查看 · {backend.name} / {backend.algorithm_version} · 保存：{candidate.document['saved_at']}"
                                  f"\n原参数：{parameters} · 目录：{candidate.directory}")
        self.file_label.setText(f"记录原图：{candidate.request.source_path.name} · {self.image.shape[1]} × {self.image.shape[0]} px"
                                + (" · 合成样例，非真实皮肤镜影像" if self.is_synthetic else ""))
        if backend.key == 'fpga_formal_v5':
            meta = candidate.result.metadata
            source = ('本机协议模拟记录' if meta.get('source_kind') == 'protocol_simulator' else
                      '离线实板采集记录' if meta.get('display_mode') == 'offline_replay' else '已保存的实时实板采集记录')
            self.notice.setText(f'记录查看 · {source} · 本次未连接 FPGA。')
            details = (self.record_label.text() + f"\nframe {meta.get('frame_id', '未记录')} / generation {meta.get('generation', '未记录')}"
                       f"\n输入 SHA256：{meta.get('input_sha256', '未记录')}"
                       '\n增强图仅用于展示，模型输入未增强；分割为原始 256×256 二值结果。')
            self.record_label.setText(f"记录查看 · {source}")
            self.record_label.setToolTip(details)
            self.technical_origin.setText(details)
            self.file_label.setText(self.file_label.text() + ' · 模型实际输入网格，非原文件分辨率')
        self.set_status("记录已打开 · 原结果与耗时已恢复，未运行算法；可查看、另存或使用原图继续处理。")
        self.linked_views.align()

    def continue_from_record(self):
        if self.busy or not self.record_view_only:
            return
        if self.delivery_mode and not self.reference_mode:
            self.open_formal_device()
            return
        self.record_view_only = False
        self._formal_view = False
        if self.result.capabilities.key == self.backend.capabilities.key and isinstance(self.request.config, ProcessingConfig):
            self.apply_config(self.request.config)
        elif self._editing_before_record is not None:
            self.apply_config(*self._editing_before_record)
        self._rebuild_presets()
        self._editing_before_record = None
        self.notice.setText(TEMPORARY_NOTICE)
        self.record_label.setText(self.record_label.text().replace("记录查看", "原记录结果", 1))
        self.set_status(f"已进入处理模式 · 当前后端：{self.backend.capabilities.name}；按 Enter 才生成新结果。")
        self._refresh_controls()

    def open_timing(self) -> None:
        if self.request is None or self.result is None:
            return
        if self.timing_dialog is None:
            self.timing_dialog = TimingDialog(self.request, self.result, self)
            if self.opened_record is not None:
                self.timing_dialog.setWindowTitle("记录处理耗时详情")
            self.timing_dialog.finished.connect(self._timing_closed)
        self.timing_dialog.show()
        self.timing_dialog.raise_()
        self.timing_dialog.activateWindow()

    def _timing_closed(self, _result: int) -> None:
        self.timing_dialog = None

    def close_timing(self) -> None:
        if self.timing_dialog is not None:
            self.timing_dialog.close()

    @Slot()
    def parameters_changed(self, *_args) -> None:
        if self.busy or self.record_view_only:
            return
        if not self._applying_config:
            blocker = QSignalBlocker(self.preset_combo)
            self.preset_combo.setCurrentIndex(0)
            del blocker
        if self.result is not None:
            if self.parameters_pending:
                self.set_status("参数尚未应用 · 当前保留上一次处理结果；点击“开始处理”后更新。")
            else:
                self.set_status("参数与当前结果一致 · 可继续查看或保存当前结果。")
        self._refresh_controls()

    def selected_config(self) -> ProcessingConfig:
        return ProcessingConfig(
            clahe_clip=self.clip_spin.value(),
            threshold=self.threshold_spin.value() if self.threshold_mode.currentIndex() == 1 else None,
            min_region_pixels=self.region_spin.value(),
        )

    def apply_config(self, config: ProcessingConfig, manual_draft: int | None = None) -> None:
        if self.busy or self.record_view_only:
            return
        config.validate()
        config = parse_config(asdict(config))
        self._set_config_fields(config, manual_draft)
        self._applying_config = True
        try:
            self.parameters_changed()
        finally:
            self._applying_config = False

    def _set_config_fields(self, config, manual_draft=None):
        """Internal atomic control update, also used for read-only record snapshots."""
        blockers = [QSignalBlocker(widget) for widget in (
            self.clip_spin, self.threshold_mode, self.threshold_spin, self.region_spin)]
        self.clip_spin.setValue(config.clahe_clip)
        self.threshold_mode.setCurrentIndex(0 if config.threshold is None else 1)
        if config.threshold is not None:
            self.threshold_spin.setValue(config.threshold)
        elif manual_draft is not None:
            self.threshold_spin.setValue(manual_draft)
        self.region_spin.setValue(config.min_region_pixels)
        del blockers

    def restore_defaults(self) -> None:
        if self.busy or self.record_view_only:
            return
        self.apply_config(ProcessingConfig(), manual_draft=110)
        self._rebuild_presets("builtin:auto")

    def restore_result_config(self) -> None:
        if (not self.busy and not self.record_view_only and self.result is not None and self.request is not None
                and self.result.capabilities.key == self.backend.capabilities.key and isinstance(self.request.config, ProcessingConfig)):
            self.apply_config(self.request.config)
            self._rebuild_presets()

    def _show_settings_warning(self) -> None:
        self.settings_label.setText(self.preferences.warning)
        self.settings_label.setVisible(bool(self.preferences.warning))

    def _restore_geometry(self) -> None:
        restored = bool(self.preferences.geometry) and self.restoreGeometry(self.preferences.geometry)
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry()
        if not restored:
            self.resize(min(1400, available.width()), min(920, available.height()))
        # Reposition a recovered window when its monitor is gone.
        if not any(s.availableGeometry().intersects(self.frameGeometry()) for s in QApplication.screens()):
            self.move(available.topLeft())
        elif not self.isMaximized():
            self.resize(min(self.width(), available.width()), min(self.height(), available.height()))
            x = max(available.left(), min(self.x(), available.right() - self.width() + 1))
            y = max(available.top(), min(self.y(), available.bottom() - self.height() + 1))
            self.move(x, y)

    def _rebuild_presets(self, selected: str | None = None) -> None:
        blocker = QSignalBlocker(self.preset_combo)
        self.preset_combo.clear()
        self.preset_combo.addItem("当前参数", None)
        self.preset_combo.addItem("默认自动", "builtin:auto")
        self.preset_combo.addItem("默认手动", "builtin:manual")
        for preset in self.preferences.presets:
            self.preset_combo.addItem(preset.name, preset.id)
        self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findData(selected)))
        del blocker
        self._refresh_controls()

    def select_preset(self, _index: int) -> None:
        identifier = self.preset_combo.currentData()
        if identifier == "builtin:auto":
            self.apply_config(ProcessingConfig())
        elif identifier == "builtin:manual":
            self.apply_config(ProcessingConfig(threshold=110))
        else:
            for preset in self.preferences.presets:
                if preset.id == identifier:
                    self.apply_config(preset.config)
                    break
        self._refresh_controls()

    def save_preset(self, name: str) -> bool:
        if self.busy or self.record_view_only:
            return False
        try:
            for spin in (self.clip_spin, self.threshold_spin, self.region_spin):
                spin.interpretText()
            preset = self.preferences.add_preset(name, self.selected_config())
            self._rebuild_presets(preset.id)
            self.set_status(f"预设已保存：{preset.name}" if not self.preferences.warning else
                            f"预设已加入本次会话：{preset.name}")
            self._show_settings_warning()
            return True
        except ValueError as exc:
            self.set_status(str(exc), error=True)
            return False

    def prompt_save_preset(self) -> None:
        if self.busy or self.record_view_only:
            return
        name, accepted = QInputDialog.getText(self, "保存参数预设", "名称（1–40 个字符，同名不可覆盖）")
        if accepted:
            self.save_preset(name)

    def delete_selected_preset(self) -> None:
        if self.busy or self.record_view_only:
            return
        try:
            self.preferences.delete_preset(self.preset_combo.currentData())
            self._rebuild_presets()
            self._show_settings_warning()
            self.set_status("已删除用户预设 · 当前参数和结果保持不变。")
        except ValueError as exc:
            self.set_status(str(exc), error=True)

    @property
    def parameters_pending(self) -> bool:
        return (
            not self.record_view_only and self.result is not None and self.request is not None
            and self.selected_config() != self.request.config
        )

    @Slot()
    def choose_image(self) -> None:
        if self.busy:
            return
        if self.delivery_mode and not self.reference_mode:
            self.open_formal_device()
            self.formal_dialog.choose_image()
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "导入皮肤镜影像", self.preferences.directory(True), "图像文件 (*.png *.jpg *.jpeg)"
        )
        if filename:
            self.load_path(Path(filename))

    def load_path(self, path: Path, is_synthetic: bool = False) -> bool:
        """Compatibility alias: True means accepted, not successfully loaded."""
        return self.start_import(path, is_synthetic)

    def start_import(self, path: Path, is_synthetic: bool = False) -> bool:
        if self.busy:
            return False
        path = Path(path)
        synthetic = is_synthetic or path.resolve() == SAMPLE_PATH.resolve()
        self.set_status(f"正在读取：{path.name} · 读取成功后替换当前图片。")
        self._start_task("load", uuid4().hex, lambda: load_candidate(path, synthetic))
        return True

    def commit_loaded_image(self, candidate: LoadedImage) -> None:
        candidate.validate()
        import_directory = candidate.source_path.resolve().parent
        started = perf_counter()
        pixmap = ImageView.prepare_pixmap(candidate.image_bgr)
        # Prepare all fallible resources before changing the previous state.
        scene = QGraphicsScene(self.panels[0].view)
        self.panels[0].view.add_pixmap(pixmap, scene)
        scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())
        self.clear_result()
        self.image, self.source_path = candidate.image_bgr, candidate.source_path
        self.is_synthetic = candidate.is_synthetic
        view = self.panels[0].view
        previous_scene = view.scene
        view.scene = scene
        view.setScene(scene)
        previous_scene.deleteLater()
        view.pixmap, view.has_image = pixmap, True
        view.placeholder.hide()
        view.fit_image()
        view.image_changed.emit()
        height, width = self.image.shape[:2]
        marker = " · 合成样例，非真实皮肤镜影像" if self.is_synthetic else ""
        self.file_label.setText(f"当前文件：{self.source_path.name}   ·   {width} × {height} px{marker}")
        self._import_display_ms = (perf_counter() - started) * 1000
        self.set_status("已载入 · 点击“开始处理”，或调整右侧参数。")
        self.preferences.remember_directory(import_directory, True)
        self._show_settings_warning()

    @Slot()
    def start_processing(self) -> None:
        if self.delivery_mode and not self.reference_mode:
            self.open_formal_device()
            return
        if self.busy or self.record_view_only or self.image is None or self.source_path is None:
            return
        for spin in (self.clip_spin, self.threshold_spin, self.region_spin):
            spin.interpretText()
        self.clear_result()
        config = self.selected_config()
        self.request = ProcessingRequest(
            image_bgr=self.image, source_path=self.source_path, config=config,
            is_synthetic=self.is_synthetic,
        )
        request = self.request
        self.set_status("正在处理 · 图像视图仍可缩放；本次输入和参数已固定。")
        self._submitted_clock = perf_counter()
        self._start_task("process", request.task_id, lambda: self.backend.process(request))

    def _start_task(self, kind: str, task_id: str, action: Callable) -> None:
        self._active_job_id = task_id
        self._thread = TaskThread(kind, task_id, action, self)
        self._thread.succeeded.connect(self._handle_success)
        self._thread.failed.connect(self._handle_failure)
        self._thread.finished.connect(self._thread_finished)
        self._refresh_controls()
        self._thread.start()

    @Slot(str, str, object)
    def _handle_success(self, kind: str, task_id: str, value) -> None:
        if (task_id != self._active_job_id or self._thread is None
                or kind != self._thread.kind):
            return
        if kind == "load":
            try:
                self.commit_loaded_image(value)
            except Exception as exc:
                record_exception("display_failed", exc, job_id=task_id, stage=kind)
                self._handle_failure(kind, task_id, str(exc))
            return
        if kind == "record":
            try:
                self.commit_record(value)
            except Exception as exc:
                record_exception("display_failed", exc, job_id=task_id, stage=kind)
                self._handle_failure(kind, task_id, str(exc))
            return
        if kind == "save":
            self.last_export = value
            self.preferences.remember_directory(Path(value).resolve().parent, False)
            self._show_settings_warning()
            pending = "已保存当前显示的结果；新参数尚未应用 · " if self.parameters_pending else ""
            self.set_status(f"保存成功 · {pending}{value}")
            return
        if self.request is None:
            return
        try:
            value.validate(self.request)
            if self._submitted_clock is not None:
                value = replace(
                    value,
                    timings_ms={
                        **value.timings_ms,
                        "backend_pipeline": value.timings_ms["end_to_end"],
                        "end_to_end": (perf_counter() - self._submitted_clock) * 1000,
                    },
                    metadata={
                        **value.metadata,
                        "timing_scope": "application_submit_to_validated_result_excluding_display",
                    },
                )
            pixmaps, overlay = self._prepare_result_display(self.request, value)
            self._show_result(value, pixmaps, overlay)
            self.set_status("处理完成 · 可保存本次结果。" if value.measurements.area_px2 else
                            "处理完成 · 未检测到有效前景区域；面积为 0，最大径不适用。")
        except Exception as exc:
            record_exception("display_failed", exc, job_id=task_id, stage=kind)
            self.result = None
            for panel in self.panels[1:]:
                panel.view.clear_image()
            for label in (self.area_label, self.diameter_label, self.time_label):
                label.setText("—")
            self.threshold_info.setText("实际分割阈值：—")
            self._handle_failure(kind, task_id, str(exc))

    @Slot(str, str, str)
    def _handle_failure(self, kind: str, task_id: str, message: str) -> None:
        if (task_id != self._active_job_id or self._thread is None
                or kind != self._thread.kind):
            return
        action = {"load": "读取", "record": "打开记录", "process": "处理", "save": "保存"}[kind]
        error_id = error_for(task_id)
        self.set_status(f"{action}失败 · {message}" + (f" · 错误编号 {error_id}" if error_id else ""), error=True)

    @Slot()
    def _thread_finished(self) -> None:
        if self.sender() is not self._thread:
            return
        thread = self._thread
        self._thread = None
        self._active_job_id = None
        if thread is not None:
            thread.deleteLater()
        self._refresh_controls()

    @Slot()
    def choose_export(self) -> None:
        if self.busy or self.result is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "选择保存位置（将新建独立记录目录）", self.preferences.directory(False))
        if folder:
            self.export_to(Path(folder))

    def export_to(self, parent: Path) -> None:
        if self.busy or self.request is None or self.result is None:
            return
        request, result = self.request, self.result
        self.set_status("正在保存 · 将写入四张 PNG 和一份 JSON 记录。")
        origin = self.opened_record.document if self.opened_record is not None else None
        self._start_task("save", uuid4().hex, lambda: save_result(parent, request, result, origin_record=origin))

    def closeEvent(self, event: QCloseEvent) -> None:
        for dialog in (self.queue_dialog, self.validation_dialog, self.ai_dialog, self.review_dialog,self.cpu_settings_dialog,self.report_dialog,
                       getattr(self,'hardware_lab_dialog',None)):
            if dialog is not None:
                was_busy = dialog.busy
                closed=dialog.close()
                if not closed and not was_busy:
                    event.ignore();return
                if was_busy:
                    event.ignore()
                    self.set_status('正在结束分析/队列任务；完成后可退出。')
                    return
        if self.classification_dialog is not None:
            try:
                class_busy=self.classification_dialog.busy
                self.classification_dialog.close()
                if class_busy:event.ignore();return
            except RuntimeError:self.classification_dialog=None
        if self.cpu_dialog is not None:
            cpu_busy = self.cpu_dialog.busy
            self.cpu_dialog.close()
            if cpu_busy:
                event.ignore();self.set_status('正在结束CPU工作进程；完成后可退出。');return
        if self.roi_dialog is not None:
            roi_busy = self.roi_dialog.busy
            self.roi_dialog.close()
            if roi_busy:
                event.ignore()
                self.set_status('正在结束ROI工作区任务；完成后可退出。')
                return
        if self.formal_dialog is not None:
            formal_busy = self.formal_dialog.busy
            self.formal_dialog.close()
            if formal_busy:
                event.ignore()
                self.set_status("正在停止正式 Mask 的 PC 等待；清理完成后可退出。")
                return
        if self.diagnostics_dialog is not None:
            diagnostics_busy = self.diagnostics_dialog.busy
            self.diagnostics_dialog.close()
            if diagnostics_busy:
                event.ignore()
                self.set_status("正在完成诊断导出 · 完成后可退出主窗口。")
                return
        if self.rgb_device_dialog is not None:
            rgb_busy = self.rgb_device_dialog.busy
            self.rgb_device_dialog.close()
            if rgb_busy:
                event.ignore()
                self.set_status("正在停止 RGB 收发 · 窗口清理完成后可退出；板卡可能需按 S0。")
                return
        if self.device_dialog is not None:
            device_busy = self.device_dialog.busy
            self.device_dialog.close()
            if device_busy:
                event.ignore()
                self.set_status("正在结束本机通信任务 · 诊断窗口关闭后可退出主窗口。")
                return
        if self.busy:
            event.ignore()
            self.set_status("当前任务尚未结束 · 请在读取、处理或保存完成后关闭窗口。")
        else:
            for spin in (self.clip_spin, self.threshold_spin, self.region_spin):
                spin.interpretText()
            config, draft = (self._editing_before_record if self.record_view_only and self._editing_before_record is not None
                             else (self.selected_config(), self.threshold_spin.value()))
            self.preferences.save_session(config, draft, self.saveGeometry())
            self._show_settings_warning()
            self.close_timing()
            for panel in self.panels:
                panel.close_viewer()
            event.accept()


def create_application() -> QApplication:
    app = QApplication.instance() or QApplication([])
    # Qt's offscreen Windows platform does not enumerate system fonts.
    # Register local fonts when discovery is unavailable, without bundling them.
    if os.name == "nt" and not QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
        for filename in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "segoeuib.ttf"):
            path = fonts / filename
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
    app.setApplicationName("Derma Image Lab")
    app.setOrganizationName("FPGA Demo")
    if not hasattr(app, "fluent_theme"):
        app.setStyle("Fusion")
        app.fluent_theme = install_theme(app)
    return app
