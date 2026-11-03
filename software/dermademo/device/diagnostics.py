"""Independent laboratory evidence; deliberately not a clinical processing record."""

from dataclasses import fields
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from .imaging import diagnostic_pattern, restore_binary_mask
from .lab import LAB_ID, LAB_VERSION, LabMode, LabResult


def save_lab_result(parent: Path, result: LabResult) -> Path:
    if (not isinstance(result, LabResult) or result.origin != "protocol_simulator"
            or result.endpoint[0] != "127.0.0.1" or not 0 < result.endpoint[1] < 65536
            or len(result.returned) != (65536 if result.request.mode == LabMode.MASK_FIXTURE else 196608)):
        raise ValueError("Invalid software laboratory evidence")
    parent = Path(parent)
    if not parent.is_dir():
        raise ValueError("请选择现有的诊断导出目录")
    directory = parent / f"communication_lab_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"
    directory.mkdir(exist_ok=False)
    marker = directory / "INCOMPLETE"
    marker.write_text("Software laboratory export is not complete.\n", encoding="ascii")
    files = {"input.rgb": result.request.rgb,
             "result.mask_u8" if result.request.mode == LabMode.MASK_FIXTURE else "result.rgb": result.returned}
    rgb = np.frombuffer(result.request.rgb, np.uint8).reshape(256, 256, 3)
    images = {"input.png": cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)}
    if result.request.mode == LabMode.MASK_FIXTURE:
        images["fixture_mask.png"] = np.frombuffer(result.returned, np.uint8).reshape(256, 256)
        if result.request.prepared is not None:
            images["fixture_mask_original_coordinates.png"] = restore_binary_mask(result.returned, result.request.prepared)
    else:
        output = np.frombuffer(result.returned, np.uint8).reshape(256, 256, 3)
        images["returned.png"] = cv2.cvtColor(output, cv2.COLOR_RGB2BGR)
    for filename, pixels in images.items():
        ok, encoded = cv2.imencode(".png", pixels)
        if not ok:
            raise ValueError("无法生成本机通信诊断图片")
        files[filename] = encoded.tobytes()
    for filename, data in files.items():
        (directory / filename).write_bytes(data)
    prepared = result.request.prepared
    transform = None if prepared is None else {
        field.name: getattr(prepared, field.name) for field in fields(prepared) if field.name != "rgb"}
    document = {
        "format": "alienhome.communication-lab", "format_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "origin": result.origin, "hardware_verified": False, "model": None,
        "protocol_status": "candidate-not-frozen", "device_id": LAB_ID, "software_version": LAB_VERSION,
        "transport": "tcp-loopback", "endpoint": {"host": result.endpoint[0], "port": result.endpoint[1]},
        "mode": result.request.mode.name, "pipeline_mode": int(result.request.mode),
        "private_fixture_extension": result.request.mode == LabMode.MASK_FIXTURE,
        "mask_meaning": "fixed-square-protocol-fixture-not-segmentation" if result.request.mode == LabMode.MASK_FIXTURE else None,
        "input": {"width": 256, "height": 256, "pixel_format": "RGB888", "stride": 768,
                  "source": "workbench-snapshot" if prepared else "asymmetric-diagnostic-pattern"
                            if result.request.rgb == diagnostic_pattern() else "external-rgb-buffer",
                  "enhancement_applied": False, "transform": transform},
        "session_id": result.session_id, "boot_id": result.boot_id, "frame_id": result.frame_id,
        "input_transfer_id": result.input_transfer_id, "output_transfer_id": result.output_transfer_id,
        "sent_wire_bytes": result.sent_wire_bytes, "received_wire_bytes": result.received_wire_bytes,
        "retries": 0, "timings_ms": dict(result.timings_ms),
        "timing_scope": "PC socket transaction with software peer; includes software wait, not FPGA compute or throughput",
        "timeout_seconds": result.request.timeout_seconds,
        "files": {filename: {"bytes": len(data), "sha256": sha256(data).hexdigest()} for filename, data in files.items()},
    }
    (directory / "diagnostic.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    marker.unlink()
    return directory
