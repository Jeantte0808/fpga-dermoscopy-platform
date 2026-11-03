"""Independent localhost wire fixture; returns saved Mask bytes, never runs a CNN."""

from hashlib import sha256
import json
import socket
import struct
from threading import Event, Thread
import zlib

from dermademo.resources import resource_path


class FormalPeer:
    def __init__(self, *, busy_once=True, bad_generation=False, bad_mask=False, bad_profile=False, drop_once=False):
        root = resource_path('formal_root')
        self.manifest = json.loads((root / 'assets/manifest.json').read_bytes())
        capture = sorted((root / 'captures').iterdir())[0]
        self.mask = (capture / 'mask.raw').read_bytes()
        self.profile = json.loads((capture / 'profile.json').read_bytes())
        if bad_mask:
            self.mask = bytes([42]) + self.mask[1:]
        self.busy_once, self.bad_generation, self.bad_profile, self.drop_once = busy_once, bad_generation, bad_profile, drop_once
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.settimeout(.05)
        self.port = self.sock.getsockname()[1]
        self.stop = Event()
        self.thread = Thread(target=self.run, daemon=True)
        self.requests, self.errors, self.cache = [], [], {}
        self.generation, self.frame = 1, 0
        self.parameters, self.rgb = bytearray(), bytearray()
        self.loaded, self.nfrag = 2770336, 2931
        self.seen_busy = self.seen_drop = None

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.stop.set()
        self.thread.join(3)
        self.sock.close()
        assert not self.thread.is_alive()
        assert not self.errors, self.errors

    def run(self):
        try:
            while not self.stop.is_set():
                try:
                    packet, address = self.sock.recvfrom(4096)
                except socket.timeout:
                    continue
                assert len(packet) >= 96 and packet[:4] == b'FCP1'
                header, body = bytearray(packet[:96]), packet[96:]
                crc = int.from_bytes(header[72:76], 'big')
                header[72:76] = bytes(4)
                assert zlib.crc32(header) == crc
                op = header[5]
                txn, generation, frame, index, size, pcrc, frame_crc = struct.unpack_from('!IIIIIII', header, 8)
                assert len(body) == size and zlib.crc32(body) == pcrc
                assert header[40:72] == bytes.fromhex(self.manifest['image_id'])
                self.requests.append((op, txn, frame, index, packet))
                if txn in self.cache:
                    old, response = self.cache[txn]
                    assert old == packet, 'retry changed txn payload'
                    self.sock.sendto(response, address)
                    continue
                error, payload, fields, flags = 0, b'', [self.loaded, self.nfrag, len(self.rgb)//1024, self.frame, self.generation], 2
                if self.drop_once and self.seen_drop is None:
                    self.seen_drop = packet
                    continue
                if op == 1:
                    self.generation += 1
                    self.loaded, self.nfrag = 0, 0
                    self.parameters.clear()
                elif op == 2:
                    fragment = self.manifest['fragments'][index]
                    assert size == fragment['bytes'] and pcrc == fragment['crc32'] and self.nfrag == index
                    self.parameters.extend(body)
                    self.loaded += len(body)
                    self.nfrag += 1
                elif op == 3:
                    assert sha256(self.parameters).hexdigest() == self.manifest['payload_sha256']
                elif op == 4:
                    self.frame = frame
                    self.rgb.clear()
                    self.seen_busy = None
                    self.expected_crc = frame_crc
                elif op == 5:
                    assert index * 1024 == len(self.rgb) and len(body) == 1024
                    self.rgb.extend(body)
                    if index == 191:
                        assert zlib.crc32(self.rgb) == self.expected_crc
                elif op == 6:
                    if self.busy_once and self.seen_busy is None:
                        error = 136
                        self.seen_busy = packet
                    else:
                        if self.seen_busy is not None and index == 0:
                            assert packet == self.seen_busy
                        payload = self.mask[index*1024:(index+1)*1024]
                elif op == 8 and index:
                    record = (index >> 4) & 0xfff
                    page = index & 15
                    assert 0 <= record <= 82 and 0 <= page <= 2
                    counters = self.profile['total'] if record == 0 else self.profile['stages'][record-1]
                    keys = [k for k in self.profile['total']]
                    fields = [counters[k] for k in keys[page*5:page*5+5]]
                    if self.bad_profile and record == 0 and page == 0:
                        fields[0] += 1
                if op != 8 or not index:
                    fields = [self.loaded, self.nfrag, len(self.rgb)//1024, self.frame, self.generation]
                reply_frame = self.frame if not (op == 8 and index == 0) else 0
                header = bytearray(96)
                struct.pack_into('!4sBBBBIIIIIIII', header, 0, b'FCR1', 1, op, error, flags, txn,
                                 self.generation + (1 if self.bad_generation and op == 6 else 0),
                                 reply_frame, index, len(payload), zlib.crc32(payload),
                                 1 if op == 8 and index else 0, 82)
                header[40:72] = bytes.fromhex(self.manifest['image_id'])
                struct.pack_into('!IIIII', header, 76, *fields)
                struct.pack_into('!I', header, 72, zlib.crc32(header))
                response = bytes(header) + payload
                if error != 136:
                    self.cache[txn] = packet, response
                self.sock.sendto(response, address)
        except Exception as exc:
            self.errors.append(repr(exc))


def local_config(peer, *, reuse=True):
    from dermademo.device.formal_session import FormalConfig
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    return FormalConfig('127.0.0.1', port, '127.0.0.1', peer.port, .15, 2, reuse)
