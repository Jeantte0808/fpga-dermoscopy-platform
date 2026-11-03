"""Bounded codec for the explicitly labelled AHFP v1 software laboratory."""

from dataclasses import dataclass
from enum import IntEnum
import struct
import zlib


HEADER = struct.Struct("!4sBBBBHHQIIIIIIIIHHII")
HEADER_BYTES = 64
CHUNK_BYTES = 1024
MAX_OBJECT_BYTES = 64 * 1024 * 1024
RESPONSE = 1
ERROR = 2


class Message(IntEnum):
    HELLO = 1
    GET_STATUS = 2
    MODEL_BEGIN = 16
    MODEL_DATA = 17
    MODEL_COMMIT = 18
    FRAME_BEGIN = 32
    FRAME_DATA = 33
    FRAME_COMMIT = 34
    RUN = 35
    GET_RESULT = 36
    RESULT_BEGIN = 48
    RESULT_DATA = 49
    RESULT_END = 50
    ACK = 64
    NACK = 65
    CANCEL = 80
    RELEASE = 81


class Status(IntEnum):
    OK = 0
    BUSY = 1
    NOT_READY = 2
    BAD_VERSION = 3
    BAD_LENGTH = 4
    BAD_CRC = 5
    UNKNOWN_FRAME = 6
    MODEL_MISMATCH = 7
    UNSUPPORTED = 8
    STALE_SESSION = 9
    RESOURCE_LIMIT = 10
    CANCELLED = 11
    RESULT_EXPIRED = 12
    CONFLICT = 13
    MISSING_DATA = 14


class Tag(IntEnum):
    CLIENT_NONCE = 1
    DEVICE_BOOT_ID = 2
    DEVICE_ID = 3
    FIRMWARE_VERSION = 4
    CAPABILITIES = 5
    MAX_CHUNK_BYTES = 6
    WINDOW_CHUNKS = 7
    MAX_INFLIGHT_FRAMES = 8
    MODEL_TOKEN = 9
    MODEL_VERSION = 10
    MODEL_SHA256 = 11
    PREPROCESS_PROFILE_ID = 12
    WIDTH = 13
    HEIGHT = 14
    ROW_STRIDE = 15
    PIXEL_FORMAT = 16
    PIPELINE_MODE = 17
    OBJECT_BYTES = 18
    OBJECT_CRC32 = 19
    ACK_CHUNK_BASE = 20
    ACK_BITMAP = 21
    DEVICE_STATE = 22
    DEVICE_COMPUTE_CYCLES = 23
    CLOCK_HZ = 24
    REFERENCED_TYPE = 25
    ERROR_DETAIL = 26
    PREPROCESS_SHA256 = 27
    VERIFICATION_KIND = 28
    QUANTIZATION_PROFILE_ID = 29
    RESULT_RETENTION_MS = 30
    MAX_MODEL_BYTES = 31
    MAX_FRAME_BYTES = 32
    RESULT_PIXEL_FORMAT = 33
    OPERATION_STATE = 34


INTEGER_WIDTHS = {
    Tag.CLIENT_NONCE: 8, Tag.DEVICE_BOOT_ID: 8, Tag.CAPABILITIES: 4,
    Tag.MAX_CHUNK_BYTES: 2, Tag.WINDOW_CHUNKS: 2, Tag.MAX_INFLIGHT_FRAMES: 2,
    Tag.MODEL_TOKEN: 4, Tag.PREPROCESS_PROFILE_ID: 4, Tag.WIDTH: 2, Tag.HEIGHT: 2,
    Tag.ROW_STRIDE: 4, Tag.PIXEL_FORMAT: 1, Tag.PIPELINE_MODE: 1, Tag.OBJECT_BYTES: 4,
    Tag.OBJECT_CRC32: 4, Tag.ACK_CHUNK_BASE: 4, Tag.ACK_BITMAP: 4, Tag.DEVICE_STATE: 1,
    Tag.DEVICE_COMPUTE_CYCLES: 8, Tag.CLOCK_HZ: 8, Tag.REFERENCED_TYPE: 1,
    Tag.VERIFICATION_KIND: 1, Tag.QUANTIZATION_PROFILE_ID: 4, Tag.RESULT_RETENTION_MS: 4,
    Tag.MAX_MODEL_BYTES: 4, Tag.MAX_FRAME_BYTES: 4, Tag.RESULT_PIXEL_FORMAT: 1, Tag.OPERATION_STATE: 1,
}
TEXT_LIMITS = {Tag.DEVICE_ID: 32, Tag.FIRMWARE_VERSION: 64, Tag.MODEL_VERSION: 64, Tag.ERROR_DETAIL: 128}
DIGEST_TAGS = {Tag.MODEL_SHA256, Tag.PREPROCESS_SHA256}
DATA_MESSAGES = {Message.MODEL_DATA, Message.FRAME_DATA, Message.RESULT_DATA}


