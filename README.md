# FPGA皮肤镜影像分割与分析平台

基于ACX750-200T的完整INT8分割网络、UDP以太网收发与PC交互分析系统。仅用于工程教学与算法演示，不作为临床诊断依据。

## 目录

- `hardware/rtl`：P7会话模块。
- `hardware/shared`：CNN、DDR搬运、CRC、协议及缓存等RTL。
- `hardware/board`：板级顶层、约束、MIG配置与实现辅助脚本。
- `hardware/generated`：配置及计算图连接头文件；参数和描述符数据文件不包含。
- `software/dermademo`：v0.16.1-rc.1 PC工作台源码。
- `software/integrations`：整图、ROI、CPU分割、分类及AI的算法适配代码，不含权重和参考数据。
- `software/tests`：对应软件测试源码；部分测试需要外部资产，不能视为本包已通过全量测试。
- `docs`：核心代码导读、依赖与发布步骤。

## 已有功能

JPEG/PNG正式输入、FPGA整网分割、四窗显示、人工ROI与触边提醒、人工修订及测量、输入质量提醒、独立CPU整图分类、多图队列、参考对照、云端AI教学分析、统一PDF及软硬件对照实验台。

计算流程：PC预处理 → UDP → FPGA输入LUT与完整INT8 CNN ↔ DDR → 原始Mask回传。PC负责测量、复核、分类调度与报告；云端AI不修改Mask。

## 当前性能与范围

P7固定样例板内图约520.23 ms。一次同输入实验的20轮正式测量中，CPU共同RGB→Mask均值23.925 ms，FPGA图像收发边界均值668.830 ms。两者精度与计时范围见实验台说明，不能称为纯算子加速比。当前未达持续5 FPS，无实测能效结论。

## 运行和构建

本包是核心源码整理版，不是含全部模型资产的一键运行发行版。软件可在准备相容Python环境后，按 `software/requirements-runtime.lock.txt` 安装依赖，从software目录执行 `python -m dermademo`；模型/参考资源不齐时对应入口可能不可用。不要在导入测试时连接板卡或填写真实云端密钥。

硬件需要Vivado、对应AMD IP、板卡配置、计算图描述符和外部模型资产。此处未提供已验证的完整独立重建脚本，不承诺直接生成与P7相同bit。板级Tcl保留原实现辅助用途，先检查路径再使用。

详见 `docs/DEPENDENCIES.md`。本次整理只做源码语法、文件校验与敏感内容规则扫描，未重新综合、烧录或执行全量回归。

## 许可

见 `THIRD_PARTY_NOTICES.md` 和 `docs/RELEASE_CHECKLIST.md`。团队自有代码的开源许可证尚需共同确认；本整理包未擅自将全体代码授权为MIT。仓库公开与授予开源许可是两项不同事项。
