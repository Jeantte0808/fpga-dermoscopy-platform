"""Member 1's 2026-10-20 RGB UDP protocol, independent of the AHFP draft."""

from enum import Enum
import struct
import zlib

import numpy as np


HEADER = struct.Struct("!4sIIIIHHII")
TOTAL = 256 * 256 * 3
CHUNK = 1024
COUNT = 192
PROFILE = "member1-rgb-udp-20261020"
ERROR_CODES = {1: "字段或范围错误", 2: "长度错误", 3: "帧、模式或来源不符", 4: "顺序错误",
               5: "命令错误", 6: "块 CRC 错误", 7: "输入整帧 CRC 错误", 8: "内部 RGB 计数或标记异常"}


class RGBMode(str, Enum):
    BYPASS = "bypass"
    GAUSSIAN = "gaussian"

    @property
    def suffix(self):
        return b"0" if self == RGBMode.BYPASS else b"1"


class RGBProtocolError(ValueError):
    pass


def header(magic: bytes, frame_id: int, sequence: int, chunk_crc: int, frame_crc: int) -> bytes:
    if (magic not in {b"INP0", b"INP1", b"ACK0", b"ACK1", b"GET0", b"GET1", b"OUT0", b"OUT1", b"BUSY", b"ERR!"}
            or type(frame_id) is not int or not 0 <= frame_id <= 0xffffffff
            or type(sequence) is not int or not 0 <= sequence < COUNT
            or any(type(crc) is not int or not 0 <= crc <= 0xffffffff for crc in (chunk_crc, frame_crc))):
        raise RGBProtocolError("Invalid RGB header arguments")
    return HEADER.pack(magic, frame_id, TOTAL, sequence * CHUNK, sequence, CHUNK, COUNT, chunk_crc, frame_crc)


def response_fields(data: bytes, magic: bytes, frame_id: int, sequence: int):
    """Call after source and stale frame/sequence filtering, including for ERR!."""
    if len(data) < HEADER.size:
        raise RGBProtocolError("RGB response shorter than 32-byte header")
    fields = HEADER.unpack_from(data)
    if fields[0] == b"ERR!":
        if len(data) != HEADER.size:
            raise RGBProtocolError("ERR! must not contain a data body")
        raise RGBProtocolError(f"FPGA error {fields[-1]}: {ERROR_CODES.get(fields[-1], '未知错误码')}")
    if fields[:7] != (magic, frame_id, TOTAL, sequence * CHUNK, sequence, CHUNK, COUNT):
        raise RGBProtocolError(f"RGB response header mismatch at sequence {sequence}")
    expected_size = HEADER.size + (CHUNK if magic[:3] == b"OUT" else 0)
    if len(data) != expected_size:
        raise RGBProtocolError("RGB response datagram length mismatch")
    return fields


def gaussian_rgb(raw: bytes) -> bytes:
    """Exact delivered reference: replicate edges, [1,2,1] outer product, floor /16."""
    if type(raw) is not bytes or len(raw) != TOTAL:
        raise ValueError("Expected 196608 immutable RGB888 bytes")
    image = np.frombuffer(raw, np.uint8).reshape(256, 256, 3).astype(np.uint16)
    padded = np.pad(image, ((1, 1), (1, 1), (0, 0)), mode="edge")
    total = np.zeros_like(image)
    for y, wy in enumerate((1, 2, 1)):
        for x, wx in enumerate((1, 2, 1)):
            total += padded[y:y + 256, x:x + 256] * (wy * wx)
    return (total // 16).astype(np.uint8).tobytes()
