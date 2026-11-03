from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
from threading import Event, Thread
from time import monotonic
import zlib

import numpy as np
import pytest

from dermademo.device.rgb_protocol import CHUNK, COUNT, HEADER, RGBMode, RGBProtocolError, gaussian_rgb, header
from dermademo.device.rgb_storage import save_rgb_outcome
from dermademo.device.rgb_udp import HANDOFF_ROOT, RGBRequest, RGBUDPConfig, run_rgb_udp
from rgb_udp_peer import RGBPeer


@pytest.fixture(scope="module")
def raw():
    return (HANDOFF_ROOT / "IMAGE.RAW").read_bytes()


@pytest.fixture(scope="module")
def reference():
    # Reviewed delivered reference contains only imports and pure function definitions.
    return runpy.run_path(str(HANDOFF_ROOT / "reference.py.txt"))["gaussian_rgb"]


def config(peer, timeout=0.03, deadline=3):
    return RGBUDPConfig("127.0.0.1", 0, "127.0.0.1", peer.port, timeout, deadline)


def test_imported_manifest_and_real_historical_evidence(raw):
    manifest = json.loads((HANDOFF_ROOT / "SHA256.json").read_text(encoding="utf-8"))
    for name, digest in manifest.items():
        if name.startswith("bit/"):
            continue
        local = HANDOFF_ROOT / (name + ".txt" if name.endswith(".py") else name)
        assert sha256(local.read_bytes()).hexdigest() == digest
    assert len(raw) == 196608 and raw == (HANDOFF_ROOT / "sample/0_bypass.raw").read_bytes()
    assert f"{zlib.crc32(raw):08X}" == "4B47E72A"
    historical = json.loads((HANDOFF_ROOT / "sample/report.json").read_text(encoding="utf-8"))
    assert historical["complete"]
    assert historical["input_sha256"] == sha256(raw).hexdigest()
    assert json.loads((HANDOFF_ROOT / "IMPORT.json").read_text())["manifest_files_verified"] == 17


def test_exact_delivered_datagrams(raw):
    metadata = json.loads((HANDOFF_ROOT / "packet_examples/metadata.json").read_text(encoding="utf-8"))
    frame = metadata["frame_id"]
    part_crc, full_crc = zlib.crc32(raw[:1024]), zlib.crc32(raw)
    for magic in (b"INP0", b"ACK0", b"GET0", b"OUT0"):
        packet = header(magic, frame, 0, 0 if magic == b"GET0" else part_crc, full_crc)
        if magic in (b"INP0", b"OUT0"):
            packet += raw[:1024]
        assert packet == (HANDOFF_ROOT / f"packet_examples/{magic.decode()}_seq000.bin").read_bytes()
        assert sha256(packet).hexdigest() == metadata["packets"][f"{magic.decode()}_seq000.bin"]["sha256"]
    assert HEADER.size == 32 and CHUNK * COUNT == len(raw)


def test_gaussian_matches_delivered_integer_reference(raw, reference):
    assert gaussian_rgb(raw) == reference(raw)
    y, x = np.indices((256, 256))
    asymmetric = np.stack((x % 256, y % 256, (x * 23 + y * 3) % 256), axis=2).astype(np.uint8).tobytes()
    assert gaussian_rgb(asymmetric) == reference(asymmetric)
    historical = json.loads((HANDOFF_ROOT / "sample/report.json").read_text(encoding="utf-8"))
    assert sha256(gaussian_rgb(raw)).hexdigest() == historical["frames"][1]["output_sha256"]


@pytest.mark.parametrize("mode", list(RGBMode))
def test_actual_udp_roundtrip_no_start_and_closed_socket(raw, reference, mode):
    expected = raw if mode == RGBMode.BYPASS else reference(raw)
    with RGBPeer(gaussian=lambda _raw: expected) as peer:
        outcome = run_rgb_udp(RGBRequest(raw, mode, "handoff-raw"), config(peer))
    assert outcome.complete, outcome.report
    assert outcome.returned == expected
    report = outcome.report
    assert report["input_packets"] == report["output_packets"] == 192
    assert report["retransmissions"] == 0 and report["origin"] == "protocol_simulator"
    assert report["model"] is None and report["mask_available"] is False
    assert report["device_session_id"] is report["model_token"] is None
    assert {request[0] for request in peer.requests} == {b"INP" + mode.suffix, b"GET" + mode.suffix}
    assert report["sent_udp_payload_bytes"] == 192 * (1056 + 32)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(tuple(report["actual_local_endpoint"]))


