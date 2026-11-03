"""Explicit loopback software experiment. This is NOT a real FPGA adapter.

Every experiment owns a fresh localhost server, connection and random session.
Mode 128 is a PRIVATE laboratory fixture extension, never a model protocol.
The candidate JSON cannot be used as an executable device configuration.
"""

from dataclasses import dataclass
from enum import IntEnum
import math
import secrets
import socket
from threading import Event, Thread
from time import monotonic, perf_counter
import zlib

import cv2
import numpy as np

from .assembly import ObjectAssembler, object_packets
from .codec import ERROR, RESPONSE, Message, Packet, ProtocolError, Status, Tag, decode_tlvs, encode_tlvs
from .imaging import PreparedInput, diagnostic_pattern
from .transport import TCPChannel, TransferCancelled


class LabMode(IntEnum):
    RGB_BYPASS = 0
    GAUSSIAN = 1
    MASK_FIXTURE = 128


LAB_ID = "AHFP-LOOPBACK-NOT-FPGA"
LAB_VERSION = "software-lab/0.1.0"
LAB_CAPABILITIES = 3 | (1 << 31)  # bypass, Gaussian, private software fixture; no model bit
LAB_PREPROCESS_ID = 0xffff0001
RGB_BYTES, MASK_BYTES = 196608, 65536


@dataclass(frozen=True)
class LabRequest:
    rgb: bytes
    mode: LabMode = LabMode.RGB_BYPASS
    timeout_seconds: float = 5.0
    prepared: PreparedInput | None = None

    def __post_init__(self):
        if (type(self.rgb) is not bytes or len(self.rgb) != RGB_BYTES
                or not isinstance(self.mode, LabMode)
                or type(self.timeout_seconds) not in (int, float)
                or not math.isfinite(self.timeout_seconds) or not 0.1 <= self.timeout_seconds <= 30):
            raise ValueError("Invalid bounded software laboratory request")
        if self.prepared is not None and (not isinstance(self.prepared, PreparedInput) or self.prepared.rgb != self.rgb):
            raise ValueError("Input transform does not match the immutable RGB snapshot")


@dataclass(frozen=True)
class LabResult:
    request: LabRequest
    returned: bytes
    session_id: int
    boot_id: int
    frame_id: int
    input_transfer_id: int
    output_transfer_id: int
    endpoint: tuple[str, int]
    sent_wire_bytes: int
    received_wire_bytes: int
    timings_ms: tuple[tuple[str, float], ...]
    origin: str = "protocol_simulator"


@dataclass(frozen=True)
class LabFaults:
    """Test-only deterministic faults, never inferred from external configuration."""
    delay_seconds: float = 0
    corrupt_result: bool = False
    wrong_session: bool = False
    wrong_format: bool = False
    disconnect_result: bool = False
    reorder_and_duplicate: bool = False

    def __post_init__(self):
        if not math.isfinite(self.delay_seconds) or not 0 <= self.delay_seconds <= 10:
            raise ValueError("Invalid bounded fault delay")


def fixture_mask() -> bytes:
    """Fixed square regardless of input: protocol fixture, NOT lesion segmentation."""
    mask = np.zeros((256, 256), np.uint8)
    mask[64:192, 64:192] = 255
    return mask.tobytes()


def _geometry(pixel_format, mode, data):
    return {Tag.WIDTH: 256, Tag.HEIGHT: 256, Tag.ROW_STRIDE: 768 if pixel_format == 1 else 256,
            Tag.PIXEL_FORMAT: pixel_format, Tag.PIPELINE_MODE: int(mode), Tag.OBJECT_BYTES: len(data),
            Tag.OBJECT_CRC32: zlib.crc32(data)}


