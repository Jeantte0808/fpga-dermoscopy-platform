def test_teammate_four_real_frames_and_record_verified_offline(tmp_path, monkeypatch):
    import socket
    import pytest
    from scripts.verify_v070_acceptance import verify
    monkeypatch.setattr(socket, 'socket', lambda *_a, **_k: pytest.fail('acceptance audit opened network'))
    report = verify(tmp_path / 'audit')
    assert report['status'] == 'PASS' and report['SHA_entries'] == 64
    assert len(report['cases']) == 4 and report['saved_record_reopened']
    assert report['network_used'] is report['state_imported'] is False
