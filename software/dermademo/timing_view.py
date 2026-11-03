"""Read-only timing details for the currently displayed processing snapshot."""

from math import isfinite

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from .contracts import ProcessingRequest, ProcessingResult


TIMING_FIELDS = (
    ("preprocessing", "预处理"), ("backend_compute", "后端计算"),
    ("postprocessing", "后处理"), ("backend_pipeline", "后端阶段总耗时"),
    ("end_to_end", "应用端到端"),
    ("fpga_graph", "板内图执行（LUT/CNN/DDR）"),
    ("image_transport_e2e", "图像收发端到端"),
    ("model_upload", "模型参数上传"), ("model_verify", "DDR 模型 CRC 校验"),
    ("rgb_upload", "RGB 上传"), ("result_wait", "主机观察的首块等待"),
    ("telemetry", "统计查询（不计入图像端到端）"),
    ("pc_presentation", "PC 展示数据准备"),
)


def timing_rows(timings: dict) -> list[tuple[str, str, str]]:
    rows = []
    for key, caption in TIMING_FIELDS:
        if key not in timings:
            value = "不可用"
        else:
            number = timings[key]
            if type(number) not in (int, float) or not isfinite(number) or number < 0:
                raise ValueError("处理耗时无效")
            value = f"{number:.2f} ms"
        rows.append((key, caption, value))
    return rows


class TimingDialog(QDialog):
    def __init__(self, request: ProcessingRequest, result: ProcessingResult, parent=None):
        super().__init__(parent)
        self.setWindowTitle("本次处理耗时详情")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumWidth(480)
        self.request, self.result = request, result
        layout = QVBoxLayout(self)
        height, width = request.image_bgr.shape[:2]
        summary = QLabel(f"任务：{result.task_id}\n后端：{result.capabilities.name}\n"
                         f"版本：{result.capabilities.algorithm_version}\n输入：{width} × {height} px")
        summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        form = QFormLayout()
        self.values = {}
        for key, caption, value in timing_rows(dict(result.timings_ms)):
            if key not in dict(TIMING_FIELDS[:5]) and result.capabilities.key != 'fpga_formal_v5':
                continue
            label = QLabel(value)
            label.setObjectName(f"timing_{key}")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.values[key] = label
            form.addRow(caption, label)
        layout.addLayout(form)
        detail = ("预处理：输入校验、灰度转换和高斯平滑。\n"
                       "后端计算：CLAHE 增强与阈值分割。\n"
                       "后处理：区域选择、量化和轮廓叠加。\n"
                       "阶段总耗时：上述三个阶段，不含结果校验。\n"
                       "端到端：提交已读取的图片至主线程校验结果，含任务调度；"
                       "不含读取、界面绘制和保存。两类总耗时不必相等。")
        if result.capabilities.key != "demo_cpu" or result.capabilities.algorithm_version != "clahe-otsu-demo/0.1.0":
            detail = "以上为记录后端提供的原始耗时，阶段含义以原后端说明为准；重开记录不会重新计时。"
        if result.capabilities.key == 'fpga_formal_v5':
            detail = ('板内图执行：100MHz 时钟，含 RGB LUT、CNN 和 DDR 等待；不是纯推理。\n'
                      '图像端到端：IMAGE_BEGIN 至合法 FINISH，含网络与等待；排除模型加载和 249 页统计。\n'
                      '应用端到端栏沿用该图像事务口径。模型上传/校验、统计查询、PC 展示准备独立列出。\n'
                      '缺失数据保持不可用；离线回放显示原记录时间，不重新测量、不认定 5FPS。')
        scope = QLabel(detail + "\n计时标识：" + str(result.metadata.get("timing_scope", "未提供")))
        scope.setWordWrap(True)
        scope.setObjectName("muted")
        layout.addWidget(scope)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
