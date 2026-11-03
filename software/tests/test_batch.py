from dataclasses import replace
import csv
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest

from dermademo.batch import BatchOptions, main, overlap_scores, run_batch, statistics
from dermademo.records import load_record
from test_registry import ContractBackend, contract_registry


def images(tmp_path):
    root = tmp_path / "中文输入"
    root.mkdir()
    image = np.full((20, 30, 3), 200, np.uint8)
    image[4:12, 5:14] = 50
    Image.fromarray(image).save(root / "病灶.png")
    return root, image


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_batch_runs_repeats_excludes_warmup_and_saves_reopenable_records(tmp_path):
    root, image = images(tmp_path)
    calls = []
    class TrackingBackend(ContractBackend):
        def process(self, request):
            calls.append(request)
            return super().process(request)
    outcome = run_batch(BatchOptions(root, tmp_path / "输出", backend_key="contract_test", warmup=2, repeat=3,
                                    save_results=True, synthetic=True), registry=contract_registry(TrackingBackend))
    assert outcome.exit_code == 0
    assert len(calls) == 5 and len({request.task_id for request in calls}) == 5
    assert all(not request.image_bgr.flags.writeable for request in calls)
    report = outcome.report
    assert report["counts"]["succeeded"] == 1
    assert report["timings_ms"]["end_to_end"]["count"] == 3
    assert report["timings_ms"]["send"]["median"] is None
    assert report["accuracy"]["dice_macro_mean"] is None
    assert report["items"][0]["results_stable"]
    assert report["items"][0]["is_synthetic"]
    assert len(read_csv(outcome.directory / "runs.csv")) == 3
    rows = read_csv(outcome.directory / "images.csv")
    assert rows[0]["input"] == "病灶.png" and rows[0]["dice"] == ""
    saved = load_record(outcome.directory / rows[0]["record"])
    np.testing.assert_array_equal(saved.request.image_bgr[:, :, ::-1], image)
    assert saved.request.task_id == calls[-1].task_id
    assert saved.result.timings_ms["end_to_end"] != 0
    assert saved.result.timings_ms["backend_pipeline"] == 0
    assert saved.request.is_synthetic
    assert not (outcome.directory / "INCOMPLETE.txt").exists()
    assert json.loads((outcome.directory / "report.json").read_text(encoding="utf-8")) == report


def test_bad_image_does_not_stop_later_inputs_and_missing_values_stay_null(tmp_path):
    root, _ = images(tmp_path)
    (root / "00坏图.png").write_bytes(b"broken")
    outcome = run_batch(BatchOptions(root, tmp_path / "out", warmup=0, repeat=1))
    assert outcome.exit_code == 1
    assert outcome.report["counts"]["failed"] == 1
    assert outcome.report["counts"]["succeeded"] == 1
    rows = read_csv(outcome.directory / "images.csv")
    assert rows[0]["status"] == "failed" and rows[0]["error_stage"] == "load"
    assert rows[0]["area_px2"] == "" and rows[0]["end_to_end_median_ms"] == ""
    assert rows[1]["status"] == "ok"
    assert outcome.report["timings_ms"]["end_to_end"]["count"] == 1


def test_labels_are_exact_binary_original_coordinates_and_missing_are_errors(tmp_path):
    root, image = images(tmp_path)
    Image.fromarray(image).save(root / "未标注.png")
    labels = tmp_path / "labels"
    labels.mkdir()
    mask = np.where(image[:, :, 0] == 50, 255, 0).astype(np.uint8)
    Image.fromarray(mask).save(labels / "病灶.png.mask.png")
    outcome = run_batch(BatchOptions(root, tmp_path / "out", "contract_test", labels_dir=labels, repeat=1),
                        registry=contract_registry())
    assert outcome.exit_code == 1
    assert outcome.report["counts"]["succeeded"] == 2
    assert outcome.report["counts"]["annotation_errors"] == 1
    assert outcome.report["accuracy"] == {"count": 1, "dice_macro_mean": 1.0, "iou_macro_mean": 1.0}
    missing = next(item for item in outcome.report["items"] if item["annotation_status"] == "missing")
    assert "dice" not in missing
    assert "annotation_sha256" in next(item for item in outcome.report["items"] if item["annotation_status"] == "ok")


