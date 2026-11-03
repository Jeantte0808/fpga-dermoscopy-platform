"""RGB transport diagnostics, including failures; never a segmentation record."""

from dataclasses import fields
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from .rgb_protocol import TOTAL
from .rgb_contracts import RGBOutcome


def save_rgb_outcome(parent: Path, outcome: RGBOutcome) -> Path:
    if not isinstance(outcome, RGBOutcome) or len(outcome.request.rgb) != TOTAL:
        raise ValueError("Invalid RGB UDP outcome")
    parent = Path(parent)
    if not parent.is_dir():
        raise ValueError("请选择现有导出目录")
    directory = parent / f"rgb_udp_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"
    directory.mkdir(exist_ok=False)
    marker = directory / "INCOMPLETE"
    marker.write_text("RGB transport diagnostic export is incomplete.\n", encoding="ascii")
    files = {"input.rgb": outcome.request.rgb}
    images = {"input.png": outcome.request.rgb}
    if outcome.returned is not None:
        files["returned.rgb" if outcome.complete else "unverified_return.rgb"] = outcome.returned
        if outcome.complete:
            images["returned.png"] = outcome.returned
    for name, raw in images.items():
        pixels = cv2.cvtColor(np.frombuffer(raw, np.uint8).reshape(256, 256, 3), cv2.COLOR_RGB2BGR)
        ok, encoded = cv2.imencode(".png", pixels)
        if not ok:
            raise ValueError("Cannot encode RGB diagnostic preview")
        files[name] = encoded.tobytes()
    for name, data in files.items():
        (directory / name).write_bytes(data)
    report = outcome.report
    prepared = outcome.request.prepared
    report.update({"created_at": datetime.now(timezone.utc).isoformat(),
                   "input_source": outcome.request.source, "enhancement_applied": False,
                   "transform": None if prepared is None else {field.name: getattr(prepared, field.name)
                                                               for field in fields(prepared) if field.name != "rgb"},
                   "files": {name: {"bytes": len(data), "sha256": sha256(data).hexdigest()} for name, data in files.items()}})
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    marker.unlink()
    return directory
