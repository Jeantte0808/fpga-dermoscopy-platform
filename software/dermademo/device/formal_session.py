"""Cancellable one-frame V5 service, shared by GUI and frozen console entry."""

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import ipaddress
import json
import os
from pathlib import Path
import socket
import tempfile
from threading import Event
import time
from uuid import uuid4
import zlib

from ..runtime_paths import data_root
from .formal_protocol import (COUNTERS, ERRORS, IMAGE_ID, PAGE_KEYS, SEED_FLOOR, FormalProtocolError,
                              assets, decode, encode, make_profile, make_summary, need)
from .formal_capture import png_bytes


@dataclass(frozen=True)
class FormalConfig:
    pc: str = '192.168.0.3'
    pc_port: int = 6102
    board: str = '192.168.0.2'
    board_port: int = 5000
    timeout: float = .5
    operation_timeout: float = 30
    reuse_model: bool = False
    telemetry_mode: str = 'full'  # Preserve existing Python callers; GUI/CLI select summary explicitly.
    trace_operations: bool = True
    hardware_profile: str = 'unspecified'

    def __post_init__(self):
        for address in (self.pc, self.board):
            ipaddress.IPv4Address(address)
        need(all(type(p) is int and 1 <= p <= 65535 for p in (self.pc_port, self.board_port)), '端口无效')
        need(.01 <= self.timeout <= 5 and self.timeout <= self.operation_timeout <= 60, '超时范围无效')
        need(self.telemetry_mode in ('summary', 'full') and type(self.trace_operations) is bool, '诊断模式无效')
        from ..hardware import PROFILE_CHOICES
        need(self.hardware_profile in [key for _label, key in PROFILE_CHOICES], '硬件声明选项无效')


class LocalPersistenceError(OSError):
    """A local artifact failure, independent of the device's FINISH state."""


def write_bytes(path, value):
    try:
        if Path(path).write_bytes(value) != len(value):
            raise OSError('Short local artifact write')
    except OSError as exc:
        raise LocalPersistenceError(str(exc)) from exc