class LoopbackLab:
    """One connection, one frame, bounded command cache; no import/startup side effects."""

    def __init__(self, faults: LabFaults | None = None):
        self.faults = faults or LabFaults()
        self.stop = Event()
        self.thread = self.listener = None
        self.endpoint = None
        self.error = None
        self.run_count = 0
        self.boot_id = secrets.randbits(64) or 1

    def __enter__(self):
        if self.thread is not None:
            raise RuntimeError("Laboratory instances are single use")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(0.05)
            self.endpoint = listener.getsockname()
            self.listener = listener
            self.thread = Thread(target=self._serve, name="AHFP-loopback-lab", daemon=False)
            self.thread.start()
        except BaseException:
            listener.close()
            raise
        return self

    def __exit__(self, *_args):
        self.stop.set()
        # Called by the CLI / GUI worker, never by the GUI thread.
        self.thread.join(timeout=1)
        if self.thread.is_alive():
            raise RuntimeError("Software laboratory did not stop within its cleanup deadline")

    def _serve(self):
        try:
            with self.listener:
                while not self.stop.is_set():
                    try:
                        connection, _peer = self.listener.accept()
                        break
                    except socket.timeout:
                        continue
                else:
                    return
                with TCPChannel(connection, self.stop, monotonic() + 35) as channel:
                    self._conversation(channel)
        except (TransferCancelled, ConnectionError, OSError):
            pass  # Expected on local client cancellation / fault disconnect.
        except Exception as exc:
            self.error = exc

    def _conversation(self, channel):
        session = None
        assembler = committed = result = None
        frame = transfer = mode = None
        cache = {}
        while True:
            packet = channel.receive()
            if packet.flags or packet.session_id != (session or packet.session_id):
                raise ProtocolError("Wrong request session/flags", Status.STALE_SESSION)
            if packet.kind == Message.FRAME_DATA:
                if assembler is None or committed is not None:
                    raise ProtocolError("DATA requires an active uncommitted BEGIN")
                assembler.add(packet)
                continue
            if not packet.command_id or packet.kind not in {
                    Message.HELLO, Message.GET_STATUS, Message.FRAME_BEGIN, Message.FRAME_COMMIT,
                    Message.RUN, Message.GET_RESULT, Message.RELEASE}:
                raise ProtocolError("Unsupported laboratory command", Status.UNSUPPORTED)
            key = packet.command_id
            if key in cache:
                old, reply = cache[key]
                if old != packet:
                    raise ProtocolError("Command ID reused with different contents", Status.CONFLICT)
                if reply is None:
                    raise ProtocolError("Streaming response must not be replayed")
                channel.send(reply)
                continue
            if len(cache) >= 32:
                raise ProtocolError("Command cache exhausted", Status.RESOURCE_LIMIT)
            values = decode_tlvs(packet.payload)
            reply_values = {Tag.REFERENCED_TYPE: int(packet.kind)}
            try:
                if packet.kind == Message.HELLO:
                    if session is not None or packet.frame_id or packet.transfer_id:
                        raise ProtocolError("HELLO must start a new session")
                    requested = {Tag.CLIENT_NONCE: packet.session_id, Tag.MAX_CHUNK_BYTES: 1024,
                                 Tag.WINDOW_CHUNKS: 16, Tag.MAX_INFLIGHT_FRAMES: 1}
                    if any(values.get(tag) != value for tag, value in requested.items()):
                        raise ProtocolError("Unsupported laboratory negotiation")
                    session = packet.session_id
                    reply_values.update(requested)
                    reply_values.update({Tag.DEVICE_BOOT_ID: self.boot_id, Tag.DEVICE_ID: LAB_ID,
                                         Tag.FIRMWARE_VERSION: LAB_VERSION, Tag.CAPABILITIES: LAB_CAPABILITIES,
                                         Tag.MAX_FRAME_BYTES: RGB_BYTES, Tag.RESULT_RETENTION_MS: 30000,
                                         Tag.DEVICE_STATE: 1, Tag.MODEL_TOKEN: 0})
                elif session is None:
                    raise ProtocolError("HELLO is required", Status.NOT_READY)
                elif packet.kind == Message.FRAME_BEGIN:
                    if assembler is not None or packet.frame_id == 0 or packet.transfer_id in (0, 0xffffffff):
                        raise ProtocolError("Only one frame may be active", Status.BUSY)
                    mode = LabMode(values.get(Tag.PIPELINE_MODE))
                    expected = {Tag.WIDTH: 256, Tag.HEIGHT: 256, Tag.ROW_STRIDE: 768, Tag.PIXEL_FORMAT: 1,
                                Tag.OBJECT_BYTES: RGB_BYTES, Tag.RESULT_PIXEL_FORMAT: 2 if mode == LabMode.MASK_FIXTURE else 1,
                                Tag.PREPROCESS_PROFILE_ID: LAB_PREPROCESS_ID}
                    if any(values.get(tag) != value for tag, value in expected.items()) or Tag.OBJECT_CRC32 not in values:
                        raise ProtocolError("Unsupported input declaration")
                    frame, transfer = packet.frame_id, packet.transfer_id
                    assembler = ObjectAssembler(session_id=session, frame_id=frame, transfer_id=transfer,
                                                total_bytes=RGB_BYTES, expected_crc32=values[Tag.OBJECT_CRC32], kind=Message.FRAME_DATA)
                else:
                    if packet.frame_id != frame or packet.transfer_id != transfer:
                        raise ProtocolError("Unknown frame/transfer", Status.UNKNOWN_FRAME)
                    if packet.kind == Message.FRAME_COMMIT:
                        if values != {Tag.OBJECT_BYTES: RGB_BYTES, Tag.OBJECT_CRC32: assembler.expected_crc32}:
                            raise ProtocolError("COMMIT differs from BEGIN", Status.CONFLICT)
                        committed = assembler.finish()
                        reply_values[Tag.OPERATION_STATE] = 1
                    elif packet.kind == Message.RUN:
                        if committed is None:
                            raise ProtocolError("Frame is not committed", Status.NOT_READY)
                        if result is None:
                            if self.stop.wait(self.faults.delay_seconds):
                                raise TransferCancelled()
                            if mode == LabMode.RGB_BYPASS:
                                result = committed
                            elif mode == LabMode.GAUSSIAN:
                                pixels = np.frombuffer(committed, np.uint8).reshape(256, 256, 3)
                                result = cv2.GaussianBlur(pixels, (3, 3), 0, borderType=cv2.BORDER_REFLECT_101).tobytes()
                            else:
                                result = fixture_mask()
                            self.run_count += 1
                        reply_values[Tag.OPERATION_STATE] = 3
                    elif packet.kind == Message.GET_STATUS:
                        reply_values.update({Tag.DEVICE_BOOT_ID: self.boot_id, Tag.DEVICE_STATE: 1,
                                             Tag.OPERATION_STATE: 3 if result is not None else 1 if committed is not None else 0})
                    elif packet.kind == Message.GET_RESULT:
                        if result is None:
                            raise ProtocolError("No completed result", Status.NOT_READY)
                        self._send_result(channel, packet, mode, result)
                        cache[key] = packet, None
                        continue
                    elif packet.kind == Message.RELEASE:
                        if result is None:
                            raise ProtocolError("Cannot release an incomplete result", Status.NOT_READY)
                        reply_values[Tag.OPERATION_STATE] = 6
                reply = Packet(Message.ACK, session, encode_tlvs(reply_values), frame_id=packet.frame_id,
                               command_id=packet.command_id, transfer_id=packet.transfer_id, flags=RESPONSE)
            except (ProtocolError, ValueError) as exc:
                status = exc.status if isinstance(exc, ProtocolError) else Status.UNSUPPORTED
                reply = Packet(Message.NACK, packet.session_id, encode_tlvs({Tag.REFERENCED_TYPE: int(packet.kind)}),
                               frame_id=packet.frame_id, command_id=packet.command_id, transfer_id=packet.transfer_id,
                               flags=RESPONSE | ERROR, status=status)
            cache[key] = packet, reply
            channel.send(reply)
            if packet.kind == Message.RELEASE:
                return

    def _send_result(self, channel, request, mode, result):
        session = request.session_id ^ 1 if self.faults.wrong_session else request.session_id
        session = session or 2
        transfer = request.transfer_id + 1
        pixel_format = 2 if mode == LabMode.MASK_FIXTURE else 1
        declaration = _geometry(3 if self.faults.wrong_format else pixel_format, mode, result)
        declaration[Tag.PREPROCESS_PROFILE_ID] = LAB_PREPROCESS_ID
        channel.send(Packet(Message.RESULT_BEGIN, session, encode_tlvs(declaration), frame_id=request.frame_id,
                            command_id=request.command_id, transfer_id=transfer, flags=RESPONSE))
        outgoing = result[:-1] + bytes([result[-1] ^ 1]) if self.faults.corrupt_result else result
        packets = list(object_packets(Message.RESULT_DATA, outgoing, session_id=session, frame_id=request.frame_id,
                                      transfer_id=transfer, flags=RESPONSE))
        if self.faults.reorder_and_duplicate:
            packets.reverse()
            packets.insert(0, packets[0])
        for index, packet in enumerate(packets):
            if self.faults.disconnect_result and index == 1:
                channel.connection.shutdown(socket.SHUT_RDWR)
                return
            channel.send(packet)
        channel.send(Packet(Message.RESULT_END, session,
                            encode_tlvs({Tag.OBJECT_BYTES: len(result), Tag.OBJECT_CRC32: zlib.crc32(result)}),
                            frame_id=request.frame_id, command_id=request.command_id, transfer_id=transfer, flags=RESPONSE))


