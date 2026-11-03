"""Serial formal FPGA queue. Every successful item is a new, durable frame.

No packet pipelining, replay substitution, automatic board recovery or old-ID retry.
"""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
from threading import Event, Lock
from time import perf_counter
from uuid import uuid4

from .analysis_identity import result_identity
from .device.formal_capture import png_bytes
from .device.formal_session import FormalConfig, run_formal, write_bytes, write_json
from .formal_input import PreparedImage, prepare_image, validate_source
from .formal_results import live_pair
from .storage import save_result

MAX_ITEMS = 256
MAX_ATTEMPTS = 1000
MIN_FREE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class QueueInput:
    item_id: str
    filename: str
    folder: Path
    manifest_sha256: str
    roi: tuple | None = None


def freeze_input(path, workspace, *, prepared=None):
    """Snapshot to disk once; subsequent filesystem edits never change this job."""
    path, workspace = Path(path), Path(workspace)
    if prepared is None:
        if path.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
            raise ValueError('批量入口仅支持JPEG/PNG')
        prepared = prepare_image(path)
    validate_source(prepared.context, prepared.rgb, prepared.source_bytes)
    folder = workspace / 'inputs' / uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    write_bytes(folder / 'source.bin', prepared.source_bytes)
    write_bytes(folder / 'input.rgb', prepared.rgb)
    preview = png_bytes(prepared.rgb, 3)
    write_bytes(folder / 'preview.png', preview)
    document = dict(schema='alienhome.queue_input.v1', filename=prepared.path.name,
                    context=prepared.context, cpu_preprocessing_ms=prepared.cpu_preprocessing_ms,
                    preview_sha256=sha256(preview).hexdigest())
    write_json(folder / 'input.json', document)
    roi = prepared.context.get('roi')
    return QueueInput(folder.name, prepared.path.name, folder, sha256((folder / 'input.json').read_bytes()).hexdigest(),
                      tuple(roi['bounds']) if roi else None)


def read_input(item):
    manifest = (item.folder / 'input.json').read_bytes()
    if len(manifest) > 262144 or sha256(manifest).hexdigest() != item.manifest_sha256:
        raise ValueError('队列输入声明损坏，不能发送')
    data = json.loads(manifest)
    if data.get('schema') != 'alienhome.queue_input.v1' or data['filename'] != item.filename:
        raise ValueError('队列输入身份不符')
    preview = item.folder / 'preview.png'
    if preview.stat().st_size > 262144 or sha256(preview.read_bytes()).hexdigest() != data.get('preview_sha256'):
        raise ValueError('输入预览摘要不符，不能发送可能被修改的快照')
    if (item.folder / 'input.rgb').stat().st_size != 196608 or (item.folder / 'source.bin').stat().st_size > 64 * 1024 * 1024:
        raise ValueError('队列输入大小无效')
    rgb, source = (item.folder / 'input.rgb').read_bytes(), (item.folder / 'source.bin').read_bytes()
    validate_source(data['context'], rgb, source)
    roi = data['context'].get('roi')
    if (tuple(roi['bounds']) if roi else None) != item.roi:
        raise ValueError('队列显示的ROI与实际输入不符')
    return PreparedImage(Path(item.filename), source, rgb, data['context'], data.get('cpu_preprocessing_ms'))


@dataclass(frozen=True)
class QueueStep:
    success: bool
    item_id: str
    pair: object
    row: dict


