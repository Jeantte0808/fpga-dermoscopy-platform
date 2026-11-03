"""Per-frame user declarations. FCP1 does not identify the running bitstream."""

import json

from .resources import resource_path


PROFILE_CHOICES = (('未声明 · 不推断当前位流', 'unspecified'),
                   ('用户确认 · 旧 Flash V6 R1', 'v6-r1-flash'),
                   ('用户确认 · P7 临时下载候选', 'p7'),
                   ('部署配置 · P7 Flash固化版', 'p7-flash'))

DEPLOYMENT_PROFILE = 'p7-flash'


def hardware_declaration(profile='unspecified'):
    if profile not in dict((key, label) for label, key in PROFILE_CHOICES):
        raise ValueError('硬件声明选项无效')
    if profile == 'unspecified':
        return None
    if profile == 'v6-r1-flash':
        value = json.loads((resource_path('full_image_root') / 'hardware_display_reference.json').read_text('utf-8'))['hardware']
    else:
        value = json.loads(resource_path('p7_hardware').read_text('utf-8'))
    if profile == 'p7-flash':
        value.update(candidate='formal_perf_p7_flash', snapshot_date='2026-10-30',
                     bit_sha256='758e81d28d872e9f0fa8e0d9b988cb49fd938179e534e82df9351abb0a85ff8d',
                     mcs_sha256='678cb92577f370b73e62c354c2c4b57161c742f4e542a9cd4455bcafe8c22343',
                     temporary_bit_sha256='6ba20d7c1154b49957c89028d2ef0536aa9668269f48a7e0c7dc72fd59e339fe',
                     programming='Flash_SPIx1_3MHz_CCLK_user_confirmed', P7_Flash_programmed=True,
                     flash_cold_boot_functional_pass=True, cold_boot_evidence_source='user_feedback_only_no_independent_capture_or_timing',
                     cold_boot_elapsed_ms=None, DDR_weights_persist_across_power_cycle=False,
                     acceptance_scope='user_reported_power_cycle_then_first_model_upload_CRC_inference_success',
                     performance_evidence='historical_P7_temporary_bit_5_frames_520.227412ms_not_new_Flash_measurement')
    value.update(declaration_profile=profile, declaration_source='user_selected_programming_declaration',
                 hardware_identity_verified=False)
    if profile == 'p7-flash':
        value['declaration_source'] = 'demo_deployment_configuration_user_confirmed_20261030'
    return value


def hardware_label(value):
    if not value:
        return '旧记录未记录或本帧未声明；不推断当前位流'
    source = ('演示部署配置，用户确认固化，非自动识别' if value.get('declaration_profile') == 'p7-flash' else
              '用户确认烧录，非自动识别' if value.get('declaration_source') == 'user_selected_programming_declaration'
              else '原记录静态声明，未远程认证')
    return value.get('candidate', '未记录') + '（' + source + '）'


def resource_summary(hardware=None):
    """Render the supplied frame snapshot only; never substitute latest resources."""
    if not hardware:
        return '硬件资源：本帧未声明或旧记录未记录。\n请选择与实际烧录一致的声明；程序无法自动识别位流。'
    values = hardware.get('resources')
    if not values:
        return hardware_label(hardware) + '\n资源数据不可用。'
    p7 = hardware.get('candidate') == 'formal_perf_p7_nearest'
    return (('P7 Flash固化版 · 冷启动后需首次上传模型/CRC' if hardware.get('declaration_profile') == 'p7-flash' else
             'P7 临时下载候选 · 未固化 Flash' if p7 else '旧 Flash / V6 R1 静态资源参考')
            + '\n' + hardware_label(hardware)
            + f"\n{hardware.get('FPGA', '型号未记录')} · {hardware.get('graph_clock_hz', 0)/1e6:g} MHz"
            + f"\nLUT {values['lut']:,.0f} · FF {values['registers']:,.0f}"
            + f"\nBRAM36 等效 {values['bram36_equivalent']:g} · DSP {values['dsp']:g}"
            + f"\n内部时序 setup {hardware.get('setup_ns', '不可用')} ns / hold {hardware.get('hold_ns', '不可用')} ns"
            + '\n整板 routed 静态统计；外部 RGMII 完整时序未签核。持续 5FPS 未验收。')
