"""Bounded, opt-in structured logging. No Qt, network or import-time I/O."""

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import json
import logging
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
import os
from pathlib import Path
from queue import Full, Queue
import re
import sys
import tempfile
from threading import Event, Lock, Thread
import threading
import time
import traceback
from uuid import uuid4

from . import __version__
from .runtime_paths import data_root


_service = None
_global_lock = Lock()
_context = ContextVar("diagnostic_context", default={})
_TOKEN = re.compile(r"^[\w.:-]{1,100}$", re.ASCII)
_FIELDS = {"job_id", "stage", "outcome", "backend", "profile", "frame_id", "elapsed_ms", "count", "code"}


class SessionLock:
    def __init__(self, path):
        self.stream = open(path, "a+b")
        try:
            if self.stream.seek(0, 2) == 0:
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.stream.close()
            raise

    def close(self):
        self.stream.close()  # OS releases byte-range/flock locks, including on crash.


def prune_sessions(root: Path, *, max_bytes=100 * 1024 * 1024, days=7):
    """Only remove our inactive, flat, identified session folders, never recurse."""
    candidates, total = [], 0
    for directory in sorted(root.glob("run-*")):
        if directory.is_symlink() or not directory.is_dir() or directory.resolve().parent != root.resolve():
            continue
        try:
            files = list(directory.iterdir())
            if any(path.is_symlink() or not path.is_file() or not re.fullmatch(r"app\.jsonl(?:\.[1-3])?|session\.(?:json|lock)", path.name) for path in files):
                continue
            metadata = json.loads((directory / "session.json").read_text(encoding="utf-8"))
            if not isinstance(metadata, dict) or metadata.get("format") != "alienhome.log-session":
                continue
            if type(metadata.get("created")) not in (int, float) or not 0 <= metadata["created"] <= time.time() + 86400:
                continue
            size = sum(path.stat().st_size for path in files)
            total += size
            candidates.append((metadata["created"], directory, files, size))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    for created, directory, files, size in sorted(candidates):
        if time.time() - created < days * 86400 and total <= max_bytes:
            continue
        try:
            lock = SessionLock(directory / "session.lock")
        except OSError:
            continue
        lock.close()
        try:
            for path in files:
                path.unlink()
            directory.rmdir()
            total -= size
        except OSError:
            continue


class _SafeFileHandler(RotatingFileHandler):
    def __init__(self, path, service, max_bytes):
        self.service = service
        super().__init__(path, maxBytes=max_bytes, backupCount=3, encoding="utf-8", errors="backslashreplace")
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        barrier = getattr(record, "barrier", None)
        if barrier is not None:
            barrier.set()
        elif self.service.persistent:
            super().emit(record)

    def handleError(self, record):
        self.service.persistent = False
        self.service.storage_error = "log_write_failed"


class _BoundedHandler(QueueHandler):
    def __init__(self, queue, service):
        super().__init__(queue)
        self.service = service

    def handleError(self, record):
        self.service.dropped_events += 1  # Never recursively log or write stderr.


class _Listener(QueueListener):
    def enqueue_sentinel(self):
        self.queue.put(self._sentinel, timeout=1)


