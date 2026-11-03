"""Local, bounded diagnostic archives. Never copies an active log directory."""

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from importlib.metadata import version, PackageNotFoundError
import os
from pathlib import Path
import platform
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from . import __version__
from .diagnostic_logging import service
from .resources import verify_resources
from .runtime_paths import bundle_root


DEFAULT_LIMIT = 25 * 1024 * 1024
IMAGE_LIMIT = 100 * 1024 * 1024
IMAGE_NAMES = {"original", "enhanced", "mask", "overlay"}


def version_info():
    # No hostname, environment dump, username, search paths or registry values.
    info = {"application": "DermaWorkbench", "version": __version__,
            "python": platform.python_version(), "system": platform.system(),
            "system_release": platform.release(), "architecture": platform.machine(),
            "frozen": bool(getattr(__import__("sys"), "frozen", False))}
    build = bundle_root() / "build-info.json"
    if info["frozen"] and build.is_file():
        value = json.loads(build.read_text(encoding="utf-8"))
        info["build"] = {key: value.get(key) for key in ("git_commit", "dirty", "built_utc", "dependencies")}
    else:
        info["dependencies"] = {}
        for package in ("PySide6", "numpy", "opencv-python-headless", "Pillow"):
            try:
                info["dependencies"][package] = version(package)
            except PackageNotFoundError:
                info["dependencies"][package] = "unavailable"
    return info


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def export_diagnostics(directory: Path, *, images=None, limit=None):
    """`images` is an explicit opt-in mapping of canonical names to copied arrays.

    Images are re-encoded, stripping EXIF/path metadata. Call from a worker.
    Only this session's sanitized, bounded in-memory event snapshot is included.
    """
    images = {} if images is None else dict(images)
    if not images.keys() <= IMAGE_NAMES:
        raise ValueError("诊断图片名称不在允许列表中")
    maximum = IMAGE_LIMIT if images else DEFAULT_LIMIT
    if limit is not None:
        maximum = min(maximum, limit)
    current = service()
    flushed = current.flush(timeout=1) if current else True
    snapshot = current.snapshot() if current else {"events": [], "persistent": False, "storage_error": "not_initialized"}
    events = snapshot.pop("events")
    snapshot["flush_completed"] = flushed
    snapshot["scope"] = "current_session_last_200_sanitized_events"
    entries = {"version.json": _json(version_info()), "log-status.json": _json(snapshot),
               "events.jsonl": b"".join(_json(event).replace(b"\n", b" ") + b"\n" for event in events)}
    try:
        entries["resources.json"] = _json(verify_resources())
    except (OSError, ValueError) as exc:
        entries["resources.json"] = _json({"status": "failed", "error_type": type(exc).__name__})
    if images:
        from PIL import Image
        import numpy as np
        for name, image in sorted(images.items()):
            if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.size > IMAGE_LIMIT:
                raise ValueError("诊断图片必须为有界 uint8 图像")
            if image.ndim == 3 and image.shape[2] == 3:
                image = image[:, :, ::-1]  # app BGR to PNG RGB
            elif image.ndim != 2:
                raise ValueError("诊断图片必须为灰度或 BGR 三通道")
            buffer = BytesIO()
            Image.fromarray(image).save(buffer, format="PNG")
            entries[f"images/{name}.png"] = buffer.getvalue()
    if sum(map(len, entries.values())) > maximum:
        raise ValueError("诊断内容超过大小上限，请减少附带图片")
    entries["manifest.json"] = _json({"format": "alienhome.diagnostic-bundle", "version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(), "includes_images": bool(images),
        "files": {name: {"bytes": len(data), "sha256": sha256(data).hexdigest()} for name, data in entries.items()}})
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError("请选择已存在的诊断导出目录")
    destination = directory / f"DermaWorkbench-diagnostics-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:12]}.zip"
    partial = destination.with_suffix(".zip.partial")
    created = False
    try:
        with partial.open("xb") as stream:
            created = True
            with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
                for name, data in entries.items():
                    archive.writestr(name, data)
            stream.flush()
            os.fsync(stream.fileno())
        if partial.stat().st_size > maximum:
            raise ValueError("诊断压缩包超过大小上限")
        # UUID names and exclusive partial creation avoid replacing prior exports.
        if destination.exists():
            raise FileExistsError("诊断目标已存在")
        partial.rename(destination)
        return destination
    except BaseException:
        if created:
            partial.unlink(missing_ok=True)
        raise
