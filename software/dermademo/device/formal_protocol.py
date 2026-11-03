"""FCP1/FCR1 V5 adapter. Distinct from the RGB and AHFP protocols."""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import struct
import zlib

from ..resources import resource_path

IMAGE_ID = '9d6ef5d5fee6d9a9ea5b2fd2ba688a3da8a848bde15fc97fdd3615c20867ac89'
PAYLOAD_SHA = 'ba90835f10b9a942c9f542042f5ac12115613bc5870273c8d04db61cb27e410e'
MANIFEST_SHA = '6c5d39c19eed1e0c5f378eef9adfd58ba30c54b3deac3ae4e31e717048fba680'
SEED_FLOOR = 1792958155
CLOCK_HZ = 100_000_000
PAGE_KEYS = ('loaded_bytes', 'next_fragment', 'next_RGB_chunk', 'result_frame', 'result_generation')
COUNTERS = ('cycles', 'AR_bursts', 'R_beats', 'AW_bursts', 'W_beats', 'read_response_wait_cycles',
            'write_data_starve_cycles', 'B_response_wait_cycles', 'AR_backpressure_cycles',
            'AW_backpressure_cycles', 'W_backpressure_cycles', 'R_backpressure_cycles',
            'short_AR_bursts', 'short_AW_bursts', 'no_outstanding_AXI_idle_cycles')
ERRORS = {128:'长度错误',129:'来源不符',130:'magic/version 错误',131:'保留位错误',132:'头 CRC 错误',
          133:'模型 ID 不符',134:'命令形状/范围错误',135:'payload CRC 错误',136:'BUSY',
          137:'事务号落后，请停止其他客户端并合并最新高水位',138:'片段不符',139:'帧号落后',
          141:'结果帧不符',142:'generation 不符'}


class FormalProtocolError(ValueError):
    pass


def need(value, message):
    if not value:
        raise FormalProtocolError(message)


def assets(root=None):
    root = Path(root) if root else resource_path('formal_root') / 'assets'
    encoded = (root / 'manifest.json').read_bytes()
    need(sha256(encoded).hexdigest() == MANIFEST_SHA, '正式模型 manifest 摘要不符')
    manifest = json.loads(encoded)
    blob = (root / 'model.payload').read_bytes()
    need(manifest['image_id'] == IMAGE_ID and len(blob) == 2770336
         and sha256(blob).hexdigest() == manifest['payload_sha256'] == PAYLOAD_SHA
         and len(manifest['fragments']) == 2931, '正式模型资产不符')
    at = 0
    for i, fragment in enumerate(manifest['fragments']):
        chunk = blob[fragment['offset']:fragment['offset'] + fragment['bytes']]
        need(fragment['index'] == i and fragment['offset'] == at and 0 < len(chunk) <= 1024
             and len(chunk) == fragment['bytes'] and len(chunk) % 16 == 0
             and zlib.crc32(chunk) == fragment['crc32'], '模型分片校验失败')
        at += len(chunk)
    need(at == len(blob), '模型分片未完整覆盖')
    sample = (root / 'sample.rgb').read_bytes()
    need(len(sample) == 196608 and sha256(sample).hexdigest() == manifest['sample_RGB_sha256'], '正式样例摘要不符')
    return manifest, blob, sample


def encode(op, txn, generation, body=b'', *, frame=0, index=0, frame_crc=0):
    need(op in range(1, 9) and type(body) is bytes and len(body) <= 1024, '请求类型/长度无效')
    need(all(type(v) is int and 0 <= v <= 0xffffffff for v in
             (txn, generation, frame, index, frame_crc)) and txn > 0, '请求编号无效')
    header = bytearray(96)
    struct.pack_into('!4sBBHIIIIIII', header, 0, b'FCP1', 1, op, 0, txn, generation, frame,
                     index, len(body), zlib.crc32(body), frame_crc)
    header[40:72] = bytes.fromhex(IMAGE_ID)
    struct.pack_into('!I', header, 72, zlib.crc32(header))
    return bytes(header) + body


def decode(packet):
    need(96 <= len(packet) <= 1120, '响应长度错误')
    header, body = bytearray(packet[:96]), packet[96:]
    need(header[:4] == b'FCR1' and header[4] == 1 and 1 <= header[5] <= 8
         and not header[7] & 224, '响应 magic/version/flags 无效')
    need(header[40:72] == bytes.fromhex(IMAGE_ID), '响应模型 image_id 不符')
    crc = int.from_bytes(header[72:76], 'big')
    header[72:76] = bytes(4)
    need(zlib.crc32(header) == crc, '响应头 CRC 不符')
    values = struct.unpack_from('!IIIIIIII', header, 8)
    txn, generation, frame, index, size, pcrc, input_error, stages = values
    expected = 1024 if header[5] == 6 and header[6] == 0 else 0
    need(size == len(body) == expected and zlib.crc32(body) == pcrc, '响应 payload 长度/CRC 不符')
    reply = dict(op=header[5], error=header[6], flags=header[7], txn=txn, generation=generation,
                 frame=frame, index=index, input_error=input_error, completed_stages=stages)
    reply.update(zip(PAGE_KEYS, struct.unpack_from('!IIIII', header, 76)))
    return reply, body


def make_profile(records, frame, generation):
    need(len(records) == 83, '统计记录数不符')
    for field in COUNTERS:
        values = [r[field] for r in records]
        need(all(type(v) is int and 0 <= v < 0xffffffff for v in values)
             and sum(values[1:]) == values[0], '统计阶段/总计不符或饱和：' + field)
    need(records[0]['cycles'] > 0, '统计周期为空')
    return dict(complete=True, schema='FCP1_PROFILE_V1', frame_id=frame, generation=generation,
                ui_clock_hz=CLOCK_HZ, graph_elapsed_cycles=records[0]['cycles'],
                graph_elapsed_ms=records[0]['cycles'] / 100000, total=records[0],
                stages=[dict(stage=i, **r) for i, r in enumerate(records[1:])],
                counters_are_nonexclusive=True, fps_5_accepted=False)


def make_summary(total, frame, generation):
    need(set(total) == set(COUNTERS) and all(type(v) is int and 0 <= v < 0xffffffff
         for v in total.values()) and total['cycles'] > 0, '总计统计字段无效或饱和')
    return dict(complete=True, schema='FCP1_PROFILE_SUMMARY_V1', frame_id=frame, generation=generation,
                ui_clock_hz=CLOCK_HZ, graph_elapsed_cycles=total['cycles'],
                graph_elapsed_ms=total['cycles']/100000, total=total, stages=[],
                telemetry_mode='summary', full_stages_available=False, stage_sum_verified=False,
                counters_are_nonexclusive=True, fps_5_accepted=False)