class FormalQueue:
    """One call per frame; GUI timers schedule dwell/pause between calls."""
    def __init__(self, items, config, directory, *, loop=False, state_dir=None, source_state=None,
                 classification_enabled=False,classification_python=None):
        if not 1 <= len(items) <= MAX_ITEMS or len({i.item_id for i in items}) != len(items):
            raise ValueError('队列需要1至256个不同的输入项')
        self.items = tuple(items)
        self.config = replace(config, reuse_model=False)  # A new queue does not guess DDR contents.
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        (self.directory / 'records').mkdir()
        self.state_dir, self.source_state = state_dir, source_state
        self.loop = bool(loop)
        self.index, self.cycle = 0, 1
        self.rows = []
        self.status, self.error = 'ready', None
        self._reuse_allowed = False
        self._lock = Lock()
        self._started = perf_counter()
        self._ended = None
        self.classification_enabled=bool(classification_enabled)
        self.classification_python=str(classification_python or '')
        self._classifier=None
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._persist()

    @property
    def finished(self):
        return self.status in ('completed', 'stopped', 'limit_reached')

    def summary(self):
        wall_ms = ((self._ended or perf_counter())-self._started)*1000
        successes = sum(row['status'] == 'success' for row in self.rows)
        return dict(schema='alienhome.formal_queue.v1', started_at=self.started_at,
                    status=self.status, error=self.error, loop=self.loop,
                    next_index=self.index, cycle=self.cycle, config=asdict(self.config),
                    success_count=successes,
                    failure_count=sum(row['status'] == 'failed' for row in self.rows),
                    classification_enabled=self.classification_enabled,classification_python=self.classification_python,
                    classification_success_count=sum(row.get('classification',{}).get('status')=='success' for row in self.rows),
                    classification_failure_count=sum(row.get('classification',{}).get('status')=='failed' for row in self.rows),
                    classification_cancelled_count=sum(row.get('classification',{}).get('status')=='cancelled' for row in self.rows),
                    classification_model_load_count=max((row.get('classification',{}).get('model_load_count',0) for row in self.rows),default=0),
                    classification_host_ms=sum(row.get('classification',{}).get('host_elapsed_ms',0) for row in self.rows),
                    cancelled_count=sum(row['status'] == 'cancelled' for row in self.rows),
                    attempt_count=len(self.rows), wall_ms=wall_ms,
                    frame_actions_ms=sum(row.get('active_ms', 0) for row in self.rows),
                    task_completion_rate_per_s=successes*1000/wall_ms if wall_ms > 0 else None,
                    task_rate_scope='queue_creation_to_last_record_completion_including_model_telemetry_save_classification_dwell_pause_failure; excludes_input_freeze_and_final_GUI_commit',
                    throughput_FPS=None, continuous_5FPS_accepted=False,
                    throughput_note='含用户停留/暂停，未执行专门连续吞吐验收；不取单帧耗时倒数。',
                    inputs=[dict(item_id=x.item_id, filename=x.filename,
                                 snapshot_directory=str(x.folder), manifest_sha256=x.manifest_sha256,
                                 roi=x.roi) for x in self.items],
                    attempts=self.rows)

    def _persist(self):
        write_json(self.directory / 'queue.json', self.summary())

    def close_classifier(self):
        client,self._classifier=self._classifier,None
        if client is not None:client.close()

    def _classify(self,record,prepared,cancel,progress):
        from .classification import ClassifierClient,run,load,attach,MODEL
        clock=perf_counter();folder=None
        state=dict(status='pending',scope='whole_original_image',source_sha256=sha256(prepared.source_bytes).hexdigest(),
                   model_version=MODEL,segmentation_record=str(record),failure_affects_segmentation=False)
        previous_loads=getattr(self._classifier,'model_load_count',0)
        try:
            if cancel.is_set():raise InterruptedError('分类已取消；分割结果已保存')
            progress('PC整图分类（不发送FPGA命令）',0)
            if self._classifier is None:self._classifier=ClassifierClient(self.classification_python)
            folder=run(self._classifier,prepared.source_bytes,prepared.path.name,self.directory/'classification_tasks',cancel=cancel)
            result=load(folder);attach(record,folder)
            linked=Path(record)/'classification'/folder.name
            state.update(status='success',folder=linked.relative_to(record).as_posix(),
                         result_sha256=sha256((linked/'result.json').read_bytes()).hexdigest(),
                         predicted_class=result['predicted_class'],class_scores=result['class_scores'],timing=result['timing'])
        except Exception as exc:
            state.update(status='cancelled' if cancel.is_set() else 'failed',error=f'{type(exc).__name__}: {exc}',
                         standalone_directory=str(folder) if folder else None)
        state.update(host_elapsed_ms=(perf_counter()-clock)*1000,model_load_count=getattr(self._classifier,'model_load_count',0),
                     model_loaded_this_item=getattr(self._classifier,'model_load_count',0)>previous_loads)
        try:write_json(Path(record)/'queue_classification.json',state)
        except Exception as exc:state.update(status='failed',error='分类状态保存失败：'+str(exc),failure_domain='classification_storage')
        return state

    def pause(self):
        if self.status in ('ready', 'running'):
            self.status = 'paused'
            self._persist()

    def resume(self, *, recovery_confirmed=False):
        if self.status not in ('paused', 'failed'):
            return
        if self.status == 'failed':
            if not recovery_confirmed:
                raise ValueError('请先核对本地保存及板卡状态，再明确确认恢复')
            self._reuse_allowed = False
        self.status, self.error = ('completed' if self.index == len(self.items) else 'ready'), None
        if self.status == 'completed':
            self._ended = perf_counter()
        self._persist()

    def stop(self):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('当前帧尚未结束；请在帧间停止')
        try:
            self.status = 'stopped'
            self._ended = perf_counter()
            self._persist()
        finally:
            self._lock.release()

    def step(self, *, cancel=None, progress=None):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('队列已有帧正在执行')
        try:
            if self.status not in ('ready', 'running'):
                raise ValueError('队列当前不能提交；请先恢复或建立新队列')
            if len(self.rows) >= MAX_ATTEMPTS:
                self.status = 'limit_reached'
                self._persist()
                raise ValueError('已达1000次尝试上限，请建立新队列以控制磁盘/内存增长')
            return self._step(cancel or Event(), progress or (lambda *_: None))
        finally:
            self._lock.release()

    def _step(self, cancel, progress):
        item = self.items[self.index]
        clock = perf_counter()
        row = dict(item_id=item.item_id, filename=item.filename, cycle=self.cycle,
                   attempt=len(self.rows)+1, status='running', frame_id=None, generation=None,
                   record_directory=None, capture_directory=None, error=None,
                   hardware_finish_confirmed=False, local_record_complete=False,
                   model_reuse=self._reuse_allowed)
        row['classification']=dict(status='not_run' if self.classification_enabled else 'not_enabled',scope='whole_original_image')
        outcome, pair, entered_wire = None, None, False
        original_index, original_cycle = self.index, self.cycle
        self.status = 'running'
        try:
            # If journal cannot be committed, do not contact the board.
            self._persist()
            if cancel.is_set():
                raise InterruptedError('已停止PC等待，尚未发送本项')
            if shutil.disk_usage(self.directory).free < MIN_FREE_BYTES:
                raise OSError('可用空间不足64MiB，队列暂停以保留证据')
            prepared = read_input(item)
            config = replace(self.config, reuse_model=self._reuse_allowed)
            entered_wire = True
            outcome = run_formal(prepared.rgb, config, self.directory / 'captures', state_dir=self.state_dir,
                                 source_state=self.source_state, cancel=cancel, progress=progress,
                                 input_context=prepared.context, source_bytes=prepared.source_bytes,
                                 cpu_preprocessing_ms=prepared.cpu_preprocessing_ms)
            report = outcome.report
            row.update(frame_id=report.get('frame_id'), generation=report.get('generation'),
                       capture_directory=str(outcome.directory), hardware_finish_confirmed=bool(report.get('hardware_finish_confirmed')),
                       failure_domain=report.get('failure_domain'), manual_recovery_required=bool(report.get('manual_recovery_required')),
                       transport={key: report.get(key) for key in ('BUSY', 'retries', 'invalid_replies', 'ignored_packets')},
                       stage_timings_ms=report.get('stage_timings_ms'), image_E2E_ms=report.get('image_E2E_ms'),
                       model_upload_ms=report.get('model_upload_ms'))
            if not report.get('complete'):
                raise RuntimeError(report.get('error') or '正式帧未完整完成')
            pair = live_pair(outcome)
            pair[1].metadata['analysis_id'] = uuid4().hex
            pair[1].metadata['queue'] = dict(item_id=item.item_id, cycle=self.cycle, attempt=row['attempt'])
            from .reference_library import update
            row['reference_comparison']=update(pair)
            row['identity'] = result_identity(pair)
            progress('保存本帧四窗记录', 0)
            save_started = perf_counter()
            record = save_result(self.directory / 'records', *pair)
            row.update(record_directory=str(record), local_record_complete=True,
                       record_save_ms=(perf_counter()-save_started)*1000,
                       graph_ms=pair[1].timings_ms.get('fpga_graph'), status='success')
            if self.classification_enabled:
                row['classification']=self._classify(record,prepared,cancel,progress)
        except Exception as exc:
            pair = None
            row.update(status='cancelled' if cancel.is_set() else 'failed', error=f'{type(exc).__name__}: {exc}')
            if outcome is None:
                row.update(failure_domain='session_unknown' if entered_wire else 'local_input',
                           manual_recovery_required=entered_wire)
            elif outcome.report.get('complete'):
                row['failure_domain'] = 'local_record_or_presentation'
            self._reuse_allowed = False
        row['active_ms'] = (perf_counter()-clock)*1000
        self.rows.append(row)
        if row['status'] == 'success':
            self.index += 1
            self._reuse_allowed = True
            if self.index == len(self.items):
                if self.loop:
                    self.index, self.cycle = 0, self.cycle+1
                else:
                    self.status = 'completed'
            else:
                self.status = 'ready'
        else:
            self.status, self.error = 'failed', row['error']
        if len(self.rows) >= MAX_ATTEMPTS and self.status == 'ready':
            self.status = 'limit_reached'
        if row['status']=='success' and cancel.is_set():self.status='stopped'
        if self.finished:
            self._ended = perf_counter()
            try:self.close_classifier()
            except Exception as exc:row['classification_cleanup_error']=str(exc)
        try:
            self._persist()
        except Exception as exc:
            # A durable individual record can exist, but the batch is NOT committed.
            self._reuse_allowed = False
            self.status, self.error = 'failed', f'队列索引保存失败：{exc}'
            self._ended = None
            row.update(status='failed', error=self.error, failure_domain='queue_index_storage')
            if pair is not None:
                self.index, self.cycle = original_index, original_cycle
            pair = None
        return QueueStep(row['status'] == 'success', item.item_id, pair, dict(row))

    def save_presentation(self, step, *, visible_ms, display_commit_ms):
        """Separate UI observation, never patch the raw hardware report/record."""
        data = dict(schema='alienhome.queue_presentation.v1', identity=step.row['identity'],
                    record_directory=step.row['record_directory'], user_visible_wait_ms=visible_ms,
                    display_commit_ms=display_commit_ms,
                    scope='frame_action_to_GUI_commit_including_record_save_excluding_asynchronous_screen_paint')
        write_json(self.directory / f'presentation_{step.row["attempt"]:04d}.json', data)
        self.rows[-1].update(user_visible_wait_ms=visible_ms, display_commit_ms=display_commit_ms)
        self._persist()

    def note_presentation_failure(self, message):
        self.status, self.error, self._ended = 'failed', message, None
        self._reuse_allowed = False
        self.rows[-1]['presentation_error'] = message
        self._persist()
