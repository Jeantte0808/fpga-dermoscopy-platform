"""An export is complete only once record.json and all PNGs exist."""

from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import platform
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image

from . import __version__
from .contracts import BackendConfig, ProcessingRequest, ProcessingResult
from .configuration import serialize_config

# First-load FCP1 operation traces exceed the former 1 MiB reader limit.
MAX_RECORD_JSON_BYTES = 8 * 1024 * 1024


class ExportError(OSError):
    def __init__(self, path: Path, completed: list[str], cause: Exception):
        self.path = path
        self.completed = completed
        details = "、".join(completed) if completed else "无"
        super().__init__(f"保存未完成：{cause}\n目录：{path}\n已写入：{details}\n此目录不是完整处理记录。")


def write_png(path: Path, array: np.ndarray) -> None:
    if array.ndim == 3:
        image = Image.fromarray(array[:, :, ::-1])
    else:
        image = Image.fromarray(array)
    image.save(path, format="PNG")


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_result(parent: Path, request: ProcessingRequest, result: ProcessingResult, *, origin_record: dict | None = None) -> Path:
    result.validate(request)
    from .roi_edge import validated_edge_report
    edge = validated_edge_report(result.metadata,result.mask)
    if result.capabilities.key=='fpga_formal_v5':
        from .reference_library import update
        update((request,result))
    processing = dict(result.metadata)
    if edge is not None:
        processing['roi_edge_contact'] = edge
    record_id = uuid4().hex
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    destination = parent / f"record_{stamp}_{record_id[:8]}"
    completed: list[str] = []
    try:
        destination.mkdir(parents=False, exist_ok=False)
        # Marker survives failed writes and is removed only after complete export.
        (destination / "INCOMPLETE.txt").write_text("Export in progress or failed.\n", encoding="utf-8")
        outputs = {
            "original.png": request.image_bgr,
            "enhanced.png": result.enhanced_bgr,
            "mask.png": result.mask,
            "overlay.png": result.overlay_bgr,
        }
        for name, array in outputs.items():
            write_png(destination / name, array)
            completed.append(name)
        wire_outputs = {}
        if result.capabilities.key == 'fpga_formal_v5':
            from .device.formal_session import write_bytes
            raw_input = np.ascontiguousarray(request.image_bgr[:, :, ::-1]).tobytes()
            raw_mask = result.mask.tobytes()
            for name, raw in (('input.rgb', raw_input), ('mask.raw', raw_mask)):
                write_bytes(destination / name, raw)
                wire_outputs[name] = {'bytes': len(raw), 'sha256': sha256(raw).hexdigest()}
                completed.append(name)
        source_artifact = None
        if request.source_bytes is not None:
            from .formal_input import validate_source
            from .device.formal_session import write_bytes
            validate_source(result.metadata.get('input_context'), raw_input, request.source_bytes)
            write_bytes(destination / 'source_image.bin', request.source_bytes)
            completed.append('source_image.bin')
            source_artifact = {'path': 'source_image.bin', 'bytes': len(request.source_bytes),
                               'sha256': sha256(request.source_bytes).hexdigest()}
        roi_outputs = {}
        context = result.metadata.get('input_context')
        if isinstance(context, dict) and context.get('schema') == 'formal_roi_v1':
            from .roi import roi_artifacts
            for name, array in roi_artifacts(request, result).items():
                write_png(destination / name, array)
                completed.append(name)
                roi_outputs[name] = {'bytes': (destination / name).stat().st_size,
                                     'sha256': file_sha256(destination / name)}
        record = {
            "schema_version": "1.2" if roi_outputs else "1.1" if isinstance(request.config, BackendConfig) else "1.0", "application_version": __version__,
            "record_id": record_id, "task_id": result.task_id,
            "submitted_at": request.submitted_at,
            "saved_at": datetime.now().astimezone().isoformat(),
            "source": {
                "filename": request.source_path.name,
                "width": request.image_bgr.shape[1], "height": request.image_bgr.shape[0],
                "color_space": "BGR", "orientation": "EXIF_normalized",
                "is_synthetic": request.is_synthetic,
                "decoded_bgr_sha256": sha256(request.image_bgr.tobytes()).hexdigest(),
                "original_copy": "decoded_lossless_png",
            },
            "backend": asdict(result.capabilities), "parameters": serialize_config(request.config),
            "measurements": {
                **asdict(result.measurements), "area_unit": "px²", "diameter_unit": "px",
            },
            "timings_ms": result.timings_ms, "processing": processing,
            "environment": {
                "python": platform.python_version(), "platform": platform.platform(),
                "opencv": cv2.__version__, "numpy": np.__version__,
            },
            "outputs": {name.removesuffix(".png"): name for name in outputs},
            "output_sha256": {name.removesuffix(".png"): file_sha256(destination / name) for name in outputs},
        }
        if origin_record is not None:
            record["restored_from"] = {
                "record_id": origin_record["record_id"], "saved_at": origin_record["saved_at"],
                "application_version": origin_record["application_version"],
                "environment": origin_record["environment"],
            }
            record["environment"] = origin_record["environment"]
            record["export_environment"] = {
                "python": platform.python_version(), "platform": platform.platform(),
                "opencv": cv2.__version__, "numpy": np.__version__,
            }
        if wire_outputs:
            record['raw_artifacts'] = wire_outputs
            record['source']['representation'] = 'actual_model_input_256_grid'
        if source_artifact:
            record['source_artifact'] = source_artifact
        if roi_outputs:
            record['roi_artifacts'] = roi_outputs
            record['roi_display'] = {'interpolation': 'nearest', 'outside_ROI': 'unprocessed_not_background',
                                     'measurements': 'model_input_256_grid_only'}
        from .formal_input import EDUCATION_NOTICE
        record['education_notice'] = EDUCATION_NOTICE
        encoded = json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
        if len(encoded) > MAX_RECORD_JSON_BYTES:
            raise ValueError('记录 JSON 超过 8 MiB 限制，未标记保存完成')
        (destination / "record.json").write_bytes(encoded)
        completed.append("record.json")
        (destination / "INCOMPLETE.txt").unlink()
        return destination
    except Exception as exc:
        raise ExportError(destination, completed, exc) from exc