@pytest.mark.parametrize("fault", ["drop_ack", "drop_out", "busy", "stale", "wrong_source"])
def test_confirmed_retry_busy_and_stale_semantics(raw, fault):
    with RGBPeer(fault) as peer:
        outcome = run_rgb_udp(RGBRequest(raw), config(peer))
    assert outcome.complete, outcome.report
    report = outcome.report
    if fault in ("drop_ack", "drop_out"):
        assert report["retransmissions"] == 1
        duplicates = [p for p in peer.requests if p[0][:3] == (b"INP" if fault == "drop_ack" else b"GET") and p[2] == 4]
        assert len(duplicates) == 2 and duplicates[0][3] == duplicates[1][3]
    elif fault == "busy":
        assert report["busy_polls"] == 2 and report["retransmissions"] == 2
    else:
        assert report["ignored_packets"] == (2 if fault == "stale" else 1)


@pytest.mark.parametrize("fault", ["bad_ack", "short_ack", "long_ack", "wrong_mode", "error", "bad_out_crc",
                                   "bad_frame_crc", "changed_frame_crc", "short_output", "bad_reference"])
def test_corruption_and_error_stop_without_restarting_frame(raw, fault):
    with RGBPeer(fault) as peer:
        outcome = run_rgb_udp(RGBRequest(raw), config(peer))
    assert not outcome.complete and outcome.report["status"] == "protocol_error"
    assert outcome.report["retransmissions"] == 0 and outcome.report["manual_reset_may_be_needed"]
    assert sum(magic == b"INP0" and seq == 0 for magic, _fid, seq, _wire in peer.requests) == 1
    if fault == "bad_reference":
        assert outcome.report["nonzero_bytes"] == 1 and outcome.returned is not None


@pytest.mark.parametrize("fault,attempts", [("timeout_upload", 3), ("timeout_get", 12), ("busy_forever", 12)])
def test_attempt_limits_and_finite_total_deadline(raw, fault, attempts):
    with RGBPeer(fault) as peer:
        outcome = run_rgb_udp(RGBRequest(raw), config(peer, timeout=0.01))
    assert not outcome.complete and outcome.report["status"] == "timeout"
    relevant = b"INP0" if fault == "timeout_upload" else b"GET0"
    assert sum(magic == relevant and seq == 0 for magic, _frame, seq, _wire in peer.requests) == attempts
    with RGBPeer("timeout_upload") as peer:
        started = monotonic()
        outcome = run_rgb_udp(RGBRequest(raw), config(peer, timeout=1, deadline=0.06))
        assert monotonic() - started < 0.5 and outcome.report["status"] == "timeout"


def test_cooperative_cancel_and_single_frame_guard(raw):
    with RGBPeer("delay") as peer:
        cancel, outcomes = Event(), []
        thread = Thread(target=lambda: outcomes.append(run_rgb_udp(RGBRequest(raw), config(peer), cancel)))
        thread.start()
        assert peer.got_input.wait(1)
        busy = run_rgb_udp(RGBRequest(raw), config(peer))
        assert not busy.complete and busy.report["status"] == "busy" and busy.report["sent_datagrams"] == 0
        cancel.set()
        thread.join(1)
        assert not thread.is_alive() and outcomes[0].report["status"] == "cancelled"
    cancel = Event()
    cancel.set()
    assert run_rgb_udp(RGBRequest(raw), cancel=cancel).report["sent_datagrams"] == 0


def test_bind_error_is_reported_and_immutable_report_snapshot(raw):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as occupied:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        occupied.bind(("127.0.0.1", 0))
        outcome = run_rgb_udp(RGBRequest(raw), RGBUDPConfig("127.0.0.1", occupied.getsockname()[1], "127.0.0.1", 5000))
    assert not outcome.complete and outcome.report["status"] == "network_error"
    assert outcome.report["sent_datagrams"] == 0
    report = outcome.report
    report["complete"] = True
    assert not outcome.complete


