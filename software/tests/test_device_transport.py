from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess
import sys
from threading import Event, Thread
from time import monotonic, sleep

import cv2
import numpy as np
import pytest

from dermademo.device.codec import Message, Packet, ProtocolError, Tag, encode_tlvs
from dermademo.device.diagnostics import save_lab_result
from dermademo.device.imaging import diagnostic_pattern, prepare_lab_input
from dermademo.device.lab import (LAB_CAPABILITIES, LAB_PREPROCESS_ID, LabFaults, LabMode, LabRequest,
                                  LabSession, LoopbackLab, _geometry, fixture_mask, run_loopback)
from dermademo.device.transport import TCPChannel, TransferCancelled, TransferTimeout
from dermademo.device.assembly import object_packets
import zlib


@pytest.mark.parametrize("mode", list(LabMode))
def test_real_loopback_roundtrip_and_cleanup(mode):
    request = LabRequest(diagnostic_pattern(), mode)
    result = run_loopback(request)
    expected = request.rgb if mode == LabMode.RGB_BYPASS else fixture_mask() if mode == LabMode.MASK_FIXTURE else cv2.GaussianBlur(
        np.frombuffer(request.rgb, np.uint8).reshape(256, 256, 3), (3, 3), 0, borderType=cv2.BORDER_REFLECT_101).tobytes()
    assert result.returned == expected
    assert result.origin == "protocol_simulator" and result.endpoint[0] == "127.0.0.1"
    assert result.sent_wire_bytes > len(request.rgb)
    assert result.received_wire_bytes > len(expected)
    assert all(value >= 0 for _name, value in result.timings_ms)
    with socket.socket() as probe:
        probe.settimeout(0.2)
        assert probe.connect_ex(result.endpoint) != 0  # Listener and accepted connection are closed.


def test_reordered_repeated_result_is_safe():
    result = run_loopback(faults=LabFaults(reorder_and_duplicate=True))
    assert result.returned == result.request.rgb


@pytest.mark.parametrize("fault", [LabFaults(corrupt_result=True), LabFaults(wrong_session=True),
                                   LabFaults(wrong_format=True), LabFaults(disconnect_result=True)])
def test_faults_rejected_without_partial_result(fault):
    with pytest.raises((ProtocolError, ConnectionError, OSError)):
        run_loopback(faults=fault)
    # A completely new session works after every failure, no implicit retry.
    assert run_loopback().returned == diagnostic_pattern()


def test_deadline_and_cooperative_cancel_are_bounded():
    started = monotonic()
    with pytest.raises(TransferTimeout):
        run_loopback(LabRequest(diagnostic_pattern(), timeout_seconds=0.15), faults=LabFaults(delay_seconds=2))
    assert monotonic() - started < 1.5
    cancel, errors = Event(), []
    def work():
        try:
            run_loopback(cancel=cancel, faults=LabFaults(delay_seconds=3))
        except Exception as exc:
            errors.append(exc)
    thread = Thread(target=work)
    thread.start()
    sleep(0.1)
    cancel.set()
    thread.join(1)
    assert not thread.is_alive() and len(errors) == 1 and isinstance(errors[0], TransferCancelled)
    with pytest.raises(TransferCancelled):
        run_loopback(cancel=cancel)


def test_server_no_client_cleanup_and_single_io_owner():
    with LoopbackLab() as lab:
        endpoint = lab.endpoint
    assert not lab.thread.is_alive()
    with LoopbackLab() as lab:
        connection = socket.create_connection(lab.endpoint)
        with TCPChannel(connection, Event(), monotonic() + 2) as channel:
            errors = []
            def wrong_owner():
                try:
                    channel.receive()
                except Exception as exc:
                    errors.append(exc)
            worker = Thread(target=wrong_owner)
            worker.start()
            worker.join(1)
            assert isinstance(errors[0], RuntimeError)
    assert endpoint[0] == "127.0.0.1" and not lab.thread.is_alive()


