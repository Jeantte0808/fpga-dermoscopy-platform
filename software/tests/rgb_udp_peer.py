"""Independent localhost-only fixture for the delivered RGB wire format."""

import socket
import struct
from threading import Event, Thread
import zlib


WIRE = struct.Struct("!4sIIIIHHII")


class RGBPeer:
    def __init__(self, fault="none", gaussian=None):
        self.fault, self.gaussian = fault, gaussian
        self.stop, self.got_input = Event(), Event()
        self.errors, self.requests = [], []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]
        self.thread = Thread(target=self._serve, name="test-rgb-udp-peer", daemon=False)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.stop.set()
        self.thread.join(2)
        self.sock.close()
        assert not self.thread.is_alive(), "RGB fixture leaked its server thread"
        assert not self.errors, self.errors

    def _serve(self):
        last_input, last_output, busy, dropped = -1, -1, 0, False
        raw, output, identity = bytearray(196608), None, None
        try:
            while not self.stop.is_set():
                try:
                    request, source = self.sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except ConnectionResetError:
                    # Windows can report an ICMP port-unreachable after a cancelled
                    # client closes. The test server must remain alive until stopped.
                    continue
                assert len(request) >= 32
                fields = WIRE.unpack_from(request)
                magic, fid, total, offset, seq, size, count, crc, frame_crc = fields
                self.requests.append((magic, fid, seq, request))
                assert total == 196608 and size == 1024 and count == 192 and offset == seq * 1024
                assert 0 <= seq < 192
                if self.fault == "timeout_upload":
                    continue
                if magic[:3] == b"INP":
                    self.got_input.set()
                    assert len(request) == 1056 and zlib.crc32(request[32:]) == crc
                    if identity is None:
                        identity = fid, magic[-1:], source, frame_crc
                    assert identity == (fid, magic[-1:], source, frame_crc)
                    assert seq in (last_input, last_input + 1)
                    raw[offset:offset+1024] = request[32:]
                    last_input = max(last_input, seq)
                    if seq == 191:
                        assert zlib.crc32(raw) == frame_crc
                        output = self.gaussian(bytes(raw)) if magic[-1:] == b"1" else bytes(raw)
                        if self.fault == "bad_reference":
                            output = bytes([output[0] ^ 1]) + output[1:]
                    if self.fault == "drop_ack" and seq == 4 and not dropped:
                        dropped = True
                        continue
                    response = b"ACK" + magic[-1:] + request[4:32]
                    if self.fault == "bad_ack":
                        response = response[:-1] + bytes([response[-1] ^ 1])
                    elif self.fault == "short_ack":
                        response = b"ACK0"
                    elif self.fault == "long_ack":
                        response += b"x"
                    elif self.fault == "wrong_mode":
                        response = b"ACK1" + response[4:]
                    elif self.fault == "error":
                        response = WIRE.pack(b"ERR!", fid, total, offset, seq, size, count, crc, 6)
                    elif self.fault == "stale" and seq == 0:
                        self.sock.sendto(WIRE.pack(b"ERR!", fid ^ 1, total, offset, seq, size, count, crc, 7), source)
                        self.sock.sendto(WIRE.pack(b"ERR!", fid, total, offset, 191, size, count, crc, 7), source)
                    elif self.fault == "wrong_source" and seq == 0:
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rogue:
                            rogue.bind(("127.0.0.1", 0))
                            rogue.sendto(b"invalid", source)
                else:
                    assert magic[:3] == b"GET" and len(request) == 32 and crc == 0
                    assert identity == (fid, magic[-1:], source, frame_crc)
                    assert last_input == 191 and seq in (last_output, last_output + 1)
                    if self.fault == "delay" and self.stop.wait(2):
                        return
                    if self.fault == "timeout_get":
                        continue
                    if self.fault == "busy_forever" or self.fault == "busy" and busy < 2:
                        busy += 1
                        self.sock.sendto(WIRE.pack(b"BUSY", fid, total, offset, seq, size, count, 0, frame_crc), source)
                        continue
                    last_output = max(last_output, seq)
                    if self.fault == "drop_out" and seq == 4 and not dropped:
                        dropped = True
                        continue
                    body = output[offset:offset+1024]
                    part_crc = zlib.crc32(body)
                    output_crc = zlib.crc32(output)
                    if self.fault == "bad_out_crc":
                        part_crc ^= 1
                    elif self.fault == "bad_frame_crc" or self.fault == "changed_frame_crc" and seq > 0:
                        output_crc ^= 1
                    response = WIRE.pack(b"OUT" + magic[-1:], fid, total, offset, seq, size, count, part_crc, output_crc) + body
                    if self.fault == "short_output":
                        response = response[:-1]
                self.sock.sendto(response, source)
        except Exception as exc:
            self.errors.append(exc)
