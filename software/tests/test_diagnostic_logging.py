import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
import time

from PySide6.QtTest import QTest
import pytest

from dermademo.diagnostic_logging import (LogService, SessionLock, error_for, initialize, logged_task,
                                          logging_session, prune_sessions, record_event, record_exception)
from dermademo.gui import MainWindow, SAMPLE_PATH, create_application


@pytest.fixture
def log(tmp_path):
    value = LogService(tmp_path / "logs", fallback=tmp_path / "fallback")
    yield value
    value.shutdown()


def test_exception_stack_and_context_survive_queue_without_private_values(tmp_path):
    log = initialize(tmp_path / "logs")
    private = "C:/Users/private-person/patient-12345.png"
    try:
        with logged_task("load", "job-17"):
            raise ValueError(private)
    except ValueError:
        pass
    assert log.flush()
    events = [json.loads(line) for line in (log.directory / "app.jsonl").read_text(encoding="utf-8").splitlines()]
    failed = next(event for event in events if event["event"] == "task_failed")
    assert failed["job_id"] == "job-17" and failed["stage"] == "load"
    assert failed["error_type"] == "ValueError" and failed["stack"][0]["frames"][-1]["line"] > 0
    assert failed["error_id"] == error_for("job-17")
    assert private not in json.dumps(events) and "patient-12345" not in json.dumps(events)
    record_event("metadata", input_path=private, stage=private, count=2)
    assert "input_path" not in log.snapshot()["events"][-1] and "stage" not in log.snapshot()["events"][-1]


def test_rotation_is_bounded_and_sessions_are_independent(log, tmp_path):
    for number in range(100):
        log.event("rotation", count=number)
    assert log.flush()
    log2 = LogService(tmp_path / "logs", max_bytes=500)
    try:
        for number in range(100):
            log2.event("rotation", count=number)
        assert log2.flush()
        assert log.directory != log2.directory
        assert 1 <= len(list(log2.directory.glob("app.jsonl*"))) <= 4
        assert len(log2.snapshot()["events"]) == 100
    finally:
        log2.shutdown()


def test_unwritable_primary_and_fallback_preserve_memory(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    temporary = LogService(blocked, fallback=tmp_path / "fallback")
    try:
        assert temporary.persistent and temporary.storage_error == "primary_unavailable_using_temporary"
    finally:
        temporary.shutdown()
    memory = LogService(blocked, fallback=blocked)
    try:
        assert not memory.persistent
        memory.event("task_started", stage="process")
        assert memory.snapshot()["events"][0]["stage"] == "process"
    finally:
        memory.shutdown()


def test_queue_full_and_blocked_shutdown_do_not_block_producer(tmp_path, monkeypatch):
    log = LogService(tmp_path / "logs", capacity=1)
    entered, release = Event(), Event()
    original = log.file_handler.emit
    def block(record):
        entered.set()
        release.wait(3)
        original(record)
    monkeypatch.setattr(log.file_handler, "emit", block)
    try:
        log.event("first")
        assert entered.wait(1)
        started = time.monotonic()
        for number in range(100):
            log.event("overflow", count=number)
        assert time.monotonic() - started < 0.5 and log.dropped_events > 0
        assert not log.shutdown(timeout=0.01)
        assert log.flush_incomplete
    finally:
        release.set()
        log.shutdown()
        assert log._stopped.wait(3)


def test_runtime_write_failure_degrades_without_recursive_logging(log, monkeypatch):
    def fail(_record):
        raise OSError("disk unavailable")
    monkeypatch.setattr(log.file_handler, "shouldRollover", fail)
    log.event("write_failed")
    assert log.flush()
    assert not log.persistent and log.storage_error == "log_write_failed"
    assert log.snapshot()["events"][-1]["event"] == "write_failed"


def test_retention_respects_active_locks_and_foreign_files(tmp_path):
    current = LogService(tmp_path / "logs")
    old = LogService(tmp_path / "logs")
    old_directory = old.directory
    old.shutdown()
    active_directory = current.directory
    foreign = tmp_path / "logs/run-foreign"
    foreign.mkdir()
    (foreign / "session.json").write_text('[]')
    (foreign / "personal.txt").write_text("preserve")
    try:
        prune_sessions(tmp_path / "logs", max_bytes=0, days=0)
        assert not old_directory.exists() and active_directory.exists()
        assert (foreign / "personal.txt").exists()
    finally:
        current.shutdown()


def test_nested_initialization_hooks_and_context_restore(tmp_path):
    outer = initialize(tmp_path / "logs")
    hook = sys.excepthook
    with logging_session("outer") as same:
        assert same is outer and initialize() is outer
        with logged_task("inner", "inner-job"):
            record_event("inner_event")
        record_event("outer_event")
    assert sys.excepthook is hook and not outer.closed
    events = outer.snapshot()["events"]
    assert next(e for e in events if e["event"] == "inner_event")["job_id"] == "inner-job"
    assert next(e for e in events if e["event"] == "outer_event")["stage"] == "outer"


def test_gui_worker_error_is_logged_and_visible_without_losing_image(tmp_path):
    app = create_application()
    log = initialize(tmp_path / "logs")
    window = MainWindow()
    window.show()
    try:
        window.load_path(SAMPLE_PATH, is_synthetic=True)
        deadline = time.monotonic() + 5
        while window.busy and time.monotonic() < deadline:
            QTest.qWait(10)
        assert not window.busy
        image = window.image
        window.load_path(tmp_path / "missing.png")
        deadline = time.monotonic() + 5
        while window.busy and time.monotonic() < deadline:
            QTest.qWait(10)
        app.processEvents()
        assert not window.busy and window.image is image
        assert "错误编号" in window.status_label.text()
        assert any(e["event"] == "task_failed" and e["stage"] == "load" for e in log.snapshot()["events"])
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_cli_module_entry_logs_no_qt_and_preserves_json_stdout(tmp_path):
    result = subprocess.run([sys.executable, "-m", "dermademo.batch", "--list-backends"], capture_output=True, timeout=10,
                            env={**os.environ, "ALIENHOME_DATA_DIR": str(tmp_path / "logs-root"), "PYTHONIOENCODING": "utf-8"})
    assert result.returncode == 0
    assert json.loads(result.stdout)[0]["backend"]["key"] == "demo_cpu"
    logs = list((tmp_path / "logs-root/logs").glob("run-*/app.jsonl"))
    assert len(logs) == 1
    events = [json.loads(line) for line in logs[0].read_text().splitlines()]
    assert any(e["event"] == "entry_exited" and e["code"] == 0 for e in events)