class LabSession:
    """Synchronous one-owner transaction, to be called by a worker or CLI only."""

    def __init__(self, channel: TCPChannel, session_id: int):
        self.channel, self.session_id = channel, session_id
        self.frame_id, self.transfer_id = 1, 1
        self._command_id = 0

    def command(self, kind, values=None, *, frame=True):
        self._command_id += 1
        request = Packet(kind, self.session_id, encode_tlvs(values or {}),
                         frame_id=self.frame_id if frame else 0, command_id=self._command_id,
                         transfer_id=self.transfer_id if frame else 0)
        self.channel.send(request)
        response = self.channel.receive()
        self._identity(response, request.frame_id, request.command_id, request.transfer_id)
        values = decode_tlvs(response.payload, (Tag.REFERENCED_TYPE,))
        if values[Tag.REFERENCED_TYPE] != kind or response.kind not in (Message.ACK, Message.NACK):
            raise ProtocolError("Unexpected command response")
        if response.kind == Message.NACK or response.status != Status.OK or response.flags != RESPONSE:
            raise ProtocolError(f"Laboratory rejected {kind.name}: {response.status.name}", response.status)
        return values

    def _identity(self, packet, frame, command, transfer):
        if (packet.session_id, packet.frame_id, packet.command_id, packet.transfer_id) != (
                self.session_id, frame, command, transfer):
            raise ProtocolError("Response identity mismatch", Status.STALE_SESSION)

    def run(self, request: LabRequest):
        started = perf_counter()
        hello = self.command(Message.HELLO, {
            Tag.CLIENT_NONCE: self.session_id, Tag.CAPABILITIES: LAB_CAPABILITIES,
            Tag.MAX_CHUNK_BYTES: 1024, Tag.WINDOW_CHUNKS: 16, Tag.MAX_INFLIGHT_FRAMES: 1}, frame=False)
        expected = {Tag.CLIENT_NONCE: self.session_id, Tag.DEVICE_ID: LAB_ID, Tag.FIRMWARE_VERSION: LAB_VERSION,
                    Tag.MAX_CHUNK_BYTES: 1024, Tag.WINDOW_CHUNKS: 16, Tag.MAX_INFLIGHT_FRAMES: 1,
                    Tag.CAPABILITIES: LAB_CAPABILITIES, Tag.MAX_FRAME_BYTES: RGB_BYTES, Tag.MODEL_TOKEN: 0,
                    Tag.DEVICE_STATE: 1, Tag.RESULT_RETENTION_MS: 30000}
        if any(hello.get(tag) != value for tag, value in expected.items()) or not hello.get(Tag.DEVICE_BOOT_ID):
            raise ProtocolError("Not the expected software laboratory; real devices are not supported")
        handshake_ms = (perf_counter() - started) * 1000
        declaration = _geometry(1, request.mode, request.rgb)
        declaration.update({Tag.RESULT_PIXEL_FORMAT: 2 if request.mode == LabMode.MASK_FIXTURE else 1,
                            Tag.PREPROCESS_PROFILE_ID: LAB_PREPROCESS_ID})
        phase = perf_counter()
        self.command(Message.FRAME_BEGIN, declaration)
        for packet in object_packets(Message.FRAME_DATA, request.rgb, session_id=self.session_id,
                                     frame_id=self.frame_id, transfer_id=self.transfer_id):
            self.channel.send(packet)
        uploaded = self.command(Message.FRAME_COMMIT, {Tag.OBJECT_BYTES: RGB_BYTES, Tag.OBJECT_CRC32: zlib.crc32(request.rgb)})
        if uploaded.get(Tag.OPERATION_STATE) != 1:
            raise ProtocolError("Upload was not confirmed")
        upload_ms = (perf_counter() - phase) * 1000
        phase = perf_counter()
        run = self.command(Message.RUN)
        if run.get(Tag.OPERATION_STATE) != 3:
            raise ProtocolError("Laboratory did not confirm completion")
        run_wait_ms = (perf_counter() - phase) * 1000
        phase = perf_counter()
        returned = self._receive_result(request)
        receive_ms = (perf_counter() - phase) * 1000
        if request.mode == LabMode.RGB_BYPASS and returned != request.rgb:
            raise ProtocolError("RGB bypass differs from transmitted bytes", Status.BAD_CRC)
        if request.mode == LabMode.MASK_FIXTURE and returned != fixture_mask():
            raise ProtocolError("Mask fixture differs from the fixed reference")
        released = self.command(Message.RELEASE)
        if released.get(Tag.OPERATION_STATE) != 6:
            raise ProtocolError("Result release was not confirmed")
        return returned, hello[Tag.DEVICE_BOOT_ID], (
            ("handshake_ms", handshake_ms), ("upload_ms", upload_ms), ("run_wait_ms", run_wait_ms),
            ("receive_ms", receive_ms), ("transaction_ms", (perf_counter() - started) * 1000))

    def _receive_result(self, request):
        self._command_id += 1
        command = self._command_id
        self.channel.send(Packet(Message.GET_RESULT, self.session_id, frame_id=self.frame_id,
                                 command_id=command, transfer_id=self.transfer_id))
        begin = self.channel.receive()
        transfer = self.transfer_id + 1
        self._identity(begin, self.frame_id, command, transfer)
        if begin.kind != Message.RESULT_BEGIN or begin.flags != RESPONSE:
            raise ProtocolError("Expected RESULT_BEGIN")
        required = (Tag.WIDTH, Tag.HEIGHT, Tag.ROW_STRIDE, Tag.PIXEL_FORMAT, Tag.PIPELINE_MODE,
                    Tag.OBJECT_BYTES, Tag.OBJECT_CRC32, Tag.PREPROCESS_PROFILE_ID)
        values = decode_tlvs(begin.payload, required)
        mask = request.mode == LabMode.MASK_FIXTURE
        expected = {Tag.WIDTH: 256, Tag.HEIGHT: 256, Tag.ROW_STRIDE: 256 if mask else 768,
                    Tag.PIXEL_FORMAT: 2 if mask else 1, Tag.PIPELINE_MODE: int(request.mode),
                    Tag.OBJECT_BYTES: MASK_BYTES if mask else RGB_BYTES, Tag.PREPROCESS_PROFILE_ID: LAB_PREPROCESS_ID}
        if any(values[tag] != value for tag, value in expected.items()):
            raise ProtocolError("Result declaration differs from requested format/geometry")
        assembler = ObjectAssembler(session_id=self.session_id, frame_id=self.frame_id, transfer_id=transfer,
                                    total_bytes=values[Tag.OBJECT_BYTES], expected_crc32=values[Tag.OBJECT_CRC32],
                                    kind=Message.RESULT_DATA)
        # Bounded even when a faulty peer streams endless duplicates.
        for _index in range(assembler.count * 2 + 1):
            packet = self.channel.receive()
            if packet.kind == Message.RESULT_END:
                self._identity(packet, self.frame_id, command, transfer)
                if packet.flags != RESPONSE or decode_tlvs(packet.payload) != {
                        Tag.OBJECT_BYTES: assembler.total_bytes, Tag.OBJECT_CRC32: assembler.expected_crc32}:
                    raise ProtocolError("Result END differs from BEGIN")
                return assembler.finish()
            if packet.flags != RESPONSE:
                raise ProtocolError("Result data is not a successful response")
            assembler.add(packet)
        raise ProtocolError("Excessive duplicate result chunks", Status.RESOURCE_LIMIT)


def run_loopback(request: LabRequest | None = None, cancel: Event | None = None, *, faults: LabFaults | None = None) -> LabResult:
    """Creates and closes an ephemeral 127.0.0.1-only lab. Never reads device profiles."""
    request = request if request is not None else LabRequest(diagnostic_pattern())
    if not isinstance(request, LabRequest):
        raise ValueError("Expected an immutable laboratory request")
    cancel = cancel if cancel is not None else Event()
    if cancel.is_set():
        raise TransferCancelled("本机通信测试已取消")
    started = perf_counter()
    session = secrets.randbits(64) or 1
    with LoopbackLab(faults) as lab:
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout(0.1)
        try:
            connection.connect(lab.endpoint)
            with TCPChannel(connection, cancel, monotonic() + request.timeout_seconds) as channel:
                returned, boot_id, timings = LabSession(channel, session).run(request)
                sent, received = channel.sent_bytes, channel.received_bytes
        finally:
            connection.close()
    return LabResult(request, returned, session, boot_id, 1, 1, 2, lab.endpoint, sent, received,
                     timings + (("lab_total_ms", (perf_counter() - started) * 1000),))