@pytest.mark.parametrize("mode,size,value", [("RGB", (30, 20), 255), ("L", (30, 20), 1), ("L", (15, 10), 255)])
def test_invalid_labels_do_not_get_silently_resized_or_converted(tmp_path, mode, size, value):
    root, _ = images(tmp_path)
    labels = tmp_path / "labels"
    labels.mkdir()
    Image.new(mode, size, value).save(labels / "病灶.png.mask.png")
    outcome = run_batch(BatchOptions(root, tmp_path / "out", labels_dir=labels, repeat=1))
    assert outcome.exit_code == 1
    assert outcome.report["items"][0]["annotation_status"] == "invalid"
    assert outcome.report["accuracy"]["count"] == 0
    assert outcome.report["accuracy"]["iou_macro_mean"] is None


def test_overlap_and_statistics_are_defined_for_empty_and_partial_masks():
    empty = np.zeros((3, 3), np.uint8)
    one = empty.copy()
    one[0, :2] = 255
    other = empty.copy()
    other[0, 1:] = 255
    assert overlap_scores(empty, empty) == (1, 1)
    assert overlap_scores(one, empty) == (0, 0)
    dice, iou = overlap_scores(one, other)
    assert dice == pytest.approx(0.5) and iou == pytest.approx(1 / 3)
    assert statistics([1, 2, 3, 4]) == {"count": 4, "median": 2.5, "p95": pytest.approx(3.85)}
    assert statistics([]) == {"count": 0, "median": None, "p95": None}


def test_recursive_scan_excludes_output_labels_and_does_not_overwrite(tmp_path):
    root, image = images(tmp_path)
    nested = root / "sub"
    nested.mkdir()
    Image.fromarray(image).save(nested / "病灶.png")
    output, labels = root / "reports", root / "labels"
    output.mkdir()
    labels.mkdir()
    Image.fromarray(image).save(output / "old.png")
    Image.fromarray(image).save(labels / "mask.png")
    options = BatchOptions(root, output, recursive=True, repeat=1, warmup=0)
    # Annotation directory is excluded only when explicitly supplied.
    options = replace(options, labels_dir=labels)
    first = run_batch(options)
    second = run_batch(options)
    assert first.directory != second.directory
    assert first.report["discovered_images"] == second.report["discovered_images"] == 2
    assert [item["input"] for item in first.report["items"]] == ["sub/病灶.png", "病灶.png"]
    assert (output / "old.png").exists()


def test_processing_failure_keeps_partial_runs_but_excludes_them_from_summary(tmp_path):
    root, _ = images(tmp_path)
    class Failure(ContractBackend):
        calls = 0
        def process(self, request):
            self.calls += 1
            if self.calls == 2:
                raise ValueError("模拟后端错误")
            return super().process(request)
    outcome = run_batch(BatchOptions(root, tmp_path / "out", "contract_test", warmup=0, repeat=3),
                        registry=contract_registry(Failure))
    assert outcome.report["items"][0]["completed_repeats"] == 1
    assert len(read_csv(outcome.directory / "runs.csv")) == 1
    assert outcome.report["timings_ms"]["end_to_end"]["count"] == 0
    assert outcome.report["items"][0]["error_stage"] == "process"
    assert outcome.exit_code == 1


def test_repeat_variation_is_visible(tmp_path):
    root, _ = images(tmp_path)
    class Varying(ContractBackend):
        calls = 0
        def process(self, request):
            self.calls += 1
            if self.calls % 2:
                return super().process(request)
            empty = np.full_like(request.image_bgr, 200)
            return super().process(replace(request, image_bgr=empty))
    outcome = run_batch(BatchOptions(root, tmp_path / "out", "contract_test", warmup=0, repeat=2),
                        registry=contract_registry(Varying))
    assert outcome.report["counts"]["unstable_results"] == 1
    assert outcome.report["items"][0]["results_stable"] is False


