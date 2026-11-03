"""Deterministic synthetic acceptance inputs; never substitutes for a model test."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path


CASES = {
    "ACC-01": "无 Python 的第二台电脑/干净 VM 离线冷启动 GUI；记录系统、构建摘要与截图。",
    "ACC-02": "载入合成图，Enter 处理；四图和像素指标存在；Ctrl+S 保存并重开 record.json。",
    "ACC-03": "处理后修改自动/手动阈值和参数；四图及指标保留；保存仍使用旧配置；Enter 后应用新配置。",
    "ACC-04": "双击、滚轮、左键拖动、联动与叠加透明度；浅/深/高对比主题及 100%/150% 可用。",
    "ACC-05": "导入 exif_orientation6.jpg、transparent.png、中文 路径 样例.png；方向、尺寸、透明白底及保存正确。",
    "ACC-06": "empty_foreground.png 设手动阈值 0 得空 Mask；非正方形尺寸保留；known_mask.png 面积 600，Feret=sqrt(29^2+19^2)。",
    "ACC-07": "导入 broken.png、truncated.jpg、oversize.png；明确失败并保留原图及结果，日志关联错误编号。",
    "ACC-08": "保存到不可写目录；重开缺文件/损坏/INCOMPLETE 及旧 schema 记录；失败保留旧有效结果。",
    "ACC-09": "真实 AlienHomeTools.exe --help；batch 成功/失败；核对退出码、报告、verify-resources 与 acceptance-check。",
    "ACC-10": "lab 运行本机软件旁路/夹具；报告注明软件来源；普通启动不自动连接板卡。",
    "ACC-11": "本机 UDP 对端验证 ACK/GET、CRC 损坏、BUSY、重试与失败报告；不算实板验收。",
    "ACC-12": "导入/处理/保存失败有日志；不可写、队列满、轮转降级真实；默认诊断 ZIP 不带影像/私人路径。",
    "ACC-13": "多进程日志独立；通信及诊断导出中关闭有清理；无 QThread 销毁警告。",
    "ACC-14": "真实板卡 RGB：192 输入/192 输出、CRC 和逐字节参考通过，保存新 RAW/报告；需网卡与板卡。",
    "ACC-15": "正式模型与 Mask 协议交付后执行真实模型端到端验收；本轮不具备条件。",
}


def _digest(path):
    return sha256(path.read_bytes()).hexdigest()


def generate_suite(directory: Path):
    """Creates a NEW directory. Existing data is never overwritten or removed."""
    from PIL import Image
    import numpy as np
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    yy, xx = np.indices((128, 192))
    rgb = np.empty((128, 192, 3), dtype=np.uint8)
    rgb[:] = (225, 195, 169)
    rgb[((xx - 96) / 40) ** 2 + ((yy - 64) / 30) ** 2 < 1] = (95, 55, 40)
    images = {"non_square.png": Image.fromarray(rgb), "中文 路径 样例.png": Image.fromarray(rgb),
              "empty_foreground.png": Image.new("RGB", (96, 64), (240, 240, 240))}
    mask = np.zeros((64, 96), np.uint8)
    mask[20:40, 30:60] = 255
    images["known_mask.png"] = Image.fromarray(mask)
    rgba = Image.new("RGBA", (80, 60), (255, 0, 0, 0))
    rgba.paste((20, 40, 60, 255), (20, 15, 60, 45))
    images["transparent.png"] = rgba
    for name, image in images.items():
        image.save(directory / name, format="PNG")
    oriented = Image.new("RGB", (80, 40), (220, 180, 160))
    oriented.paste((40, 80, 120), (0, 0, 20, 20))
    exif = Image.Exif()
    exif[274] = 6
    oriented.save(directory / "exif_orientation6.jpg", format="JPEG", quality=90, exif=exif)
    (directory / "broken.png").write_bytes(b"not an image\n")
    # Oversize pixels but small disk footprint; decoding is rejected before allocation.
    Image.new("1", (5001, 4800)).save(directory / "oversize.png", format="PNG")
    buffer = BytesIO()
    oriented.save(buffer, format="JPEG", quality=90)
    (directory / "truncated.jpg").write_bytes(buffer.getvalue()[:80])
    expected = {
        "non_square.png": {"size": [192, 128]}, "中文 路径 样例.png": {"size": [192, 128]},
        "empty_foreground.png": {"size": [96, 64], "manual_threshold": 0, "foreground_pixels": 0},
        "known_mask.png": {"size": [96, 64], "role": "known_binary_mask", "foreground_pixels": 600},
        "transparent.png": {"size": [80, 60], "corner_bgr": [255, 255, 255]},
        "exif_orientation6.jpg": {"size": [40, 80]},
        "broken.png": {"reject": True}, "truncated.jpg": {"reject": True}, "oversize.png": {"reject": True}}
    manifest = {"format": "alienhome.acceptance-suite", "version": 1,
                "origin": "deterministic_synthetic_not_medical", "generator": "dermademo.acceptance.v1",
                "files": {name: {"bytes": (directory / name).stat().st_size, "sha256": _digest(directory / name),
                                 "expected": expected[name]} for name in sorted(expected)},
                "cases": [{"id": key, "steps_and_expected": value} for key, value in CASES.items()]}
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    template = {"format": "alienhome.acceptance-report", "version": 1, "build_version": None,
                "release_sha256": None, "suite_manifest_sha256": _digest(directory / "manifest.json"),
                "machine": {"windows_version": None, "no_python_clean_environment": False},
                "cases": [{"id": key, "status": "NOT_RUN", "reason": "尚未执行", "evidence": []} for key in CASES]}
    (directory / "report-template.json").write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify_suite(directory: Path):
    """Verifies trusted suite inventory, then exercises actual import/geometry rules."""
    import math
    import numpy as np
    from .imaging import load_image, max_feret_diameter
    directory = Path(directory).resolve()
    document = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if document.get("format") != "alienhome.acceptance-suite" or document.get("version") != 1:
        raise ValueError("验收清单格式不支持")
    if [case["id"] for case in document["cases"]] != list(CASES):
        raise ValueError("验收编号缺失或重复")
    results = []
    for name, spec in document["files"].items():
        path = directory / name
        if Path(name).name != name or path.is_symlink() or path.resolve().parent != directory:
            raise ValueError("验收文件路径越界")
        if path.stat().st_size != spec["bytes"] or _digest(path) != spec["sha256"]:
            raise ValueError(f"验收文件摘要不匹配：{name}")
        expected = spec["expected"]
        try:
            image = load_image(path)
        except ValueError:
            if not expected.get("reject"):
                raise
        else:
            if expected.get("reject") or [image.shape[1], image.shape[0]] != expected["size"]:
                raise ValueError(f"验收解码预期不匹配：{name}")
            if "corner_bgr" in expected and image[0, 0].tolist() != expected["corner_bgr"]:
                raise ValueError("透明图片白底规则不匹配")
            if expected.get("role") == "known_binary_mask":
                mask = image[:, :, 0]
                if np.count_nonzero(mask) != 600 or not math.isclose(max_feret_diameter(mask), math.hypot(29, 19)):
                    raise ValueError("已知 Mask 几何不匹配")
        results.append({"file": name, "status": "PASS"})
    return {"format": "alienhome.acceptance-input-check", "suite_manifest_sha256": _digest(directory / "manifest.json"),
            "checks": results, "scope": "fixture_integrity_and_decode_only_not_full_acceptance"}


def summarize_report(report):
    """No default PASS, no hidden NOT_RUN, and no skipped clean-machine gate."""
    rows = report["cases"]
    if [row["id"] for row in rows] != list(CASES):
        raise ValueError("报告验收编号不完整或重复")
    counts = {key: 0 for key in ("PASS", "FAIL", "NOT_RUN", "NOT_APPLICABLE")}
    for row in rows:
        if row["status"] not in counts:
            raise ValueError("报告状态不支持")
        if row["status"] in {"PASS", "FAIL"} and not row.get("evidence"):
            raise ValueError("通过或失败必须提供证据")
        if row["status"] in {"NOT_RUN", "NOT_APPLICABLE"} and not row.get("reason"):
            raise ValueError("未执行或不适用必须说明理由")
        counts[row["status"]] += 1
    base = all(row["status"] == "PASS" for row in rows[:13])
    identified = bool(report.get("release_sha256") and report.get("build_version") and report.get("suite_manifest_sha256"))
    return {"counts": counts, "base_pc_accepted": base and identified and report["machine"].get("no_python_clean_environment") is True,
            "real_rgb_accepted": rows[13]["status"] == "PASS" and identified,
            "formal_model_accepted": rows[14]["status"] == "PASS" and identified}
