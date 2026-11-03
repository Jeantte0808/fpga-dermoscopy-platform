# 核心代码导读
建议先读系统入口，再读协议与计算，最后读实验与报告。

## 完整计算图
硬件计算阶段调度与连接
- `hardware/shared/formal_graph_control.v`
- `hardware/shared/formal_graph_engine.v`

## 卷积与DDR
卷积/存储访问
- `hardware/shared/formal_conv_ddr.v`

## 会话与协议
帧、模型与传输控制
- `hardware/rtl/formal_ethernet_session.v`
- `hardware/board/formal_ethernet_board_session.v`
- `hardware/board/formal_ethernet_top.v`

## 硬件实验台
重复测量、逐帧身份、阶段证据与技术PDF
- `software/dermademo/hardware_lab.py`
- `software/dermademo/hardware_lab_cli.py`
- `software/dermademo/hardware_lab_pdf.py`
- `software/dermademo/hardware_lab_ui.py`
- `software/tests/test_hardware_lab.py`
- `software/tests/test_hardware_lab_gui.py`

## ROI与人工复核
人工选框及成功帧上下文
- `software/dermademo/roi.py`
- `software/dermademo/roi_chain.py`
- `software/dermademo/roi_edge.py`
- `software/dermademo/roi_edge_contact.py`
- `software/dermademo/roi_expansion.py`
- `software/dermademo/roi_preprocessing.py`
- `software/dermademo/roi_selector.py`
- `software/dermademo/roi_ui.py`
- `software/tests/roi_test_support.py`
- `software/tests/test_roi.py`
- `software/tests/test_roi_confirmed.py`
- `software/tests/test_roi_edge.py`

## AI及报告
事实约束、请求确认与解读
- `software/dermademo/ai_content_policy.py`
- `software/dermademo/ai_diagnostics.py`
- `software/dermademo/ai_protocol.py`
- `software/dermademo/ai_recover_cli.py`
- `software/dermademo/ai_report_display.py`
- `software/dermademo/ai_service.py`
- `software/dermademo/ai_transport.py`
- `software/dermademo/ai_ui.py`
- `software/tests/test_ai_draft_recovery.py`
- `software/tests/test_ai_source_review.py`
- `software/tests/test_ai_transport.py`
- `software/integrations/ai_draft_r2/dermademo/ai_content_policy.py`

## 参考库核对
身份匹配与原始Mask对照
- `software/dermademo/preferences.py`
- `software/dermademo/reference_library.py`
- `software/tests/test_demo100_reference.py`
- `software/tests/test_preferences.py`
- `software/tests/test_queue_reference_import.py`
- `software/integrations/full_image_20261025/software/official_reference_loader.py`
- `software/integrations/roi_int8_r1/software/official_reference_loader.py`
