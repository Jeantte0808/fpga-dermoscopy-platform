"""Stable RGB data contracts shared by import and python -m entry points."""

from dataclasses import dataclass
from ipaddress import IPv4Address
import json
import math

from .imaging import PreparedInput
from .rgb_protocol import TOTAL, RGBMode


@dataclass(frozen=True)
class RGBUDPConfig:
    bind_ip: str = "192.168.0.3"
    local_port: int = 6102
    board_ip: str = "192.168.0.2"
    board_port: int = 5000
    timeout_seconds: float = 1.0
    transaction_seconds: float = 30.0

    def __post_init__(self):
        for name in ("bind_ip", "board_ip"):
            value = getattr(self, name)
            if type(value) is not str:
                raise ValueError("请输入明确的 IPv4 地址")
            address = IPv4Address(value)
            if str(address) != value or address.is_unspecified or address.is_multicast or int(address) == 0xffffffff:
                raise ValueError("IPv4 地址不能为通配、组播或广播地址")
        if (type(self.local_port) is not int or not 0 <= self.local_port <= 65535
                or type(self.board_port) is not int or not 1 <= self.board_port <= 65535):
            raise ValueError("UDP 端口超出范围")
        for value, maximum in ((self.timeout_seconds, 5), (self.transaction_seconds, 300)):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0.01 <= value <= maximum:
                raise ValueError("超时必须为有限正数：单次最多 5 秒，总期限最多 300 秒")


@dataclass(frozen=True)
class RGBRequest:
    rgb: bytes
    mode: RGBMode = RGBMode.BYPASS
    source: str = "external-rgb-buffer"
    prepared: PreparedInput | None = None

    def __post_init__(self):
        if (type(self.rgb) is not bytes or len(self.rgb) != TOTAL or not isinstance(self.mode, RGBMode)
                or type(self.source) is not str
                or self.source not in {"external-rgb-buffer", "handoff-raw", "workbench-snapshot", "raw-file"}):
            raise ValueError("RGB 请求必须为 256×256 RGB888，不支持 Mask 或其他模式")
        if self.prepared is not None and (not isinstance(self.prepared, PreparedInput) or self.prepared.rgb != self.rgb):
            raise ValueError("图像几何快照与发送字节不一致")


@dataclass(frozen=True)
class RGBOutcome:
    request: RGBRequest
    config: RGBUDPConfig
    frame_id: int
    returned: bytes | None
    report_json: str

    @property
    def report(self):
        # Each caller receives a new object; saved evidence cannot be edited by UI options.
        return json.loads(self.report_json)

    @property
    def complete(self):
        return self.report["complete"]
