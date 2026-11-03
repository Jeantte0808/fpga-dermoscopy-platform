"""Single-frame stop-and-wait RGB UDP adapter. No START, model or Mask commands."""

from dataclasses import asdict
from hashlib import sha256
from ipaddress import IPv4Address
import json
from pathlib import Path
import secrets
import socket
from threading import Event, Lock
from time import monotonic, perf_counter
import zlib

import numpy as np

from .rgb_contracts import RGBOutcome, RGBRequest, RGBUDPConfig
from .rgb_protocol import CHUNK, COUNT, HEADER, PROFILE, TOTAL, RGBMode, RGBProtocolError, gaussian_rgb, header, response_fields
from .transport import TransferCancelled, TransferTimeout
from ..resources import resource_path
from ..diagnostic_logging import entry_session, record_exception, record_event


HANDOFF_ROOT = resource_path("rgb_root")
_FRAME_LOCK = Lock()


class _RGBExchange:
    def __init__(self, sock, config, cancel, deadline, report):
        self.sock, self.config, self.cancel, self.deadline, self.report = sock, config, cancel, deadline, report
        self.peer = config.board_ip, config.board_port

    def check(self):
        if self.cancel.is_set():
            raise TransferCancelled("PC 已停止等待；协议无取消命令，板卡状态需人工确认")
        if monotonic() >= self.deadline:
            raise TransferTimeout("RGB 整帧事务总期限已到，不自动重发整帧")

    def pause(self, seconds):
        self.check()
        self.cancel.wait(max(0, min(seconds, self.deadline - monotonic())))
        self.check()

    def exchange(self, request, sequence, magic, *, read=False):
        attempts = 12 if read else 3
        for attempt in range(attempts):
            self.check()
            if attempt:
                self.report["retransmissions"] += 1
            sent = self.sock.sendto(request, self.peer)
            if sent != len(request):
                raise OSError("UDP request was not sent in full")
            self.report["sent_datagrams"] += 1
            self.report["sent_udp_payload_bytes"] += sent
            attempt_deadline = min(self.deadline, monotonic() + self.config.timeout_seconds)
            while monotonic() < attempt_deadline:
                self.check()
                self.sock.settimeout(max(0.001, min(0.05, attempt_deadline - monotonic())))
                try:
                    data, source = self.sock.recvfrom(65535)
                except socket.timeout:
                    continue
                self.report["received_datagrams"] += 1
                self.report["received_udp_payload_bytes"] += len(data)
                if source != self.peer:
                    self.report["ignored_packets"] += 1
                    continue
                if len(data) >= HEADER.size:
                    fields = HEADER.unpack_from(data)
                    if (fields[1], fields[4]) != (self.report["frame_id"], sequence):
                        self.report["ignored_packets"] += 1
                        continue
                if data[:4] == b"BUSY":
                    response_fields(data, b"BUSY", self.report["frame_id"], sequence)
                    if not read:
                        raise RGBProtocolError("Unexpected BUSY during input upload")
                    self.report["busy_polls"] += 1
                    self.pause(0.01)
                    break
                fields = response_fields(data, magic, self.report["frame_id"], sequence)
                return data, fields
        raise TransferTimeout(f"RGB sequence {sequence}: no valid {magic.decode()} response after {attempts} attempts")


