import json
from pathlib import Path

import pytest

from dermademo.acceptance import CASES, generate_suite, summarize_report, verify_suite


ROOT = Path(__file__).resolve().parent.parent / "acceptance"


def test_committed_suite_decodes_and_matches_deterministic_generator(tmp_path):
    result = verify_suite(ROOT)
    assert len(result["checks"]) == 9
    first = generate_suite(tmp_path / "first")
    second = generate_suite(tmp_path / "second")
    assert first == second == json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert len(first["cases"]) == len(CASES) == 15
    with pytest.raises(FileExistsError):
        generate_suite(tmp_path / "first")


def test_suite_corruption_or_path_traversal_rejected(tmp_path):
    root = tmp_path / "suite"
    generate_suite(root)
    (root / "broken.png").write_bytes(b"bad replacement")
    with pytest.raises(ValueError, match="摘要"):
        verify_suite(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"] = {"../outside.png": manifest["files"]["broken.png"]}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="越界"):
        verify_suite(root)


def test_reports_do_not_hide_unrun_clean_machine_or_hardware_cases():
    report = json.loads((ROOT / "report-template.json").read_text(encoding="utf-8"))
    assert summarize_report(report)["counts"]["NOT_RUN"] == 15
    for row in report["cases"][:13]:
        row.update(status="PASS", evidence=["verified-source-test.txt"])
    report.update(build_version="test", release_sha256="a" * 64)
    summary = summarize_report(report)
    assert not summary["base_pc_accepted"]
    assert summary["counts"]["NOT_RUN"] == 2
    assert not summary["real_rgb_accepted"] and not summary["formal_model_accepted"]
    report["machine"]["no_python_clean_environment"] = True
    assert summarize_report(report)["base_pc_accepted"]
    report["cases"][0]["evidence"] = []
    with pytest.raises(ValueError, match="证据"):
        summarize_report(report)