def test_duplicate_run_is_idempotent_and_conflicting_command_is_rejected():
    rgb = diagnostic_pattern()
    with LoopbackLab() as lab:
        with TCPChannel(socket.create_connection(lab.endpoint), Event(), monotonic() + 3) as channel:
            client = LabSession(channel, 100)
            client.command(Message.HELLO, {Tag.CLIENT_NONCE: 100, Tag.CAPABILITIES: LAB_CAPABILITIES,
                           Tag.MAX_CHUNK_BYTES: 1024, Tag.WINDOW_CHUNKS: 16, Tag.MAX_INFLIGHT_FRAMES: 1}, frame=False)
            declaration = _geometry(1, LabMode.RGB_BYPASS, rgb)
            declaration.update({Tag.RESULT_PIXEL_FORMAT: 1, Tag.PREPROCESS_PROFILE_ID: LAB_PREPROCESS_ID})
            client.command(Message.FRAME_BEGIN, declaration)
            for packet in object_packets(Message.FRAME_DATA, rgb, session_id=100, frame_id=1, transfer_id=1):
                channel.send(packet)
            client.command(Message.FRAME_COMMIT, {Tag.OBJECT_BYTES: len(rgb), Tag.OBJECT_CRC32: zlib.crc32(rgb)})
            run = Packet(Message.RUN, 100, frame_id=1, command_id=4, transfer_id=1)
            channel.send(run)
            first = channel.receive()
            channel.send(run)
            assert channel.receive() == first and lab.run_count == 1
            # Even a new ID for the same frame must not cause another computation.
            client._command_id = 4
            client.command(Message.RUN)
            assert lab.run_count == 1
            channel.send(Packet(Message.RUN, 100, encode_tlvs({Tag.MODEL_TOKEN: 9}), frame_id=1, command_id=4, transfer_id=1))
            with pytest.raises(ConnectionError):
                channel.receive()
    assert isinstance(lab.error, ProtocolError)


def test_export_provenance_bytes_hashes_and_original_geometry(tmp_path):
    image = np.zeros((90, 150, 3), np.uint8)
    prepared = prepare_lab_input(image, "letterbox")
    result = run_loopback(LabRequest(prepared.rgb, LabMode.MASK_FIXTURE, prepared=prepared))
    directory = save_lab_result(tmp_path, result)
    document = json.loads((directory / "diagnostic.json").read_text(encoding="utf-8"))
    assert document["format"] == "alienhome.communication-lab"
    assert document["hardware_verified"] is False and document["model"] is None
    assert document["origin"] == "protocol_simulator" and document["private_fixture_extension"]
    assert document["input"]["enhancement_applied"] is False
    assert document["input"]["transform"]["original_width"] == 150
    assert not (directory / "INCOMPLETE").exists() and not (directory / "record.json").exists()
    for filename, evidence in document["files"].items():
        data = (directory / filename).read_bytes()
        assert len(data) == evidence["bytes"] and sha256(data).hexdigest() == evidence["sha256"]
    restored = cv2.imdecode(np.frombuffer((directory / "fixture_mask_original_coordinates.png").read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
    assert restored.shape == image.shape[:2] and set(np.unique(restored)) == {0, 255}
    assert save_lab_result(tmp_path, result) != directory


def test_cli_no_qt_no_remote_arguments(tmp_path):
    script = "import sys; from dermademo.device.__main__ import main; result=main(); assert 'PySide6' not in sys.modules; raise SystemExit(result)"
    command = [sys.executable, "-c", script, "--mode", "mask-fixture", "--output", str(tmp_path)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["hardware_verified"] is False
    unsupported = subprocess.run([sys.executable, "-m", "dermademo.device", "--host", "192.168.1.2"], capture_output=True, text=True, timeout=10)
    assert unsupported.returncode == 2


def test_failed_export_retains_incomplete_marker(tmp_path, monkeypatch):
    result = run_loopback()
    original = Path.write_bytes
    def fail_partial(path, data):
        if path.name == "result.rgb":
            raise OSError("simulated disk write failure")
        return original(path, data)
    monkeypatch.setattr(Path, "write_bytes", fail_partial)
    with pytest.raises(OSError, match="simulated disk"):
        save_lab_result(tmp_path, result)
    exported = list(tmp_path.glob("communication_lab_*"))
    assert len(exported) == 1 and (exported[0] / "INCOMPLETE").is_file()
    assert not (exported[0] / "diagnostic.json").exists()


@pytest.mark.parametrize("timeout", [0, float("nan"), float("inf"), True, 31])
def test_invalid_requests_do_not_open_sockets(timeout, monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *_a, **_k: pytest.fail("Invalid input opened a socket"))
    with pytest.raises(ValueError):
        run_loopback(LabRequest(diagnostic_pattern(), timeout_seconds=timeout))