def run_rgb_udp(request: RGBRequest, config: RGBUDPConfig | None = None, cancel: Event | None = None, progress=None) -> RGBOutcome:
    """One caller owns its socket. Known failures return an exportable incomplete report."""
    if not isinstance(request, RGBRequest):
        raise ValueError("Expected an immutable RGB request")
    config = config if config is not None else RGBUDPConfig()
    if not isinstance(config, RGBUDPConfig):
        raise ValueError("Expected an immutable UDP configuration")
    cancel = cancel if cancel is not None else Event()
    frame_id = secrets.randbits(32)
    frame_crc = zlib.crc32(request.rgb)
    report = {
        "format": "alienhome.rgb-udp", "format_version": "1.0", "protocol_profile": PROFILE,
        "complete": False, "status": "pending", "phase": "preflight", "mode": request.mode.value,
        "origin": "protocol_simulator" if IPv4Address(config.board_ip).is_loopback else "udp_rgb_peer",
        "hardware_identity_verified": False, "model": None, "mask_available": False,
        "frame_id": frame_id, "config": asdict(config), "actual_local_endpoint": None,
        "input_bytes": TOTAL, "input_crc32": f"{frame_crc:08X}", "input_sha256": sha256(request.rgb).hexdigest(),
        "input_packets": 0, "output_packets": 0, "retransmissions": 0, "busy_polls": 0, "ignored_packets": 0,
        "sent_datagrams": 0, "received_datagrams": 0, "sent_udp_payload_bytes": 0, "received_udp_payload_bytes": 0,
        "last_acked_sequence": None, "last_output_sequence": None, "error": None,
        "upload_ms": None, "receive_ms": None, "transaction_ms": None,
        "timing_scope": "PC/network/frame transfer and automatic RGB processing; no isolated FPGA compute or CNN timing",
        "header_integrity": "no header CRC in delivered RGB protocol; echoed fields and application data CRC checked",
        "device_session_id": None, "device_boot_id": None, "model_token": None,
        "cancellation_scope": "host-only; no wire CANCEL/START/RELEASE",
        "manual_reset_may_be_needed": False,
    }
    started = perf_counter()
    sock, locked, returned = None, False, None
    try:
        if cancel.is_set():
            raise TransferCancelled("RGB 收发在开始前已取消")
        locked = _FRAME_LOCK.acquire(blocking=False)
        if not locked:
            report["status"] = "busy"
            raise RGBProtocolError("本进程已有一帧 RGB 在途，请等待完成")
        expected = request.rgb if request.mode == RGBMode.BYPASS else gaussian_rgb(request.rgb)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((config.bind_ip, config.local_port))
        report["actual_local_endpoint"] = list(sock.getsockname())
        exchange = _RGBExchange(sock, config, cancel, monotonic() + config.transaction_seconds, report)
        report["phase"] = "upload"
        upload_started = perf_counter()
        for sequence in range(COUNT):
            body = request.rgb[sequence * CHUNK:(sequence + 1) * CHUNK]
            packet = header(b"INP" + request.mode.suffix, frame_id, sequence, zlib.crc32(body), frame_crc) + body
            data, _fields = exchange.exchange(packet, sequence, b"ACK" + request.mode.suffix)
            if data != b"ACK" + request.mode.suffix + packet[4:HEADER.size]:
                raise RGBProtocolError("ACK fields differ from accepted input packet")
            report["input_packets"] += 1
            report["last_acked_sequence"] = sequence
            if progress is not None and (sequence + 1) % 8 == 0:
                progress("upload", sequence + 1)
        report["upload_ms"] = (perf_counter() - upload_started) * 1000
        # Last ACK is upload completion, not compute completion. No START exists.
        report["phase"] = "automatic_processing"
        exchange.pause(0.02)
        report["phase"] = "receive"
        receive_started = perf_counter()
        output, advertised_crc = bytearray(TOTAL), None
        for sequence in range(COUNT):
            packet = header(b"GET" + request.mode.suffix, frame_id, sequence, 0, frame_crc)
            data, fields = exchange.exchange(packet, sequence, b"OUT" + request.mode.suffix, read=True)
            body = data[HEADER.size:]
            if zlib.crc32(body) != fields[7]:
                raise RGBProtocolError("Output chunk CRC mismatch")
            if advertised_crc is not None and fields[8] != advertised_crc:
                raise RGBProtocolError("Output frame CRC changed between datagrams")
            advertised_crc = fields[8]
            output[sequence * CHUNK:(sequence + 1) * CHUNK] = body
            report["output_packets"] += 1
            report["last_output_sequence"] = sequence
            if progress is not None and (sequence + 1) % 8 == 0:
                progress("receive", sequence + 1)
        report["receive_ms"] = (perf_counter() - receive_started) * 1000
        returned = bytes(output)
        report["phase"] = "validate"
        delta = np.abs(np.frombuffer(returned, np.uint8).astype(np.int16) - np.frombuffer(expected, np.uint8).astype(np.int16))
        report.update({"output_crc32": f"{zlib.crc32(returned):08X}", "advertised_crc32": f"{advertised_crc:08X}",
                       "output_sha256": sha256(returned).hexdigest(), "bytes_out": len(returned),
                       "nonzero_bytes": int(np.count_nonzero(delta)), "max_diff": int(delta.max()), "mae": float(delta.mean())})
        if zlib.crc32(returned) != advertised_crc or np.any(delta):
            raise RGBProtocolError("完整输出 CRC 或逐字节软件参考比较失败")
        exchange.check()
        report.update(complete=True, status="complete", phase="complete")
    except TransferCancelled as exc:
        report.update(status="cancelled", error=str(exc))
    except TransferTimeout as exc:
        record_exception("rgb_transfer_failed", exc, stage=report["phase"], frame_id=frame_id, profile=PROFILE)
        report.update(status="timeout", error=str(exc))
    except RGBProtocolError as exc:
        record_exception("rgb_transfer_failed", exc, stage=report["phase"], frame_id=frame_id, profile=PROFILE)
        report.update(status="busy" if report["status"] == "busy" else "protocol_error", error=str(exc))
    except OSError as exc:
        record_exception("rgb_transfer_failed", exc, stage=report["phase"], frame_id=frame_id, profile=PROFILE)
        report.update(status="network_error", error=str(exc))
    finally:
        if sock is not None:
            sock.close()
        if locked:
            _FRAME_LOCK.release()
    report["transaction_ms"] = (perf_counter() - started) * 1000
    report["manual_reset_may_be_needed"] = bool(report["sent_datagrams"] and not report["complete"])
    record_event("rgb_transaction_finished", stage=report["phase"], outcome=report["status"], frame_id=frame_id,
                 profile=PROFILE, elapsed_ms=report["transaction_ms"])
    return RGBOutcome(request, config, frame_id, returned, json.dumps(report, ensure_ascii=False, allow_nan=False))


