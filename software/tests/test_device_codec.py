from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import struct
import subprocess
import sys
import zlib

import numpy as np
import pytest

from dermademo.device.codec import (CHUNK_BYTES, ERROR, RESPONSE, HEADER, Message, Packet,
    ProtocolError, Status, StreamDecoder, Tag, decode_packet, decode_tlvs, encode_tlvs, header_payload_length)
from dermademo.device.assembly import ObjectAssembler, object_packets
from dermademo.device.imaging import diagnostic_pattern, prepare_lab_input, restore_binary_mask


ROOT = Path(__file__).resolve().parents[1]


def test_codec_matches_all_previously_committed_design_vectors():
    vectors = json.loads((ROOT / "examples/fpga_protocol.draft_vectors.json").read_text(encoding="utf-8"))
    for item in vectors["vectors"]:
        payload = bytes.fromhex(item["payload_hex"]) if "payload_hex" in item else bytes(index % 256 for index in range(item["payload_generation"]["length"]))
        wire = bytes.fromhex(item["header_hex"]) + payload
        packet = decode_packet(wire)
        assert packet.encode() == wire
        assert sha256(wire).hexdigest() == item["wire_packet_sha256"]
        assert packet.session_id == int(item["expected_header"]["session_id"], 0)
        assert packet.total_bytes == item["expected_header"]["total_bytes"]


def test_stream_parser_handles_every_split_boundary_and_many_coalesced_messages():
    packets = [Packet(Message.GET_STATUS, 7, command_id=index) for index in range(1, 5)]
    encoded = b"".join(packet.encode() for packet in packets)
    for split in range(len(encoded)+1):
        stream = StreamDecoder()
        assert stream.feed(encoded[:split]) + stream.feed(encoded[split:]) == packets
        stream.finish()
    stream = StreamDecoder()
    received = []
    for byte in encoded:
        received += stream.feed(bytes([byte]))
        assert len(stream.buffer) <= 1088
    assert received == packets


@pytest.mark.parametrize("offset", [0, 4, 20, 56, 60, 64, 67])
def test_corrupt_header_or_payload_fails_and_poisoned_stream_never_resynchronizes(offset):
    packet = Packet(Message.HELLO, 1, b"abcd", command_id=1)
    wire = bytearray(packet.encode())
    wire[offset] ^= 1
    with pytest.raises(ProtocolError):
        decode_packet(bytes(wire))
    stream = StreamDecoder()
    with pytest.raises(ProtocolError):
        stream.feed(bytes(wire))
    assert not stream.buffer
    with pytest.raises(ProtocolError):
        stream.feed(packet.encode())


def test_eof_and_invalid_declared_size_are_rejected_before_body_buffering():
    packet = Packet(Message.GET_STATUS, 1, command_id=1).encode()
    stream = StreamDecoder()
    stream.feed(packet[:30])
    with pytest.raises(ProtocolError, match="EOF"):
        stream.finish()
    fields = list(HEADER.unpack(packet))
    fields[6], fields[18] = 60000, 0
    fields[18] = zlib.crc32(HEADER.pack(*fields))
    with pytest.raises(ProtocolError, match="bound"):
        header_payload_length(HEADER.pack(*fields))
    with pytest.raises(ProtocolError):
        decode_packet(packet + b"trailing")


@pytest.mark.parametrize("change", [{"session_id": 0}, {"frame_id": True}, {"command_id": -1},
    {"flags": 4}, {"status": Status.BUSY}, {"payload": b"x"*1025, "total_bytes": 1025},
    {"chunk_count": 0}, {"byte_offset": 1}, {"total_bytes": 1}])
def test_invalid_outgoing_packets_are_rejected(change):
    with pytest.raises(ProtocolError):
        replace(Packet(Message.GET_STATUS, 1, command_id=1), **change).encode()


def test_error_response_requires_error_flag_and_round_trips():
    packet = Packet(Message.NACK, 1, encode_tlvs({Tag.REFERENCED_TYPE: int(Message.RUN)}),
                    frame_id=3, command_id=4, status=Status.BUSY, flags=RESPONSE|ERROR)
    assert decode_packet(packet.encode()) == packet