def test_cancellation_finalizes_partial_report(tmp_path):
    root, image = images(tmp_path)
    Image.fromarray(image).save(root / "第二张.png")
    completed = []
    outcome = run_batch(BatchOptions(root, tmp_path / "out", warmup=0, repeat=1),
                        progress=lambda item, _n, _total: completed.append(item), cancelled=lambda: bool(completed))
    assert outcome.exit_code == 130
    assert outcome.report["status"] == "cancelled"
    assert outcome.report["counts"]["succeeded"] == 1 and outcome.report["counts"]["not_started"] == 1
    assert not (outcome.directory / "INCOMPLETE.txt").exists()


def test_report_write_failure_retains_incomplete_marker(tmp_path, monkeypatch):
    root, _ = images(tmp_path)
    import dermademo.batch as batch
    original_write = batch._write_report
    def failing_write(directory, report):
        if report["status"] != "running":
            raise OSError("模拟报告写入失败")
        return original_write(directory, report)
    monkeypatch.setattr(batch, "_write_report", failing_write)
    with pytest.raises(OSError):
        run_batch(BatchOptions(root, tmp_path / "out", warmup=0, repeat=1))
    directories = list((tmp_path / "out").iterdir())
    assert len(directories) == 1 and (directories[0] / "INCOMPLETE.txt").exists()
    assert (directories[0] / "images.csv").exists()


def test_export_failure_is_reported_separately_and_not_counted_as_success(tmp_path, monkeypatch):
    root, _ = images(tmp_path)
    import dermademo.storage as storage
    def fail(*args):
        raise OSError("模拟图片写入失败")
    monkeypatch.setattr(storage, "write_png", fail)
    outcome = run_batch(BatchOptions(root, tmp_path / "out", warmup=0, repeat=1, save_results=True))
    item = outcome.report["items"][0]
    assert item["status"] == "failed" and item["error_stage"] == "export"
    assert (outcome.directory / item["record"] / "INCOMPLETE.txt").exists()
    assert outcome.exit_code == 1 and outcome.report["counts"]["succeeded"] == 0


@pytest.mark.parametrize("change", [{"repeat": 0}, {"warmup": -1}, {"repeat": True}, {"backend_key": "fpga"},
                                    {"config_document": {"probability_threshold": 0.5}}])
def test_invalid_batch_request_fails_before_creating_output(tmp_path, change):
    root, _ = images(tmp_path)
    output = tmp_path / "out"
    with pytest.raises(ValueError):
        run_batch(replace(BatchOptions(root, output), **change))
    assert not output.exists()


def test_cli_exit_codes_and_power_shell_launcher(tmp_path, capsys):
    assert main(["--list-backends"]) == 0
    assert '"demo_cpu"' in capsys.readouterr().out
    assert main([str(tmp_path / "missing"), "--output", str(tmp_path / "out")]) == 2
    root, _ = images(tmp_path)
    (root / "坏图.png").write_bytes(b"broken")
    assert main([str(root), "--output", str(tmp_path / "out"), "--repeat", "1", "--warmup", "0"]) == 1
    project = Path(__file__).resolve().parents[1]
    invocation = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(project / "run_batch.ps1"), "-InputDirectory", str(root), "-OutputDirectory", str(tmp_path / "PS 中文输出"),
        "-Repeat", "1", "-Warmup", "0", "-SaveResults"], cwd=tmp_path, capture_output=True)
    assert invocation.returncode == 1, invocation.stderr.decode(errors="replace")
    assert len(list((tmp_path / "PS 中文输出").glob("batch_*/report.json"))) == 1


def test_initialization_failure_produces_cli_error_code_two(tmp_path, monkeypatch):
    root, _ = images(tmp_path)
    import dermademo.batch as batch
    registry = contract_registry()
    def failing_open(_key):
        raise RuntimeError("运行时初始化失败")
    monkeypatch.setattr(registry, "open", failing_open)
    monkeypatch.setattr(batch, "create_registry", lambda: registry)
    assert main([str(root), "--backend", "contract_test", "--output", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("data", [None, {"backend_key": "demo_cpu", "config_version": "1", "values": None}])
def test_null_config_file_is_rejected_instead_of_silently_using_defaults(tmp_path, data):
    root, _ = images(tmp_path)
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(data), encoding="utf-8")
    assert main([str(root), "--config", str(config_file), "--output", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out").exists()
