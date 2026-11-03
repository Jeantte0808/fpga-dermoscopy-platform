"""A bounded object assembler created only after a validated BEGIN."""

import zlib

from .codec import CHUNK_BYTES, MAX_OBJECT_BYTES, Packet, ProtocolError, Status, DATA_MESSAGES


class ObjectAssembler:
    def __init__(self, *, session_id, frame_id, transfer_id, total_bytes, expected_crc32,
                 chunk_bytes=CHUNK_BYTES, max_bytes=196608, kind=None):
        if (type(total_bytes) is not int or type(max_bytes) is not int or not 0 < total_bytes <= max_bytes <= MAX_OBJECT_BYTES
                or type(chunk_bytes) is not int or not 1 <= chunk_bytes <= CHUNK_BYTES
                or type(expected_crc32) is not int or not 0 <= expected_crc32 <= 0xffffffff):
            raise ProtocolError("Invalid object declaration", Status.RESOURCE_LIMIT)
        if kind is not None and kind not in DATA_MESSAGES:
            raise ProtocolError("Invalid data message type")
        self.identity = session_id, frame_id, transfer_id
        self.total_bytes, self.expected_crc32, self.chunk_bytes, self.kind = total_bytes, expected_crc32, chunk_bytes, kind
        self.count = (total_bytes + chunk_bytes - 1) // chunk_bytes
        self._buffer = bytearray(total_bytes)
        self._received = bytearray(self.count)
        self.received_count = 0

    @property
    def complete(self):
        return self.received_count == self.count

    def add(self, packet: Packet) -> bool:
        packet.validate(self.chunk_bytes)
        if (packet.kind not in DATA_MESSAGES or self.kind is not None and packet.kind != self.kind
                or (packet.session_id, packet.frame_id, packet.transfer_id) != self.identity
                or packet.total_bytes != self.total_bytes or packet.chunk_count != self.count):
            raise ProtocolError("Chunk does not belong to this object", Status.CONFLICT)
        start, end = packet.byte_offset, packet.byte_offset + len(packet.payload)
        if self._received[packet.chunk_index]:
            if self._buffer[start:end] != packet.payload:
                raise ProtocolError("Duplicate chunk has different content", Status.CONFLICT)
            return False
        self._buffer[start:end] = packet.payload
        self._received[packet.chunk_index] = 1
        self.received_count += 1
        return True

    def ack_bitmap(self, base, width=16):
        if type(width) is not int or not 1 <= width <= 32 or type(base) is not int or base < 0 or base % width:
            raise ProtocolError("Invalid window geometry")
        return sum(1 << index for index in range(width) if base + index < self.count and self._received[base + index])

    def finish(self) -> bytes:
        if not self.complete:
            raise ProtocolError("Object has missing chunks", Status.MISSING_DATA)
        if zlib.crc32(self._buffer) != self.expected_crc32:
            raise ProtocolError("Whole object CRC mismatch", Status.BAD_CRC)
        return bytes(self._buffer)


def object_packets(kind, data: bytes, *, session_id, frame_id, transfer_id, chunk_bytes=CHUNK_BYTES, flags=0):
    if type(data) is not bytes or not data or len(data) > MAX_OBJECT_BYTES:
        raise ProtocolError("Invalid outgoing object")
    if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= CHUNK_BYTES:
        raise ProtocolError("Invalid outgoing chunk size")
    count = (len(data) + chunk_bytes - 1) // chunk_bytes
    for index, start in enumerate(range(0, len(data), chunk_bytes)):
        packet = Packet(kind, session_id, data[start:start + chunk_bytes], frame_id=frame_id,
                        transfer_id=transfer_id, chunk_index=index, chunk_count=count,
                        byte_offset=start, total_bytes=len(data), flags=flags)
        packet.validate(chunk_bytes)
        yield packet