class ProtocolError(ValueError):
    def __init__(self, message, status=Status.BAD_LENGTH):
        super().__init__(message)
        self.status = status


def _uint(value, bits, name):
    if type(value) is not int or not 0 <= value < 1 << bits:
        raise ProtocolError(f"Invalid {name}")


@dataclass(frozen=True)
class Packet:
    kind: Message
    session_id: int
    payload: bytes = b""
    frame_id: int = 0
    command_id: int = 0
    transfer_id: int = 0
    chunk_index: int = 0
    chunk_count: int = 1
    byte_offset: int = 0
    total_bytes: int | None = None
    flags: int = 0
    status: Status = Status.OK

    def __post_init__(self):
        try:
            object.__setattr__(self, "kind", Message(self.kind))
            object.__setattr__(self, "status", Status(self.status))
        except (ValueError, TypeError) as exc:
            raise ProtocolError("Unknown message/status", Status.UNSUPPORTED) from exc
        if type(self.payload) is not bytes:
            raise ProtocolError("Payload must be immutable bytes")
        if self.total_bytes is None:
            object.__setattr__(self, "total_bytes", len(self.payload))

    def validate(self, chunk_bytes=CHUNK_BYTES):
        if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= CHUNK_BYTES:
            raise ProtocolError("Invalid laboratory chunk size")
        _uint(self.session_id, 64, "session_id")
        if self.session_id == 0:
            raise ProtocolError("Session ID must be nonzero")
        for name in ("frame_id", "command_id", "transfer_id", "chunk_index", "chunk_count", "byte_offset", "total_bytes"):
            _uint(getattr(self, name), 32, name)
        _uint(self.flags, 8, "flags")
        if self.flags & ~3 or bool(self.flags & ERROR) != (self.status != Status.OK):
            raise ProtocolError("Invalid response/error flags")
        if self.status != Status.OK and not self.flags & RESPONSE:
            raise ProtocolError("Error must be a response")
        if len(self.payload) > chunk_bytes:
            raise ProtocolError("Payload exceeds negotiated bound")
        if self.kind in DATA_MESSAGES:
            if (not 0 < self.total_bytes <= MAX_OBJECT_BYTES or self.transfer_id in (0, 0xffffffff)
                    or self.command_id != 0 or not 0 <= self.chunk_index < self.chunk_count
                    or self.chunk_count != (self.total_bytes + chunk_bytes - 1) // chunk_bytes
                    or self.byte_offset != self.chunk_index * chunk_bytes
                    or len(self.payload) != min(chunk_bytes, self.total_bytes - self.byte_offset)):
                raise ProtocolError("Invalid data chunk geometry")
        elif (self.chunk_count != 1 or self.chunk_index != 0 or self.byte_offset != 0
              or self.total_bytes != len(self.payload)):
            raise ProtocolError("Invalid control packet geometry")

    def encode(self, chunk_bytes=CHUNK_BYTES):
        self.validate(chunk_bytes)
        values = [b"AHFP", 1, 0, self.kind, self.flags, HEADER_BYTES, len(self.payload), self.session_id,
                  self.frame_id, self.command_id, self.transfer_id, self.chunk_index, self.chunk_count,
                  self.byte_offset, self.total_bytes, zlib.crc32(self.payload), self.status, 0, 0, 0]
        values[18] = zlib.crc32(HEADER.pack(*values))
        return HEADER.pack(*values) + self.payload


def header_payload_length(header: bytes, chunk_bytes=CHUNK_BYTES) -> int:
    """Validate before using the declared length or buffering any body."""
    if len(header) != HEADER_BYTES:
        raise ProtocolError("Incomplete fixed header")
    fields = HEADER.unpack(header)
    if fields[0] != b"AHFP" or fields[1:3] != (1, 0) or fields[5] != HEADER_BYTES:
        raise ProtocolError("Wrong magic/header/version", Status.BAD_VERSION)
    if fields[17] != 0 or fields[19] != 0 or fields[4] & ~3:
        raise ProtocolError("Unknown flags/reserved fields")
    if zlib.crc32(header[:56] + bytes(4) + header[60:]) != fields[18]:
        raise ProtocolError("Header CRC mismatch", Status.BAD_CRC)
    if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= CHUNK_BYTES or fields[6] > chunk_bytes:
        raise ProtocolError("Declared payload exceeds bound")
    if fields[14] > MAX_OBJECT_BYTES:
        raise ProtocolError("Declared object exceeds bound", Status.RESOURCE_LIMIT)
    return fields[6]


