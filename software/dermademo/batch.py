"""Independent, reproducible batch validation; no QApplication or GUI settings."""

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from hashlib import sha256
import csv
import json
import os
from pathlib import Path
import platform
import sys
from time import perf_counter
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image, __version__ as pillow_version

from . import __version__
from .configuration import read_config, serialize_config
from .contracts import ProcessingRequest
from .imaging import load_candidate
from .registry import BackendRegistry, create_registry
from .storage import ExportError, file_sha256, save_result
from .diagnostic_logging import entry_session, record_exception


SUPPORTED = {".png", ".jpg", ".jpeg"}
TIMING_KEYS = ("end_to_end", "backend_pipeline", "preprocessing", "backend_compute",
               "postprocessing", "send", "receive")
ITEM_FIELDS = ("input", "status", "error_stage", "error", "width", "height", "is_synthetic",
               "decoded_bgr_sha256", "load_ms", "completed_repeats", "area_px2", "max_feret_px",
               "mask_sha256", "results_stable", "annotation_status", "annotation_error",
               "annotation_sha256", "dice", "iou", "end_to_end_median_ms", "end_to_end_p95_ms", "record")
RUN_FIELDS = ("input", "repeat", "task_id", "area_px2", "max_feret_px", "mask_sha256",
              *[f"{key}_ms" for key in TIMING_KEYS], "timings_json")


@dataclass(frozen=True)
class BatchOptions:
    input_dir: Path
    output_dir: Path
    backend_key: str = "demo_cpu"
    config_document: dict | None = None
    labels_dir: Path | None = None
    recursive: bool = False
    warmup: int = 1
    repeat: int = 3
    save_results: bool = False
    synthetic: bool = False


@dataclass(frozen=True)
class BatchOutcome:
    directory: Path
    report: dict

    @property
    def exit_code(self):
        if self.report["status"] == "cancelled":
            return 130
        return 1 if self.report["status"] == "completed_with_errors" else 0


def discover_images(root: Path, *, recursive: bool, excluded: tuple[Path, ...] = ()) -> list[Path]:
    root = root.resolve()

    def eligible(path):
        resolved = path.resolve()
        return not path.is_symlink() and not any(resolved.is_relative_to(folder) for folder in excluded)

    images = []
    if recursive:
        for directory, children, files in os.walk(root, followlinks=False):
            children[:] = [name for name in children if eligible(Path(directory) / name)]
            images.extend(Path(directory) / name for name in files
                          if Path(name).suffix.lower() in SUPPORTED and eligible(Path(directory) / name))
    else:
        images = [path for path in root.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED and eligible(path)]
    return sorted(images, key=lambda path: (path.relative_to(root).as_posix().casefold(), path.as_posix()))


def overlap_scores(mask: np.ndarray, annotation: np.ndarray) -> tuple[float, float]:
    if mask.shape != annotation.shape:
        raise ValueError("标注尺寸必须与方向校正后的原图一致，不自动缩放标注")
    for array in (mask, annotation):
        if array.ndim != 2 or array.dtype != np.uint8 or not np.isin(array, (0, 255)).all():
            raise ValueError("预测与标注必须为 uint8 0/255 二值 Mask")
    prediction, target = mask != 0, annotation != 0
    intersection = int(np.count_nonzero(prediction & target))
    total = int(np.count_nonzero(prediction)) + int(np.count_nonzero(target))
    union = total - intersection
    return (2 * intersection / total if total else 1.0,
            intersection / union if union else 1.0)


def read_annotation(path: Path, shape: tuple[int, int]) -> np.ndarray:
    with Image.open(path) as image:
        if image.format != "PNG" or image.mode != "L" or image.size != (shape[1], shape[0]):
            raise ValueError("标注应为与原图同尺寸的单通道 PNG，不自动转换或缩放")
        annotation = np.array(image)
    if not np.isin(annotation, (0, 255)).all():
        raise ValueError("标注只能包含 0（背景）和 255（前景）")
    return annotation


def statistics(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "median": None, "p95": None}
    return {"count": len(values), "median": float(np.median(values)),
            "p95": float(np.percentile(values, 95, method="linear"))}


