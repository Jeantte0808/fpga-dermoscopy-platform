"""Deadline/cancellation aware TCP framing; no device discovery or guessed endpoints."""

from collections import deque
import socket
from threading import Event, get_ident
from time import monotonic

from .codec import Packet, StreamDecoder


class TransferCancelled(Exception):
    pass


class TransferTimeout(TimeoutError):
    pass


class TCPChannel:
    """One calling thread owns all reads, writes and closure of this channel."""

    def __init__(self, connection: socket.socket, cancel: Event, deadline: float):
        self.connection, self.cancel, self.deadline = connection, cancel, deadline
        self._owner = get_ident()
        self._decoder, self._pending = StreamDecoder(), deque()
        self.sent_bytes = self.received_bytes = 0
        connection.settimeout(0.05)

    def check(self):
        if get_ident() != self._owner:
            raise RuntimeError("TCP channel must have a single I/O owner")
        if self.cancel.is_set():
            raise TransferCancelled("本机通信测试已取消；没有连接真实设备")
        if monotonic() >= self.deadline:
            raise TransferTimeout("本机通信测试超时；连接已关闭，不自动重跑")

    def send(self, packet: Packet):
        data = memoryview(packet.encode())
        while data:
            self.check()
            try:
                size = self.connection.send(data)
            except socket.timeout:
                continue
            if size == 0:
                raise ConnectionError("TCP peer closed during send")
            self.sent_bytes += size
            data = data[size:]

    def receive(self) -> Packet:
        while True:
            self.check()
            if self._pending:
                return self._pending.popleft()
            try:
                data = self.connection.recv(4096)
            except socket.timeout:
                continue
            if not data:
                self._decoder.finish()
                raise ConnectionError("TCP peer closed before transaction completed")
            self.received_bytes += len(data)
            self._pending.extend(self._decoder.feed(data))

    def close(self):
        if get_ident() != self._owner:
            raise RuntimeError("Only the I/O owner may close this channel")
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