def test_tlv_types_unknown_extensions_and_required_fields():
    values = {Tag.CLIENT_NONCE: 0x1122334455667788, Tag.DEVICE_ID: "software-lab", Tag.MODEL_SHA256: bytes(range(32)), Tag.WIDTH: 256}
    encoded = encode_tlvs(values)
    assert decode_tlvs(encoded, required=(Tag.DEVICE_ID,)) == values
    assert decode_tlvs(encoded + struct.pack("!HH", 0x7000, 2) + b"ok") == values
    with pytest.raises(ProtocolError, match="required extension"):
        decode_tlvs(encoded + struct.pack("!HH", 0x8001, 0))
    for bad in (encoded[:3], encoded[:-1], encoded + encoded,
                struct.pack("!HH", Tag.WIDTH, 1) + b"x"):
        with pytest.raises(ProtocolError):
            decode_tlvs(bad)
    with pytest.raises(ProtocolError, match="missing"):
        decode_tlvs(b"", required=(Tag.WIDTH,))


@pytest.mark.parametrize("values", [{Tag.WIDTH: True}, {Tag.WIDTH: -1}, {Tag.WIDTH: 65536},
    {Tag.DEVICE_ID: "\x00bad"}, {Tag.DEVICE_ID: "中文"}, {Tag.DEVICE_ID: "x"*33}, {Tag.MODEL_SHA256: b"short"}])
def test_tlv_writer_rejects_invalid_values(values):
    with pytest.raises(ProtocolError):
        encode_tlvs(values)


def test_object_assembler_accepts_reordered_chunks_once_and_checks_whole_crc():
    data = bytes(range(251)) * 10
    packets = list(object_packets(Message.RESULT_DATA, data, session_id=5, frame_id=7, transfer_id=0x80000001, flags=RESPONSE))
    obj = ObjectAssembler(session_id=5, frame_id=7, transfer_id=0x80000001,
                          total_bytes=len(data), expected_crc32=zlib.crc32(data), kind=Message.RESULT_DATA)
    with pytest.raises(ProtocolError) as error:
        obj.finish()
    assert error.value.status == Status.MISSING_DATA
    assert obj.add(packets[1])
    assert not obj.add(packets[1])
    assert obj.received_count == 1 and obj.ack_bitmap(0) == 2
    for packet in (packets[2], packets[0]):
        obj.add(packet)
    assert obj.complete and obj.finish() == data and obj.ack_bitmap(0) == 7
    with pytest.raises(ProtocolError, match="different content"):
        obj.add(replace(packets[1], payload=b"z"*len(packets[1].payload)))
    with pytest.raises(ProtocolError, match="belong"):
        obj.add(replace(packets[0], frame_id=8))


def test_size_and_whole_object_checksum_prevent_false_success():
    with pytest.raises(ProtocolError):
        ObjectAssembler(session_id=1, frame_id=1, transfer_id=1, total_bytes=2**31, expected_crc32=0)
    data = b"small"
    packet = next(object_packets(Message.FRAME_DATA, data, session_id=1, frame_id=1, transfer_id=1))
    obj = ObjectAssembler(session_id=1, frame_id=1, transfer_id=1, total_bytes=len(data), expected_crc32=0)
    obj.add(packet)
    with pytest.raises(ProtocolError, match="Whole object CRC"):
        obj.finish()


@pytest.mark.parametrize("geometry", ["stretch", "letterbox"])
def test_lab_input_preserves_original_and_restores_binary_original_coordinates(geometry):
    image = np.zeros((101, 203, 3), np.uint8)
    image[:] = (30, 80, 170)
    image.setflags(write=False)
    prepared = prepare_lab_input(image, geometry)
    assert prepared.preprocessing_profile.endswith("not-team-reference")
    assert len(prepared.rgb) == 196608 and sha256(prepared.rgb).hexdigest() == prepared.sha256
    rgb = np.frombuffer(prepared.rgb, np.uint8).reshape(256,256,3)
    np.testing.assert_array_equal(rgb[128,128], [170,80,30])
    mask = np.zeros((256,256),np.uint8)
    mask[prepared.top:prepared.top+prepared.resized_height, prepared.left:prepared.left+prepared.resized_width] = 255
    restored = restore_binary_mask(mask.tobytes(), prepared)
    assert restored.shape == (101,203) and (restored == 255).all()
    np.testing.assert_array_equal(image[0,0], [30,80,170])
    with pytest.raises(ValueError):
        restore_binary_mask(bytes([1])*65536, prepared)
    with pytest.raises(ValueError):
        restore_binary_mask(b"short", prepared)


def test_diagnostic_pattern_is_asymmetric_and_core_import_does_not_import_qt():
    image = np.frombuffer(diagnostic_pattern(),np.uint8).reshape(256,256,3)
    np.testing.assert_array_equal(image[17,29], [29,17,(29*3+17*5)%256])
    assert not np.array_equal(image, image[::-1])
    result = subprocess.run([sys.executable,"-c",
        "import dermademo.device.codec, dermademo.device.assembly, dermademo.device.imaging, sys; assert not any(k.startswith('PySide6') for k in sys.modules)"],
        cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