def write_json(path, value):
    """Replace a whole report/profile, retaining the previous report on failure."""
    path, temporary = Path(path), None
    try:
        encoded = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
        with tempfile.NamedTemporaryFile('wb', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            if stream.write(encoded) != len(encoded):
                raise OSError('Short JSON write')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise LocalPersistenceError(str(exc)) from exc
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


@contextmanager
def transaction_state(directory, source_state=None):
    """Hold an OS lock for the session; reserve durable IDs before sending anything."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'transaction.lock').open('a+b') as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise FormalProtocolError('已有正式 Mask 客户端占用事务状态，请先停止其他客户端') from exc
        try:
            path = directory / 'transaction_highwater.json'
            floors = [SEED_FLOOR]
            for current in (path, Path(source_state) if source_state else None):
                if current is not None:
                    if current == path and not current.exists():
                        continue
                    value = json.loads(current.read_text(encoding='utf-8'))['reserved_through']
                    need(type(value) is int and 0 <= value < 0xffffffff, '事务高水位损坏；不得删除或回退')
                    floors.append(value)
            next_id = max(max(floors) + 1, int(time.time()))
            end = next_id

            def transaction():
                nonlocal next_id, end
                if next_id == end:
                    end = next_id + 10000
                    need(end <= 0xffffffff, '事务号耗尽；请与硬件成员协调新 epoch，不可回绕')
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=directory, delete=False) as stream:
                            temporary = Path(stream.name)
                            json.dump({'reserved_through': end - 1}, stream)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, path)
                    finally:
                        if temporary is not None and temporary.exists():
                            temporary.unlink()
                value = next_id
                next_id += 1
                return value

            yield transaction
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def transport_report():
    return dict(successful_operations=0, retries=0, ignored_packets=0, invalid_replies=0, BUSY=0)


class FormalClient:
    def __init__(self, sock, config, transaction, report, cancel):
        self.sock, self.config, self.transaction = sock, config, transaction
        self.report, self.cancel = report, cancel

    def check_cancel(self):
        if self.cancel.is_set():
            raise InterruptedError('已停止 PC 等待；没有向 FPGA 发送 CANCEL/FINISH 或自动复位')

    def call(self, op, generation, body=b'', *, frame=0, index=0, frame_crc=0):
        started = time.perf_counter()
        self._operation_started = started
        self._first_send_clock = None
        self._operation = dict(op=op, txn=None, generation=generation, frame=frame, index=index,
                               sends=0, BUSY=0, timeout_retries=0, tx_bytes=0, rx_bytes=0,
                               first_matching_reply_ms=None, success=False, error=None)
        try:
            value = self._call(op, generation, body, frame=frame, index=index, frame_crc=frame_crc)
            self._operation['success'] = True
            return value
        except Exception as exc:
            self._operation['error'] = type(exc).__name__ + ': ' + str(exc)
            raise
        finally:
            self._operation['elapsed_ms'] = (time.perf_counter()-started)*1000
            self._operation['sent_to_completion_ms'] = ((time.perf_counter()-self._first_send_clock)*1000
                                                       if self._first_send_clock is not None else None)
            clock = time.perf_counter()
            trace = self.report.setdefault('operation_trace', dict(enabled=self.config.trace_operations,
                sampling='all_calls' if self.config.trace_operations else 'disabled',
                overhead_scope='trace append only; per-call clock/counter cost included in elapsed, not isolated',
                bookkeeping_ms=0.0, calls=[]))
            if self.config.trace_operations:
                trace['calls'].append(self._operation)
            trace['bookkeeping_ms'] += (time.perf_counter()-clock)*1000

    def _call(self, op, generation, body=b'', *, frame=0, index=0, frame_crc=0):
        self.check_cancel()
        txn = self.transaction()
        self._operation['txn'] = txn
        request = encode(op, txn, generation, body, frame=frame, index=index, frame_crc=frame_crc)
        self.report['last_request'] = dict(op=op, txn=txn, generation=generation, frame=frame, index=index)
        deadline = time.monotonic() + self.config.operation_timeout
        attempt, previous_busy = 0, False
        while time.monotonic() < deadline:
            self.check_cancel()
            attempt += 1
            if attempt > 1:
                self.report['retries'] += 1
                if not previous_busy:
                    self._operation['timeout_retries'] += 1
            if self._first_send_clock is None:
                self._first_send_clock = time.perf_counter()
            self.sock.sendto(request, (self.config.board, self.config.board_port))
            self._operation['sends'] += 1
            self._operation['tx_bytes'] += len(request)
            until = min(deadline, time.monotonic() + self.config.timeout)
            busy = False
            while time.monotonic() < until:
                self.check_cancel()
                self.sock.settimeout(max(.001, min(.1, until - time.monotonic())))
                try:
                    packet, peer = self.sock.recvfrom(65535)
                except socket.timeout:
                    continue
                self._operation['rx_bytes'] += len(packet)
                if peer != (self.config.board, self.config.board_port):
                    self.report['ignored_packets'] += 1
                    continue
                try:
                    reply, payload = decode(packet)
                except FormalProtocolError:
                    self.report['invalid_replies'] += 1
                    continue
                if reply['txn'] != txn or reply['op'] != op:
                    self.report['ignored_packets'] += 1
                    continue
                self.report['last_reply'] = reply
                if self._operation['first_matching_reply_ms'] is None:
                    self._operation['first_matching_reply_ms'] = (time.perf_counter()-self._first_send_clock)*1000
                if reply['error'] == 136:
                    self.report['BUSY'] += 1
                    self._operation['BUSY'] += 1
                    busy = True
                    break
                if reply['error']:
                    raise FormalProtocolError(f"板卡错误 {reply['error']}：{ERRORS.get(reply['error'], '硬件错误')}；{reply}")
                expected_generation = generation + 1 if op == 1 else generation
                need(op == 8 or reply['generation'] == expected_generation, '响应 generation 不匹配')
                need(op not in (4, 5, 6, 7) or reply['frame'] == frame, '响应 frame 不匹配')
                need(reply['index'] == index, '响应 index 不匹配')
                need(not reply['flags'] & 16, '板卡报告硬件故障，保留现场等待共同恢复')
                # On telemetry pages input_error carries diagnostic flags, not the normal error field.
                need(op == 8 or reply['input_error'] == 0, '板卡输入错误；不可作为有效结果')
                self.report['successful_operations'] += 1
                return reply, payload
            if busy:
                self.cancel.wait(.005)
            previous_busy = busy
        raise TimeoutError('操作超时；保留本次请求编号，未自动释放结果或重置设备')


def collect_profile(client, frame, generation):
    diagnostic, previous = transport_report(), client.report
    client.report = diagnostic
    try:
        records = []
        summary = client.config.telemetry_mode == 'summary'
        for record in range(1 if summary else 83):
            words = []
            for page in range(3):
                index = 0x50520000 | (record << 4) | page
                reply, body = client.call(8, 0, index=index)
                need(not body and reply['frame'] == frame and reply['generation'] == generation
                     and reply['completed_stages'] == 82 and not reply['input_error'] & 6
                     and (record != 0 or reply['input_error'] & 1), '统计记录身份不符、未完成或已饱和')
                words.extend(reply[key] for key in PAGE_KEYS)
            records.append(dict(zip(COUNTERS, words)))
        profile = make_summary(records[0], frame, generation) if summary else make_profile(records, frame, generation)
        if not summary:
            profile.update(telemetry_mode='full', full_stages_available=True, stage_sum_verified=True)
        profile['transport'] = diagnostic
        return profile
    except Exception as exc:
        diagnostic['validation_error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        previous['telemetry_transport'] = diagnostic
        client.report = previous


@dataclass(frozen=True)
class FormalOutcome:
    rgb: bytes
    mask: bytes
    report: dict
    profile: dict | None
    directory: Path
    source_bytes: bytes | None = None


def run_formal(rgb, config, output, *, state_dir=None, source_state=None, cancel=None, progress=None, assets_dir=None,
               input_context=None, source_bytes=None, cpu_preprocessing_ms=None, _transaction=None):
    """Explicit live action. Assets are read-only; state/captures are always external."""
    preflight_clock = time.perf_counter()
    manifest, blob, _sample = assets(assets_dir)
    need(type(rgb) is bytes and len(rgb) == 196608, '输入必须是紧密 256×256 RGB888，196608 字节')
    from ..formal_input import validate_source, FIXED_THRESHOLD, MODEL_VERSION
    from ..hardware import hardware_declaration
    from .. import __version__
    if input_context is not None:
        validate_source(input_context, rgb, source_bytes)
        if input_context.get('schema') == 'formal_roi_v1':
            need(not input_context['rule_confirmation']['test_only'] or ipaddress.ip_address(config.board).is_loopback,
                 'ROI测试夹具输入不得发送到真实板卡')
    else:
        need(source_bytes is None, '原始图片缺少预处理信息')
    cancel, progress = cancel or Event(), progress or (lambda _stage, _count: None)
    directory = Path(output) / ('formal_mask_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    report = dict(complete=False, protocol='FCP1/FCR1_candidate', model_image_id=IMAGE_ID,
                  input_sha256=sha256(rgb).hexdigest(), **transport_report(), board=[config.board, config.board_port],
                  pc=[config.pc, config.pc_port], pure_FPGA_inference_ms=None, fps_accepted=False,
                  origin='protocol_simulator' if ipaddress.ip_address(config.board).is_loopback else 'live_FPGA_session',
                  hardware_identity_verified=False, hardware_finish_confirmed=False,
                  local_artifacts_complete=False, stage='save_inputs', error=None,
                  input_preprocessing='exact_RGB888_no_resize_no_CLAHE_no_host_normalization')
    report.update(client_version=__version__, model_version=MODEL_VERSION, fixed_logits_threshold=dict(FIXED_THRESHOLD),
                  hardware_declaration=hardware_declaration(config.hardware_profile),
                  hardware_declaration_profile=config.hardware_profile)
    report['host_preflight_ms'] = (time.perf_counter()-preflight_clock)*1000
    report.update(telemetry_mode=config.telemetry_mode, stage_timings_ms={key: None for key in (
        'image_begin','rgb_upload','first_mask_wait','remaining_mask','mask_validation','finish',
        'statistics','local_save','display_prepare')},
        local_save_scope='input/artifact writes; excludes transaction journal and final atomic report commit',
        progress_policy='RGB every 16 chunks; Mask every 8; queued UI signals only')
    if input_context is not None:
        report.update(input_context=input_context, input_preprocessing=input_context['preprocessing']['version'])
    report['cpu_preprocessing_ms'] = cpu_preprocessing_ms
    mask, profile = bytearray(), None
    started = time.perf_counter()
    save_ms = 0.0
    save_started = started
    @contextmanager
    def phase(name):
        clock = time.perf_counter()
        try:
            yield
        finally:
            report['stage_timings_ms'][name] = (time.perf_counter()-clock)*1000
    try:
        write_bytes(directory / 'INCOMPLETE.txt', b'FPGA image session not yet complete.\n')
        write_json(directory / 'report.json', report)
        write_bytes(directory / 'input.rgb', rgb)
        write_bytes(directory / 'input.png', png_bytes(rgb, 3))
        if source_bytes is not None:
            write_bytes(directory / 'source_image.bin', source_bytes)
        save_ms += (time.perf_counter()-started)*1000
        save_started = None
        report['stage'] = 'socket'
        # Experiments hold the same OS lock across all frames, including CPU gaps.
        with (nullcontext(_transaction) if _transaction is not None else
              transaction_state(state_dir or data_root() / 'formal_mask', source_state)) as transaction:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind((config.pc, config.pc_port))
                client = FormalClient(sock, config, transaction, report, cancel)
                status, _ = client.call(8, 0)
                generation = status['generation']
                need(status['input_error'] == 0 and not status['flags'] & (4 | 8 | 16),
                     '设备有未释放结果/故障；不要自动 UPDATE，请先与硬件成员确认恢复')
                if not config.reuse_model:
                    report['stage'] = 'model_update'
                    status, _ = client.call(1, generation)
                    generation = status['generation']
                    clock = time.perf_counter()
                    for fragment in manifest['fragments']:
                        i, at, size = fragment['index'], fragment['offset'], fragment['bytes']
                        report['stage'] = 'parameter_' + str(i)
                        client.call(2, generation, blob[at:at + size], index=i)
                        if i % 32 == 0 or i == 2930:
                            progress('模型上传', i + 1)
                    report['model_upload_ms'] = (time.perf_counter() - clock) * 1000
                    report['stage'] = 'hardware_model_CRC'
                    clock = time.perf_counter()
                    status, _ = client.call(3, generation)
                    report['model_verify_ms'] = (time.perf_counter() - clock) * 1000
                need(status['flags'] & 2 and status['loaded_bytes'] == 2770336
                     and status['next_fragment'] == 2931, '模型未通过 DDR 读回资格校验；不能复用')
                frame = transaction()
                report.update(frame_id=frame, generation=generation, stage='image_begin')
                image_started = time.perf_counter()
                with phase('image_begin'):
                    client.call(4, generation, frame=frame, frame_crc=zlib.crc32(rgb))
                with phase('rgb_upload'):
                    for i in range(192):
                        report['stage'] = 'RGB_' + str(i)
                        client.call(5, generation, rgb[i*1024:(i+1)*1024], frame=frame, index=i)
                        if (i+1) % 16 == 0:
                            progress('RGB 上传', i + 1)
                ready = time.perf_counter()
                report['RGB_upload_ms'] = (ready - image_started) * 1000
                report['RGB_upload_legacy_scope'] = 'includes IMAGE_BEGIN; use stage_timings_ms.rgb_upload for 192 chunks only'
                for i in range(64):
                    report['stage'] = 'Mask_' + str(i)
                    clock = time.perf_counter()
                    try:
                        reply, body = client.call(6, generation, frame=frame, index=i)
                    finally:
                        key = 'first_mask_wait' if i == 0 else 'remaining_mask'
                        report['stage_timings_ms'][key] = (report['stage_timings_ms'][key] or 0) + (time.perf_counter()-clock)*1000
                    need(reply['completed_stages'] == 82 and reply['result_frame'] == frame
                         and reply['result_generation'] == generation, 'Mask 结果身份/阶段不完整')
                    if i == 0:
                        report['host_observed_result_wait_ms'] = (time.perf_counter() - ready) * 1000
                    mask.extend(body)
                    if (i+1) % 8 == 0:
                        progress('Mask 读取', i + 1)
                clock = time.perf_counter()
                need(len(mask) == 65536 and set(mask) <= {0, 255}, 'Mask 必须为 65536 字节 0/255')
                report['mask_sha256'] = sha256(mask).hexdigest()
                if report['input_sha256'] == manifest['sample_RGB_sha256']:
                    report['official_fixed_sample_exact'] = report['mask_sha256'] == manifest['sample_Mask_sha256']
                    need(report['official_fixed_sample_exact'], '正式固定样例 Mask 摘要不符；保留板卡结果租约')
                report['stage_timings_ms']['mask_validation'] = (time.perf_counter()-clock)*1000
                report['stage'] = 'finish'
                with phase('finish'):
                    finish, _ = client.call(7, generation, frame=frame)
                need(finish['completed_stages'] == 82 and finish['result_frame'] == frame
                     and finish['result_generation'] == generation and finish['loaded_bytes'] == 2770336
                     and finish['next_fragment'] == 2931 and finish['next_RGB_chunk'] == 192, 'FINISH 回执不完整')
                report['image_E2E_ms'] = (time.perf_counter() - image_started) * 1000
                report['rgb_to_valid_mask_ms'] = report['image_E2E_ms']
                report['rgb_to_valid_mask_scope'] = ('host IMAGE_BEGIN call through validated Mask and legal FINISH; '
                    'excludes status/model load/preparation/telemetry/save; includes IPC-equivalent transport')
                report.update(hardware_finish_confirmed=True, stage='save_artifacts')
                telemetry_started = time.perf_counter()
                try:
                    progress('板内统计', 0)
                    profile = collect_profile(client, frame, generation)
                except Exception as exc:
                    # FINISH has already succeeded. Keep the actual Mask, expose missing timing.
                    profile = dict(complete=False, error=type(exc).__name__ + ': ' + str(exc))
                report['telemetry_ms'] = (time.perf_counter() - telemetry_started) * 1000
                report['stage_timings_ms']['statistics'] = report['telemetry_ms']
        # Commit local files only after the hardware lease and contexts have ended.
        save_started = time.perf_counter()
        write_bytes(directory / 'mask.raw', mask)
        write_bytes(directory / 'mask.png', png_bytes(mask, 1))
        if profile is not None:
            report['stage'] = 'save_profile'
            write_json(directory / 'profile.json', profile)
        report['stage'] = 'remove_incomplete'
        try:
            (directory / 'INCOMPLETE.txt').unlink()
        except OSError as exc:
            raise LocalPersistenceError(str(exc)) from exc
        report['stage'] = 'save_report'
        report['stage_timings_ms']['local_save'] = save_ms + (time.perf_counter()-save_started)*1000
        save_started = None
        report['total_ms_including_model_and_telemetry'] = (time.perf_counter() - started) * 1000
        committed = dict(report, complete=True, local_artifacts_complete=True, stage='complete')
        write_json(directory / 'report.json', committed)
        report.update(committed)
    except Exception as exc:
        if save_started is not None:
            report['stage_timings_ms']['local_save'] = save_ms + (time.perf_counter()-save_started)*1000
        report['complete'] = False
        report['local_artifacts_complete'] = False
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        report['failure_domain'] = ('local_storage' if isinstance(exc, LocalPersistenceError)
                                    else 'cancelled' if isinstance(exc, InterruptedError) else 'session')
        report['cancelled'] = isinstance(exc, InterruptedError)
        report['manual_recovery_required'] = ('last_request' in report and not report['hardware_finish_confirmed'])
    finally:
        if not report['complete']:
            if mask:
                try:
                    write_bytes(directory / 'partial_mask.raw', mask)
                except OSError as exc:
                    report['partial_persistence_error'] = str(exc)
            report['total_ms_including_model_and_telemetry'] = (time.perf_counter() - started) * 1000
            # Return a failure even when the diagnostic directory itself is unwritable.
            # The previous atomic report remains incomplete; never return a false success.
            for action in (lambda: write_bytes(directory / 'INCOMPLETE.txt', b'Local capture is incomplete.\n'),
                           lambda: write_json(directory / 'report.json', report)):
                try:
                    action()
                except OSError as exc:
                    report['diagnostic_persistence_error'] = str(exc)
    return FormalOutcome(rgb, bytes(mask), report, profile, directory, source_bytes)
