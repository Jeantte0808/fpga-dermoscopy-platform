"""Read schema 1.0/1.1/1.2 exports without inference or Qt API calls."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from math import isclose, isfinite
from pathlib import Path

import numpy as np
from PIL import Image

from .contracts import BackendCapabilities, BackendConfig, Measurements, ProcessingRequest, ProcessingResult
from .imaging import MAX_IMAGE_PIXELS, max_feret_diameter
from .configuration import parse_parameters
from .storage import file_sha256, MAX_RECORD_JSON_BYTES


@dataclass(frozen=True)
class LoadedRecord:
    directory: Path
    document: dict
    request: ProcessingRequest
    result: ProcessingResult

    def validate(self) -> None:
        self.result.validate(self.request)


def _text(value, caption: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(c in value for c in "\r\n\x00"):
        raise ValueError(f"记录中的{caption}无效")
    return value


def _timestamp(value, caption):
    value = _text(value, caption)
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError()
    except ValueError as exc:
        raise ValueError(f"记录中的{caption}无效") from exc
    return value


def _reject_constant(value):
    raise ValueError(f"JSON 含有无效数值：{value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 字段重复：{key}")
        result[key] = value
    return result


def load_record(directory: Path) -> LoadedRecord:
    """Validate the complete record before returning a candidate for GUI commit."""
    directory = Path(directory).resolve()
    try:
        if (directory / "INCOMPLETE.txt").exists():
            raise ValueError("记录带有 INCOMPLETE.txt，保存尚未完成")
        manifest = directory / "record.json"
        if manifest.stat().st_size > MAX_RECORD_JSON_BYTES:
            raise ValueError("记录 JSON 超过 8 MiB 限制")
        data = json.loads(manifest.read_text(encoding="utf-8"), parse_constant=_reject_constant,
                          object_pairs_hook=_unique_object)
        if not isinstance(data, dict) or data.get("schema_version") not in {"1.0", "1.1", "1.2"}:
            raise ValueError("不支持此记录格式版本，目前支持 1.0/1.1/1.2")
        _text(data["record_id"], "记录标识")
        task_id = _text(data["task_id"], "任务标识")
        _text(data["application_version"], "应用版本")
        _timestamp(data["saved_at"], "保存时间")
        submitted = _timestamp(data["submitted_at"], "处理时间")
        source = data["source"]
        filename = _text(source["filename"], "原文件名")
        if Path(filename).name != filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
            raise ValueError("原文件名应为单个文件名")
        width, height = source["width"], source["height"]
        if (type(width) is not int or type(height) is not int or min(width, height) < 1
                or width * height > MAX_IMAGE_PIXELS):
            raise ValueError("记录图像尺寸无效或超过 2400 万像素")
        if source["color_space"] != "BGR" or source["orientation"] != "EXIF_normalized":
            raise ValueError("记录颜色空间或方向约定不支持")
        if type(source["is_synthetic"]) is not bool:
            raise ValueError("记录样例标识无效")
        outputs = data["outputs"]
        if not isinstance(outputs, dict) or set(outputs) != {"original", "enhanced", "mask", "overlay"}:
            raise ValueError("记录必须包含四张结果图片")
        checksums = data.get("output_sha256")
        if checksums is not None and (not isinstance(checksums, dict) or set(checksums) != set(outputs)):
            raise ValueError("结果图片校验字段不完整")
        arrays, paths = {}, set()
        for key, name in outputs.items():
            _text(name, "输出路径")
            relative = Path(name)
            if relative.is_absolute() or relative.drive or ".." in relative.parts:
                raise ValueError("结果图片路径必须位于记录目录内")
            path = (directory / relative).resolve()
            if not path.is_relative_to(directory) or path in paths:
                raise ValueError("结果图片路径越界或重复")
            paths.add(path)
            if checksums is not None and file_sha256(path) != checksums[key]:
                raise ValueError(f"{key} 图片校验值与记录不一致")
            with Image.open(path) as image:
                expected_mode = "L" if key == "mask" else "RGB"
                if image.format != "PNG" or image.mode != expected_mode or image.size != (width, height):
                    raise ValueError(f"{key} 图片格式、编码或尺寸与记录不一致")
                array = np.array(image)
                if key != "mask":
                    array = np.ascontiguousarray(array[:, :, ::-1])
                array.setflags(write=False)
                arrays[key] = array
        if sha256(arrays["original"].tobytes()).hexdigest() != source["decoded_bgr_sha256"]:
            raise ValueError("原图校验值与记录不一致")
        backend = data["backend"]
        config = parse_parameters(data["parameters"], backend_key=backend["key"])
        if (data["schema_version"] in ("1.1", "1.2")) != isinstance(config, BackendConfig):
            raise ValueError("参数结构与记录格式版本不匹配")
        for key in ("key", "name", "algorithm_version"):
            _text(backend[key], f"后端 {key}")
        if type(backend["is_placeholder"]) is not bool or backend.get("color_space") != "BGR":
            raise ValueError("后端标识或颜色空间无效")
        capabilities = BackendCapabilities(**{key: backend[key] for key in
            ("key", "name", "algorithm_version", "is_placeholder", "color_space")})
        values = data["measurements"]
        if (type(values["area_px2"]) is not int or values["area_px2"] < 0
                or values["area_unit"] != "px²" or values["diameter_unit"] != "px"
                or values["diameter_definition"] != "max_distance_between_boundary_pixel_centers"):
            raise ValueError("记录指标或计算定义不支持")
        _text(values["source"], "指标来源")
        diameter = values["max_feret_px"]
        if diameter is not None and (type(diameter) not in (int, float) or not isfinite(diameter) or diameter < 0):
            raise ValueError("记录最大径无效")
        timings = data["timings_ms"]
        if (not isinstance(timings, dict) or "end_to_end" not in timings or any(
                type(v) not in (int, float) or not isfinite(v) or v < 0 for v in timings.values())):
            raise ValueError("记录耗时缺失或无效")
        processing = data["processing"]
        if not isinstance(processing, dict) or not isinstance(data["environment"], dict):
            raise ValueError("记录处理信息或环境无效")
        source_bytes = None
        if capabilities.key == 'fpga_formal_v5':
            raw_artifacts = data.get('raw_artifacts', {})
            expected = {'input.rgb': np.ascontiguousarray(arrays['original'][:, :, ::-1]).tobytes(),
                        'mask.raw': arrays['mask'].tobytes()}
            if set(raw_artifacts) != set(expected):
                raise ValueError('正式 Mask 记录缺少原始输入/Mask 摘要')
            for name, raw in expected.items():
                if (directory / name).read_bytes() != raw or raw_artifacts[name] != {
                        'bytes': len(raw), 'sha256': sha256(raw).hexdigest()}:
                    raise ValueError('正式 Mask 原始字节与四窗记录不一致')
            context = processing.get('input_context')
            if context is not None:
                from .formal_input import validate_source
                artifact = data.get('source_artifact', {})
                if artifact.get('path') != 'source_image.bin':
                    raise ValueError('整图记录缺少原始图片快照')
                source_bytes = (directory / 'source_image.bin').read_bytes()
                if (artifact.get('bytes') != len(source_bytes)
                        or artifact.get('sha256') != sha256(source_bytes).hexdigest()):
                    raise ValueError('原始图片快照校验不符')
                validate_source(context, expected['input.rgb'], source_bytes)
            elif data.get('source_artifact') is not None:
                raise ValueError('原始图片快照缺少预处理信息')
        calibration = processing.get("calibration", {})
        if (not isinstance(calibration, dict) or calibration.get("status") != "not_calibrated"
                or calibration.get("area_mm2") is not None or calibration.get("diameter_mm") is not None):
            raise ValueError("此版本仅支持未标定的像素量化记录")
        mapping = processing.get("coordinate_mapping", {})
        if not isinstance(mapping, dict) or mapping.get("output_space") != "original_image":
            raise ValueError("记录 Mask 必须处于原图坐标")
        threshold = processing.get("effective_threshold")
        if threshold is not None and (type(threshold) not in (int, float) or not isfinite(threshold) or not 0 <= threshold <= 255):
            raise ValueError("记录实际阈值无效")
        request = ProcessingRequest(arrays["original"], Path(filename), config,
                                    source["is_synthetic"], task_id, submitted, source_bytes)
        result = ProcessingResult(task_id, arrays["enhanced"], arrays["mask"], arrays["overlay"],
            Measurements(values["area_px2"], diameter, values["source"], values["diameter_definition"]),
            capabilities, dict(timings), dict(processing))
        result.validate(request)
        roi_context = processing.get('input_context')
        is_roi = isinstance(roi_context, dict) and roi_context.get('schema') == 'formal_roi_v1'
        if (data['schema_version'] == '1.2') != is_roi:
            raise ValueError('ROI记录格式版本不匹配')
        if is_roi:
            from .roi import roi_artifacts
            expected_roi = roi_artifacts(request, result)
            hashes = data.get('roi_artifacts', {})
            if (set(hashes) != set(expected_roi) or data.get('roi_display') != {
                    'interpolation': 'nearest', 'outside_ROI': 'unprocessed_not_background',
                    'measurements': 'model_input_256_grid_only'}):
                raise ValueError('ROI显示文件或映射声明缺失')
            for name, expected_array in expected_roi.items():
                file = directory / name
                if file.is_symlink() or file.stat().st_size != hashes[name]['bytes'] or file_sha256(file) != hashes[name]['sha256']:
                    raise ValueError('ROI显示文件摘要不一致')
                with Image.open(file) as image:
                    rgb = expected_array[:, :, ::-1] if expected_array.ndim == 3 else expected_array
                    if image.format != 'PNG' or not np.array_equal(np.array(image), rgb):
                        raise ValueError('ROI显示映射与原始结果不一致')
        elif data.get('roi_artifacts') is not None:
            raise ValueError('非ROI记录不能附加ROI显示结果')
        from .roi_edge import validated_edge_report
        validated_edge_report(processing,result.mask)
        measured = max_feret_diameter(result.mask)
        if ((measured is None) != (diameter is None)
                or measured is not None and not isclose(measured, diameter, rel_tol=1e-9, abs_tol=1e-9)):
            raise ValueError("记录最大径与最终 Mask 不一致")
        if result.capabilities.key=='fpga_formal_v5':
            # Recompute locally for legacy records; saved record.json remains untouched.
            from .reference_library import update
            update((request,result))
        return LoadedRecord(directory, data, request, result)
    except (OSError, KeyError, TypeError, ValueError, OverflowError, RecursionError,
            Image.DecompressionBombError) as exc:
        raise ValueError(f"记录读取失败：{exc}") from exc
