import json
from pathlib import Path

import pytest

from dermademo.device import formal_cli, formal_session as session
from dermademo.device.formal_protocol import assets
from formal_mask_peer import FormalPeer, local_config


@pytest.mark.parametrize('fault', ['mask.raw', 'mask.png', 'INCOMPLETE.txt'])
def test_post_finish_local_failure_is_not_success_or_unconfirmed_hardware(tmp_path, monkeypatch, fault):
    original_write, original_unlink = Path.write_bytes, Path.unlink
    def write(path, data):
        if path.name == fault and fault != 'INCOMPLETE.txt':
            raise OSError('injected local persistence failure')
        return original_write(path, data)
    def unlink(path, *args, **kwargs):
        if path.name == fault:
            raise OSError('injected marker failure')
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_bytes', write)
    monkeypatch.setattr(Path, 'unlink', unlink)
    with FormalPeer() as peer:
        result = session.run_formal(assets()[2], local_config(peer), tmp_path, state_dir=tmp_path/'state')
        assert sum(row[0] == 7 for row in peer.requests) == 1
    assert result.report['complete'] is False
    assert result.report['hardware_finish_confirmed'] is True
    assert result.report['local_artifacts_complete'] is False
    assert result.report['manual_recovery_required'] is False
    assert (result.directory/'INCOMPLETE.txt').exists()
    disk = json.loads((result.directory/'report.json').read_text(encoding='utf-8'))
    assert disk['complete'] is False and disk['error']
    monkeypatch.setattr(formal_cli, 'run_formal', lambda *_a, **_k: result)
    assert formal_cli.main(['run']) == 1


@pytest.mark.parametrize('fault', ['profile.json', 'report.json', 'short_mask'])
def test_all_artifact_commit_failures_are_fail_closed(tmp_path, monkeypatch, fault):
    original_json, original_bytes = session.write_json, Path.write_bytes
    def write_json(path, value):
        if Path(path).name == fault and (fault != 'report.json' or value['complete']):
            raise session.LocalPersistenceError('injected final JSON save failure')
        return original_json(path, value)
    def short_write(path, data):
        if path.name == 'mask.raw' and fault == 'short_mask':
            original_bytes(path, data[:1024])
            return 1024
        return original_bytes(path, data)
    monkeypatch.setattr(session, 'write_json', write_json)
    monkeypatch.setattr(Path, 'write_bytes', short_write)
    with FormalPeer() as peer:
        outcome = session.run_formal(assets()[2], local_config(peer), tmp_path, state_dir=tmp_path/'state')
    assert outcome.report['complete'] is False
    assert outcome.report['hardware_finish_confirmed'] is True
    assert outcome.report['manual_recovery_required'] is False
    assert outcome.report['failure_domain'] == 'local_storage'
    assert (outcome.directory/'INCOMPLETE.txt').exists()
    assert json.loads((outcome.directory/'report.json').read_text())['complete'] is False


def test_atomic_json_failure_keeps_previous_incomplete_report(tmp_path, monkeypatch):
    target = tmp_path/'report.json'
    session.write_json(target, {'complete': False})
    monkeypatch.setattr(session.os, 'replace', lambda *_a: (_ for _ in ()).throw(OSError('disk unavailable')))
    with pytest.raises(session.LocalPersistenceError):
        session.write_json(target, {'complete': True})
    assert json.loads(target.read_text()) == {'complete': False}
    assert list(tmp_path.iterdir()) == [target]