def decode_packet(wire: bytes, chunk_bytes=CHUNK_BYTES) -> Packet:
    if not isinstance(wire, bytes) or len(wire) < HEADER_BYTES:
        raise ProtocolError("Truncated packet")
    length = header_payload_length(wire[:HEADER_BYTES], chunk_bytes)
    if len(wire) != HEADER_BYTES + length:
        raise ProtocolError("Truncated packet or trailing bytes")
    fields, payload = HEADER.unpack(wire[:HEADER_BYTES]), wire[HEADER_BYTES:]
    if zlib.crc32(payload) != fields[15]:
        raise ProtocolError("Payload CRC mismatch", Status.BAD_CRC)
    packet = Packet(fields[3], fields[7], payload, *fields[8:15], flags=fields[4], status=fields[16])
    packet.validate(chunk_bytes)
    return packet


class StreamDecoder:
    """Incremental stream parser. Any malformed header poisons this decoder."""
    def __init__(self, chunk_bytes=CHUNK_BYTES):
        self.chunk_bytes = chunk_bytes
        self.buffer = bytearray()
        self.failed = False

    def feed(self, data: bytes) -> list[Packet]:
        if self.failed:
            raise ProtocolError("Decoder failed; rebuild the session")
        # Feed in bounded slices so one caller cannot force an unbounded buffer.
        packets = []
        try:
            for start in range(0, len(data), HEADER_BYTES + self.chunk_bytes):
                self.buffer.extend(data[start:start + HEADER_BYTES + self.chunk_bytes])
                while len(self.buffer) >= HEADER_BYTES:
                    size = HEADER_BYTES + header_payload_length(bytes(self.buffer[:HEADER_BYTES]), self.chunk_bytes)
                    if len(self.buffer) < size:
                        break
                    packets.append(decode_packet(bytes(self.buffer[:size]), self.chunk_bytes))
                    del self.buffer[:size]
        except ProtocolError:
            self.failed = True
            self.buffer.clear()
            raise
        return packets

    def finish(self):
        if self.buffer:
            self.failed = True
            self.buffer.clear()
            raise ProtocolError("EOF in an incomplete packet")


def _validate_tlv(tag, data):
    if tag in INTEGER_WIDTHS and len(data) != INTEGER_WIDTHS[tag]:
        raise ProtocolError(f"Wrong integer width: {tag.name}")
    if tag in DIGEST_TAGS and len(data) != 32:
        raise ProtocolError("Wrong SHA-256 width")
    if tag in TEXT_LIMITS:
        if not 1 <= len(data) <= TEXT_LIMITS[tag] or any(byte < 32 or byte > 126 for byte in data):
            raise ProtocolError("Invalid ASCII identifier")


def encode_tlvs(values: dict) -> bytes:
    result = bytearray()
    for tag, value in sorted(values.items()):
        try:
            tag = Tag(tag)
        except ValueError as exc:
            raise ProtocolError("Unknown TLV tag") from exc
        if tag in INTEGER_WIDTHS:
            if type(value) is not int or not 0 <= value < 1 << (INTEGER_WIDTHS[tag] * 8):
                raise ProtocolError("Invalid TLV integer")
            data = value.to_bytes(INTEGER_WIDTHS[tag], "big")
        elif isinstance(value, str):
            try:
                data = value.encode("ascii")
            except UnicodeEncodeError as exc:
                raise ProtocolError("TLV identifier must be ASCII") from exc
        elif type(value) is bytes:
            data = value
        else:
            raise ProtocolError("Invalid TLV value")
        _validate_tlv(tag, data)
        result.extend(struct.pack("!HH", tag, len(data)) + data)
    if len(result) > CHUNK_BYTES:
        raise ProtocolError("Control TLVs exceed limit")
    return bytes(result)


def decode_tlvs(payload: bytes, required=()) -> dict:
    if len(payload) > CHUNK_BYTES:
        raise ProtocolError("Control payload exceeds limit")
    values, seen, offset = {}, set(), 0
    while offset < len(payload):
        if len(payload) - offset < 4:
            raise ProtocolError("Truncated TLV header")
        raw_tag, length = struct.unpack_from("!HH", payload, offset)
        offset += 4
        if raw_tag in seen or length > len(payload) - offset:
            raise ProtocolError("Duplicate or truncated TLV")
        seen.add(raw_tag)
        data, offset = payload[offset:offset + length], offset + length
        try:
            tag = Tag(raw_tag)
        except ValueError:
            if raw_tag & 0x8000:
                raise ProtocolError("Unknown required extension", Status.UNSUPPORTED)
            continue
        _validate_tlv(tag, data)
        values[tag] = int.from_bytes(data, "big") if tag in INTEGER_WIDTHS else data.decode("ascii") if tag in TEXT_LIMITS else data
    if not set(required) <= values.keys():
        raise ProtocolError("Required TLV is missing")
    return values