def test_success_and_failure_export_provenance_hashes_and_incomplete_marker(raw, tmp_path, monkeypatch):
    for fault in ("none", "bad_reference", "timeout_upload"):
        with RGBPeer(fault) as peer:
            outcome = run_rgb_udp(RGBRequest(raw), config(peer, timeout=0.01))
        directory = save_rgb_outcome(tmp_path, outcome)
        report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
        assert report["complete"] == (fault == "none") and not report["hardware_identity_verified"]
        assert report["model"] is None and not report["mask_available"]
        assert not (directory / "record.json").exists() and not (directory / "INCOMPLETE").exists()
        for name, evidence in report["files"].items():
            assert sha256((directory / name).read_bytes()).hexdigest() == evidence["sha256"]
        if fault == "bad_reference":
            assert (directory / "unverified_return.rgb").exists() and not (directory / "returned.png").exists()
    write = Path.write_bytes
    def fail(path, data):
        if path.name == "input.rgb":
            raise OSError("disk unavailable")
        return write(path, data)
    monkeypatch.setattr(Path, "write_bytes", fail)
    with pytest.raises(OSError):
        save_rgb_outcome(tmp_path, outcome)
    assert any((p / "INCOMPLETE").exists() for p in tmp_path.glob("rgb_udp_*"))


@pytest.mark.parametrize("launch", ["import", "module"])
def test_cli_real_udp_and_no_qt_import(raw, tmp_path, launch):
    with RGBPeer() as peer:
        script = "import sys; from dermademo.device.rgb_udp import main; code=main(); assert 'PySide6' not in sys.modules; raise SystemExit(code)"
        entry = ["-c", script] if launch == "import" else ["-m", "dermademo.device.rgb_udp"]
        result = subprocess.run([sys.executable, *entry, "--bind", "127.0.0.1", "--local-port", "0",
                                 "--board", "127.0.0.1", "--board-port", str(peer.port), "--output", str(tmp_path)],
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout)["complete"] is True
    result = subprocess.run([sys.executable, "-m", "dermademo.device.rgb_udp", "--mode", "mask"], capture_output=True, timeout=10)
    assert result.returncode == 2


def test_module_cli_exports_failed_bind_without_sending(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as occupied:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        occupied.bind(("127.0.0.1", 0))
        result = subprocess.run([sys.executable, "-m", "dermademo.device.rgb_udp", "--bind", "127.0.0.1",
                                 "--local-port", str(occupied.getsockname()[1]), "--board", "127.0.0.1",
                                 "--output", str(tmp_path)], capture_output=True, text=True, encoding="utf-8",
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=10)
    assert result.returncode == 1
    summary = json.loads(result.stdout)
    assert not summary["complete"] and summary["status"] == "network_error"
    report = json.loads((Path(summary["output"]) / "report.json").read_text(encoding="utf-8"))
    assert report["sent_datagrams"] == 0 and report["input_packets"] == report["output_packets"] == 0
    assert report["phase"] == "preflight" and not report["manual_reset_may_be_needed"]
    assert not (Path(summary["output"]) / "INCOMPLETE").exists()


@pytest.mark.parametrize("changes", [{"board_ip": "fpga.local"}, {"board_ip": "0.0.0.0"}, {"board_ip": "224.0.0.1"},
                                     {"board_port": 0}, {"local_port": True}, {"timeout_seconds": float("nan")},
                                     {"transaction_seconds": float("inf")}, {"timeout_seconds": 0}])
def test_invalid_config_never_creates_socket(raw, changes, monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *_a, **_k: pytest.fail("Invalid config opened socket"))
    with pytest.raises(ValueError):
        run_rgb_udp(RGBRequest(raw), replace(RGBUDPConfig(), **changes))


def test_mask_wrong_mode_and_invalid_header_rejected():
    with pytest.raises(ValueError):
        RGBRequest(bytes(65536))
    with pytest.raises(ValueError):
        RGBRequest(bytes(196608), "bypass")
    for arguments in [(b"STRT", 1, 0, 0, 0), (b"INP0", True, 0, 0, 0), (b"GET0", 1, 192, 0, 0)]:
        with pytest.raises(RGBProtocolError):
            header(*arguments)