def _csv_row(row, fields):
    # Preserve literal filenames/errors when opening reports in spreadsheet software.
    return {key: "'" + value if isinstance(value := row.get(key), str) and value.startswith(("=", "+", "-", "@", "\t", "\r", "\n"))
            else value for key in fields}


def _write_report(directory: Path, report: dict):
    temporary = directory / "report.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(directory / "report.json")


def run_batch(options: BatchOptions, *, registry: BackendRegistry | None = None,
              progress=None, cancelled=None) -> BatchOutcome:
    """Keep per-image failures, continue subsequent inputs, finalize honest summaries."""
    root, output = options.input_dir.resolve(), options.output_dir.resolve()
    labels = options.labels_dir.resolve() if options.labels_dir is not None else None
    if not root.is_dir():
        raise ValueError("输入目录不存在")
    if output == root or root.is_relative_to(output):
        raise ValueError("输出目录应独立于输入目录，或位于输入目录的单独子目录")
    if labels is not None and (not labels.is_dir() or labels == root or root.is_relative_to(labels)):
        raise ValueError("标注目录不存在或与输入目录重叠")
    if type(options.warmup) is not int or not 0 <= options.warmup <= 100 or type(options.repeat) is not int or not 1 <= options.repeat <= 100:
        raise ValueError("预热次数应为 0–100，重复次数应为 1–100")
    registry = registry if registry is not None else create_registry()
    definition = registry.get(options.backend_key)
    config = definition.parse_config_document(options.config_document) if options.config_document is not None else definition.configure()
    excluded = (output,) + ((labels,) if labels is not None else ())
    inputs = discover_images(root, recursive=options.recursive, excluded=excluded)
    if not inputs:
        raise ValueError("输入目录没有可处理的 PNG/JPEG 图片")
    initialized = perf_counter()
    backend = registry.open(options.backend_key)
    initialization_ms = (perf_counter() - initialized) * 1000
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"batch_{stamp}_{uuid4().hex[:8]}"
    destination.mkdir(exist_ok=False)
    marker = destination / "INCOMPLETE.txt"
    marker.write_text("Batch report in progress or interrupted before finalization.\n", encoding="utf-8")
    records_parent = destination / "records"
    if options.save_results:
        records_parent.mkdir()
    report = {
        "schema_version": "1.0", "application_version": __version__, "status": "running",
        "started_at": datetime.now().astimezone().isoformat(), "input_dir": str(root),
        "labels_dir": str(labels) if labels is not None else None,
        "backend": asdict(definition.capabilities), "capability_description": definition.describe(),
        "parameters": serialize_config(config), "backend_initialization_ms": initialization_ms,
        "methodology": {
            "warmup_per_image": options.warmup, "measured_repeats_per_image": options.repeat,
            "execution": "sequential_single_backend_instance",
            "end_to_end_scope": "synchronous_submit_to_validated_result_excluding_read_display_save_and_metrics",
            "backend_pipeline_scope": "backend_reported_end_to_end_before_outer_measurement",
            "timing_aggregation": "successful_complete_images_only_each_repeat_equal_weight",
            "percentile_method": "linear", "recursive": options.recursive,
            "annotation_naming": "relative_original_filename_including_extension + .mask.png",
            "annotation_space": "EXIF_normalized_original_no_resize_no_postprocessing",
            "empty_mask_scores": "both_empty=1;one_empty=0",
            "accuracy_aggregation": "per_image_macro_mean_last_measured_repeat_valid_annotations_only",
            "saved_result": "last_measured_repeat", "save_results": options.save_results,
            "synthetic_override": options.synthetic,
        },
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "numpy": np.__version__, "opencv": cv2.__version__, "pillow": pillow_version,
                        "opencv_threads": cv2.getNumThreads()},
        "discovered_images": len(inputs), "items": [],
        "note": (("临时后端仅验证流程。" if definition.capabilities.is_placeholder else "")
                 + "本报告没有 FPGA 加速倍数。缺少标注不生成精度数值。"),
    }
    _write_report(destination, report)
    all_timings = {key: [] for key in TIMING_KEYS}
    interrupted = False
    sample_path = Path(__file__).resolve().parents[1] / "examples" / "synthetic_demo.png"
    with (destination / "images.csv").open("w", encoding="utf-8-sig", newline="") as image_stream, \
            (destination / "runs.csv").open("w", encoding="utf-8-sig", newline="") as run_stream:
        image_writer = csv.DictWriter(image_stream, ITEM_FIELDS)
        run_writer = csv.DictWriter(run_stream, RUN_FIELDS)
        image_writer.writeheader()
        run_writer.writeheader()
        for path in inputs:
            if cancelled is not None and cancelled():
                interrupted = True
                break
            relative = path.relative_to(root)
            item = {"input": relative.as_posix(), "status": "failed", "completed_repeats": 0,
                    "annotation_status": "not_requested" if labels is None else "not_evaluated"}
            measured_runs, hashes = [], []
            stage = "load"
            try:
                started = perf_counter()
                candidate = load_candidate(path, options.synthetic or path.resolve() == sample_path.resolve())
                item.update(load_ms=(perf_counter() - started) * 1000,
                            width=candidate.image_bgr.shape[1], height=candidate.image_bgr.shape[0],
                            is_synthetic=candidate.is_synthetic,
                            decoded_bgr_sha256=sha256(candidate.image_bgr.tobytes()).hexdigest())
                stage = "process"
                for index in range(options.warmup + options.repeat):
                    if cancelled is not None and cancelled():
                        raise KeyboardInterrupt()
                    request = ProcessingRequest(candidate.image_bgr, path, config, candidate.is_synthetic)
                    started = perf_counter()
                    result = backend.process(request)
                    elapsed = (perf_counter() - started) * 1000
                    if index < options.warmup:
                        continue
                    result = replace(result, timings_ms={**result.timings_ms,
                        "backend_pipeline": result.timings_ms["end_to_end"], "end_to_end": elapsed},
                        metadata={**result.metadata, "timing_scope": report["methodology"]["end_to_end_scope"]})
                    mask_hash = sha256(result.mask.tobytes()).hexdigest()
                    hashes.append((mask_hash, result.measurements.area_px2, result.measurements.max_feret_px))
                    run = {"input": relative.as_posix(), "repeat": index - options.warmup + 1,
                           "task_id": result.task_id, "area_px2": result.measurements.area_px2,
                           "max_feret_px": result.measurements.max_feret_px, "mask_sha256": mask_hash,
                           **{f"{key}_ms": result.timings_ms.get(key) for key in TIMING_KEYS},
                           "timings_json": json.dumps(result.timings_ms, allow_nan=False)}
                    run_writer.writerow(_csv_row(run, RUN_FIELDS))
                    run_stream.flush()
                    measured_runs.append(result.timings_ms)
                    item["completed_repeats"] += 1
                item.update(area_px2=result.measurements.area_px2, max_feret_px=result.measurements.max_feret_px,
                            mask_sha256=hashes[-1][0], results_stable=len(set(hashes)) == 1)
                end_stats = statistics([timings["end_to_end"] for timings in measured_runs])
                item.update(end_to_end_median_ms=end_stats["median"], end_to_end_p95_ms=end_stats["p95"])
                if labels is not None:
                    label_path = labels / relative.parent / (relative.name + ".mask.png")
                    try:
                        annotation = read_annotation(label_path, result.mask.shape)
                        dice, iou = overlap_scores(result.mask, annotation)
                        item.update(annotation_status="ok", annotation_sha256=file_sha256(label_path), dice=dice, iou=iou)
                    except Exception as exc:
                        item.update(annotation_status="missing" if not label_path.exists() else "invalid",
                                    annotation_error=str(exc))
                stage = "export"
                if options.save_results:
                    saved = save_result(records_parent, request, result)
                    item["record"] = saved.relative_to(destination).as_posix()
                item["status"] = "ok"
                for timings in measured_runs:
                    for key in TIMING_KEYS:
                        if key in timings:
                            all_timings[key].append(timings[key])
            except KeyboardInterrupt:
                interrupted = True
                item.update(status="cancelled", error_stage=stage, error="用户取消，未开始的图片不计为成功")
            except Exception as exc:
                record_exception("batch_item_failed", exc, stage=stage, count=len(report["items"]))
                item.update(error_stage=stage, error=str(exc))
                if isinstance(exc, ExportError):
                    item["record"] = exc.path.relative_to(destination).as_posix()
            report["items"].append(item)
            image_writer.writerow(_csv_row(item, ITEM_FIELDS))
            image_stream.flush()
            if progress is not None:
                progress(dict(item), len(report["items"]), len(inputs))
            if interrupted:
                break
        # Streamed CSVs remain inspectable if later report finalization fails.
    items = report["items"]
    successes = [item for item in items if item["status"] == "ok"]
    evaluated = [item for item in successes if item["annotation_status"] == "ok"]
    annotation_errors = sum(item["annotation_status"] in {"missing", "invalid"} for item in items)
    failures = sum(item["status"] == "failed" for item in items)
    report.update(
        status="cancelled" if interrupted else "completed_with_errors" if failures or annotation_errors else "completed",
        finished_at=datetime.now().astimezone().isoformat(),
        counts={"attempted": len(items), "succeeded": len(successes), "failed": failures,
                "cancelled": sum(item["status"] == "cancelled" for item in items),
                "not_started": len(inputs) - len(items), "annotation_evaluated": len(evaluated),
                "annotation_errors": annotation_errors,
                "unstable_results": sum(item.get("results_stable") is False for item in successes)},
        timings_ms={key: statistics(values) for key, values in all_timings.items()},
        accuracy={"count": len(evaluated),
                  "dice_macro_mean": float(np.mean([item["dice"] for item in evaluated])) if evaluated else None,
                  "iou_macro_mean": float(np.mean([item["iou"] for item in evaluated])) if evaluated else None},
        files={"images": "images.csv", "runs": "runs.csv", "summary": "report.json"},
    )
    _write_report(destination, report)
    marker.unlink()
    return BatchOutcome(destination, report)