@entry_session("rgb_cli")
def main(argv=None):
    import argparse
    from .rgb_storage import save_rgb_outcome
    parser = argparse.ArgumentParser(description="成员一 RGB UDP 单帧联调：自动启动处理，无 START，无 Mask")
    parser.add_argument("--input", type=Path, default=HANDOFF_ROOT / "IMAGE.RAW")
    parser.add_argument("--mode", choices=("bypass", "gaussian"), default="bypass")
    parser.add_argument("--bind", default="192.168.0.3")
    parser.add_argument("--local-port", type=int, default=6102)
    parser.add_argument("--board", default="192.168.0.2")
    parser.add_argument("--board-port", type=int, default=5000)
    parser.add_argument("--timeout", type=float, default=1)
    parser.add_argument("--deadline", type=float, default=30)
    parser.add_argument("--output", type=Path, default=Path("outputs"))
    args = parser.parse_args(argv)
    try:
        config = RGBUDPConfig(args.bind, args.local_port, args.board, args.board_port, args.timeout, args.deadline)
        if args.input.stat().st_size != TOTAL:
            raise ValueError("RAW 文件必须恰好 196608 字节，不能直接传 PNG/JPEG 或 Mask")
        request = RGBRequest(args.input.read_bytes(), RGBMode(args.mode), "raw-file")
        args.output.mkdir(parents=True, exist_ok=True)
        outcome = run_rgb_udp(request, config)
        directory = save_rgb_outcome(args.output, outcome)
    except (OSError, ValueError) as exc:
        print(f"RGB UDP error: {exc}")
        return 1
    print(json.dumps({"complete": outcome.complete, "status": outcome.report["status"],
                      "origin": outcome.report["origin"], "error": outcome.report["error"], "output": str(directory)}, ensure_ascii=False))
    return 0 if outcome.complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
