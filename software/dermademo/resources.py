"""Explicit resource inventory shared by source and frozen applications."""

from hashlib import sha256
import json
from pathlib import Path
import sys

from .runtime_paths import bundle_root


LOCATIONS = {"reference_library":"integrations/demo100_reference_20261031", "quality_rules":"dermademo/data/quality_v1.json", "classifier_root":"integrations/classifier_v2", "classifier_worker":"dermademo/classifier_worker.py", "sample": "examples/synthetic_demo.png", "rgb_root": "integrations/pc_rgb_20261020",
             "theme": "dermademo/_fluent", "formal_root": "integrations/formal_mask_20261025",
             "full_image_root": "integrations/full_image_20261025",
             "roi_root": "integrations/roi_int8_r1", "cpu_root": "integrations/cpu_fp32_r1",
             "cpu_worker": "dermademo/cpu_worker.py", "edge_root": "integrations/roi_edge_r1",
             "p7_hardware": "integrations/p7_acceptance_20261029/hardware_p7.json",
             "p7_evidence": "integrations/p7_acceptance_20261029/original_handoff.zip"}


def resource_path(name: str, *, root: Path | None = None) -> Path:
    base = (Path(root) if root is not None else bundle_root()).resolve()
    path = (base / LOCATIONS[name]).resolve()
    if not path.is_relative_to(base):
        raise ValueError("Resource escapes application bundle")
    return path


def resource_files(*, root: Path | None = None) -> list[Path]:
    base = (Path(root) if root is not None else bundle_root()).resolve()
    names = ["examples/synthetic_demo.png", "integrations/pc_rgb_20261020/IMAGE.RAW",
             "integrations/pc_rgb_20261020/IMPORT_NOTES.md", "integrations/pc_rgb_20261020/PROTOCOL.md",
             "integrations/pc_rgb_20261020/README_FIRST.md", "dermademo/_fluent/fluent.qss.in",
             "dermademo/_fluent/LICENSE", "dermademo/_fluent/NOTICE.md"]
    names.extend(f"dermademo/_fluent/resources/{name}" for name in (
        "fluent2-official-web-theme-tokens.json", "qt-token-map.json", "shell-token-map.json", "source-manifest.json"))
    names.extend(path.relative_to(base).as_posix() for path in sorted((base / "dermademo/_fluent/icons").glob("*.svg")))
    formal = base / LOCATIONS['formal_root']
    names.extend(f"{LOCATIONS['formal_root']}/assets/{name}" for name in
                 ('manifest.json', 'model.payload', 'sample.rgb', 'session_seed.json'))
    names.extend(path.relative_to(base).as_posix() for path in sorted((formal / 'captures').rglob('*')) if path.is_file())
    full_image = base / LOCATIONS['full_image_root']
    names.extend(f"{LOCATIONS['full_image_root']}/{name}" for name in
                 ('pc_environment.json', 'hardware_display_reference.json', 'samples.json', 'model_identity.json', 'manifest.json'))
    names.extend(path.relative_to(base).as_posix() for path in sorted((full_image / 'samples').rglob('*')) if path.is_file())
    for key in ('roi_root','cpu_root','edge_root','classifier_root','reference_library'):
        folder = base / LOCATIONS[key]
        names.extend(path.relative_to(base).as_posix() for path in sorted(folder.rglob('*'))
                     if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc')
    names.append(LOCATIONS['cpu_worker'])
    names.append(LOCATIONS['classifier_worker'])
    names.append(LOCATIONS['quality_rules'])
    names.append(LOCATIONS['p7_hardware'])
    names.append(LOCATIONS['p7_evidence'])
    paths = [(base / name).resolve() for name in names]
    if any(not path.is_relative_to(base) or not path.is_file() for path in paths):
        raise ValueError("Required application resource missing or outside bundle")
    if not any(path.suffix == ".svg" for path in paths):
        raise ValueError("Fluent icon resources missing")
    return paths


def verify_resources(*, root: Path | None = None) -> dict:
    base = (Path(root) if root is not None else bundle_root()).resolve()
    files = {path.relative_to(base).as_posix(): {"bytes": path.stat().st_size, "sha256": sha256(path.read_bytes()).hexdigest()}
             for path in resource_files(root=base)}
    if files["integrations/pc_rgb_20261020/IMAGE.RAW"]["sha256"] != "02ced8440ac591d306fb18ca46926902cc94e6a2a01ab4d743d1c90497d3b854":
        raise ValueError("Delivered RGB input resource digest mismatch")
    if getattr(sys, "frozen", False):
        expected = json.loads((base / "resource-manifest.json").read_text(encoding="utf-8"))
        if expected.get("files") != files:
            raise ValueError("Frozen resource inventory digest mismatch")
    return {"format": "alienhome.resources", "version": 1, "files": files}