class LogService:
    def __init__(self, root: Path | None, *, fallback: Path | None = None, capacity=2000, max_bytes=5 * 1024 * 1024):
        self.session_id = f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{os.getpid()}-{uuid4().hex[:8]}"
        self.directory = self.lock = self.listener = self.file_handler = None
        self.persistent, self.storage_error = False, None
        self.dropped_events, self.flush_incomplete = 0, False
        self.closed = False
        self._ring, self._errors, self._mutex = deque(maxlen=200), {}, Lock()
        self._stopped, self._shutdown_started = Event(), False
        self.queue = Queue(maxsize=capacity)
        self.logger = logging.Logger(f"alienhome.{self.session_id}", logging.DEBUG)
        self.logger.propagate = False
        self.logger.addHandler(_BoundedHandler(self.queue, self))
        locations = [] if root is None else [Path(root)]
        try:
            locations.append(Path(fallback) if fallback is not None else Path(tempfile.gettempdir()) / "AlienHomeDerma-logs")
        except OSError:
            pass
        for base in dict.fromkeys(locations):
            lock = None
            try:
                base.mkdir(parents=True, exist_ok=True)
                directory = base / self.session_id
                directory.mkdir(exist_ok=False)
                lock = SessionLock(directory / "session.lock")
                (directory / "session.json").write_text(json.dumps({"format": "alienhome.log-session", "created": time.time()}), encoding="utf-8")
                handler = _SafeFileHandler(directory / "app.jsonl", self, max_bytes)
                self.directory, self.lock, self.file_handler = directory, lock, handler
                self.persistent = True
                self.storage_error = None if root is not None and base == Path(root) else "primary_unavailable_using_temporary"
                self.listener = _Listener(self.queue, handler)
                self.listener.start()
                try:
                    prune_sessions(base)
                except OSError:
                    pass
                break
            except OSError:
                if lock:
                    lock.close()
        if self.listener is None:
            self.storage_error = "persistent_logging_unavailable"

    def event(self, name, *, exception=None, **fields):
        if self.closed:
            return None
        if not isinstance(name, str) or not _TOKEN.fullmatch(name):
            name = "unrecognized_event"
        context = {**_context.get(), **fields}
        event = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": name, "session_id": self.session_id,
                 "process_id": os.getpid(), "app_version": __version__, "level": "ERROR" if exception else "INFO"}
        for key, value in context.items():
            if key not in _FIELDS:
                continue
            if type(value) in (int, float, bool) and (type(value) is not float or -1e100 < value < 1e100):
                event[key] = value
            elif isinstance(value, str) and _TOKEN.fullmatch(value):
                event[key] = value
        if exception is not None:
            event["error_id"] = uuid4().hex[:12]
            event["error_type"] = type(exception).__name__[:80]
            event["code"] = getattr(exception, "winerror", None) or getattr(exception, "errno", None)
            chain, seen, current = [], set(), exception
            while current is not None and id(current) not in seen and len(chain) < 4:
                seen.add(id(current))
                frames = [{"module": Path(frame.filename).name, "function": frame.name[:80], "line": frame.lineno}
                          for frame in traceback.extract_tb(current.__traceback__)[-24:]]
                chain.append({"type": type(current).__name__[:80], "frames": frames})
                current = current.__cause__ or (current.__context__ if not current.__suppress_context__ else None)
            event["stack"] = chain
        encoded = json.dumps(event, ensure_ascii=True, allow_nan=False)
        if len(encoded) > 16000:
            event.pop("stack", None)
            event["truncated"] = True
            encoded = json.dumps(event, ensure_ascii=True)
        with self._mutex:
            self._ring.append(encoded)
            if "error_id" in event:
                if len(self._errors) >= 100:
                    self._errors.pop(next(iter(self._errors)))
                self._errors[event.get("job_id", event["error_id"])] = event["error_id"]
        if self.listener is not None:
            self.logger.log(logging.ERROR if exception else logging.INFO, encoded)
        return event.get("error_id")

    def snapshot(self):
        with self._mutex:
            events = [json.loads(line) for line in self._ring]
        return {"session_id": self.session_id, "persistent": self.persistent, "storage_error": self.storage_error,
                "dropped_events": self.dropped_events, "flush_incomplete": self.flush_incomplete,
                "closed": self.closed, "events": events}

    def flush(self, timeout=2):
        if self.listener is None or self.closed:
            return self.listener is None
        barrier = Event()
        record = logging.LogRecord("barrier", logging.INFO, "", 0, "", (), None)
        record.barrier = barrier
        deadline = time.monotonic() + timeout
        try:
            self.queue.put(record, timeout=timeout)
        except Full:
            return False
        return barrier.wait(max(0, deadline - time.monotonic()))

    def shutdown(self, timeout=2):
        with self._mutex:
            if not self._shutdown_started:
                self._shutdown_started = True
                self.closed = True
                Thread(target=self._stop, name="diagnostic-log-shutdown", daemon=True).start()
        if not self._stopped.wait(timeout):
            self.flush_incomplete = True
            return False
        return not self.flush_incomplete

    def _stop(self):
        try:
            if self.listener:
                self.listener.stop()
            if self.file_handler:
                self.file_handler.close()
            if self.lock:
                self.lock.close()
        except Exception:
            self.flush_incomplete = True
        finally:
            self._stopped.set()


def initialize(root=None):
    global _service
    with _global_lock:
        if _service is None or _service.closed:
            try:
                location = Path(root) if root is not None else data_root() / "logs"
            except (OSError, ValueError):
                location = None
            _service = LogService(location)
        return _service


def service():
    return _service


def record_event(name, **fields):
    if _service is None:
        return None
    try:
        return _service.event(name, **fields)
    except Exception:
        _service.dropped_events += 1
        return None


def record_exception(name, exception, **fields):
    return record_event(name, exception=exception, **fields)


def error_for(job_id):
    if _service:
        with _service._mutex:
            return _service._errors.get(job_id)


@contextmanager
def logged_task(stage, job_id=None, **fields):
    context = {**_context.get(), "stage": stage, "job_id": job_id or uuid4().hex, **fields}
    token = _context.set(context)
    started = time.perf_counter()
    record_event("task_started")
    try:
        yield context["job_id"]
    except BaseException as exc:
        cancelled = isinstance(exc, KeyboardInterrupt) or type(exc).__name__ == "TransferCancelled"
        normal_exit = isinstance(exc, SystemExit) and exc.code in (0, None)
        record_event("task_finished" if normal_exit else "task_cancelled" if cancelled else "task_failed",
                     exception=None if cancelled or normal_exit else exc,
                     outcome="completed" if normal_exit else "cancelled" if cancelled else "failed")
        raise
    else:
        record_event("task_finished", outcome="completed", elapsed_ms=(time.perf_counter() - started) * 1000)
    finally:
        _context.reset(token)


@contextmanager
def logging_session(stage):
    owner = _service is None or _service.closed
    current = initialize()
    old_sys, old_thread = sys.excepthook, threading.excepthook
    def sys_hook(kind, value, tb):
        record_exception("unhandled_exception", value, stage=stage)
        if sys.stderr is not None:
            old_sys(kind, value, tb)
    def thread_hook(args):
        record_exception("unhandled_thread_exception", args.exc_value, stage=stage)
        if sys.stderr is not None:
            old_thread(args)
    if owner:
        sys.excepthook, threading.excepthook = sys_hook, thread_hook
    try:
        with logged_task(stage):
            yield current
    finally:
        if owner:
            sys.excepthook, threading.excepthook = old_sys, old_thread
            current.shutdown()


def entry_session(stage):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with logging_session(stage):
                result = function(*args, **kwargs)
                record_event("entry_exited", stage=stage, code=result, outcome="success" if result == 0 else "failed")
                return result
        return wrapped
    return decorate