@entry_session("batch_cli")
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="独立批量验证（不会打开 GUI）")
    parser.add_argument("input_dir", nargs="?", type=Path, help="PNG/JPEG 输入目录")
    parser.add_argument("--output", type=Path, default=Path("outputs"), help="报告保存父目录，默认 outputs")
    parser.add_argument("--backend", default="demo_cpu", help="已注册后端标识")
    parser.add_argument("--config", type=Path, help="参数 JSON，可用 --list-backends 查看参数能力")
    parser.add_argument("--labels", type=Path, help="标注目录，例如 image.jpg 对应 image.jpg.mask.png")
    parser.add_argument("--recursive", action="store_true", help="包含子目录，排除输出和标注目录")
    parser.add_argument("--warmup", type=int, default=1, help="每张图预热次数，不计入统计（默认 1）")
    parser.add_argument("--repeat", type=int, default=3, help="每张图测量次数（默认 3）")
    parser.add_argument("--save-results", action="store_true", help="保存每张图最后一次测量的完整四图记录")
    parser.add_argument("--synthetic", action="store_true", help="将所有输入明确标记为合成样例")
    parser.add_argument("--list-backends", action="store_true", help="列出实际已注册后端和参数，不运行处理")
    args = parser.parse_args(argv)
    if args.list_backends:
        print(json.dumps(create_registry().describe(), ensure_ascii=False, indent=2))
        return 0
    if args.input_dir is None:
        parser.error("请提供输入目录，或使用 --list-backends")
    try:
        options = BatchOptions(args.input_dir, args.output, args.backend,
            read_config(args.config) if args.config is not None else None,
            args.labels, args.recursive, args.warmup, args.repeat, args.save_results, args.synthetic)
        def progress(item, completed, total):
            print(f"[{completed}/{total}] {item['input']} : {item['status']} / {item['annotation_status']}", flush=True)
        outcome = run_batch(options, progress=progress)
        print(f"报告：{outcome.directory}\n状态：{outcome.report['status']}\n统计：{outcome.report['counts']}", flush=True)
        return outcome.exit_code
    except KeyboardInterrupt:
        print("批量验证已取消。若报告未完成，请检查输出目录的 INCOMPLETE.txt。", file=sys.stderr)
        return 130
    except Exception as exc:
        record_exception("batch_failed", exc, stage="batch_cli")
        print(f"批量验证失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
