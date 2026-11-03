"""Validate preserved evidence and reject self-consistent but incorrect RGB results."""

from hashlib import sha256
from io import BytesIO
import json
import zipfile
import zlib

import numpy as np
from PIL import Image
import pytest

from scripts.verify_rgb_acceptance import DEFAULT_ARCHIVE, PREFIX, audit, reference_gaussian


def rewrite(tmp_path, change):
    with zipfile.ZipFile(DEFAULT_ARCHIVE) as original:
        data = {name[len(PREFIX):]: original.read(name) for name in original.namelist()}
    change(data)
    data["SHA256.json"] = json.dumps({name: {"bytes": len(raw), "sha256": sha256(raw).hexdigest()}
                                      for name, raw in data.items() if name != "SHA256.json"}).encode()
    target = tmp_path / "changed.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, raw in data.items():
            bundle.writestr(PREFIX + name, raw)
    return target


def test_preserved_archive_and_committed_audit_are_reproducible():
    result = audit(DEFAULT_ARCHIVE)
    assert result == json.loads(DEFAULT_ARCHIVE.with_name("AUDIT.json").read_text(encoding="utf-8"))
    imported = json.loads(DEFAULT_ARCHIVE.with_name("IMPORT.json").read_text(encoding="utf-8"))
    assert result["archive_sha256"] == imported["archive_sha256"]
    assert result["archive_sha256"] == "7e20d8ec603ca160486eff293da640388b96031c6e37d6f25d59e574620f298a"
    assert result["inventory_files_verified"] == 18
    assert [r["mode"] for r in result["captures"]] == ["bypass", "gaussian", "bypass"]
    assert all(result[k] is False for k in ("cnn_verified", "mask_verified", "fps_verified", "clean_machine_verified"))


def test_gaussian_independent_reference_channel_and_edge_rounding():
    image = np.zeros((256, 256, 3), np.uint8)
    image[0, 0] = [255, 16, 1]
    actual = np.frombuffer(reference_gaussian(image.tobytes()), np.uint8).reshape(image.shape)
    assert actual[0, 0].tolist() == [143, 9, 0]  # replicated corner weight 9/16
    assert actual[0, 1].tolist() == [47, 3, 0]
    assert actual[1, 1].tolist() == [15, 1, 0]
    assert not actual[2:].any()


def test_audit_rejects_wrong_gaussian_even_with_updated_raw_png_and_checksums(tmp_path):
    def change(data):
        report_name = next(n for n in data if n.endswith('/report.json') and b'gaussian' in data[n])
        folder = report_name.rsplit('/', 1)[0]
        report = json.loads(data[report_name])
        wrong = bytearray(data[folder + '/returned.rgb'])
        wrong[0] ^= 1
        data[folder + '/returned.rgb'] = bytes(wrong)
        buffer = BytesIO()
        Image.frombytes('RGB', (256, 256), bytes(wrong)).save(buffer, format='PNG')
        data[folder + '/returned.png'] = buffer.getvalue()
        report['output_crc32'] = report['advertised_crc32'] = f'{zlib.crc32(wrong):08X}'
        report['output_sha256'] = sha256(wrong).hexdigest()
        for name in report['files']:
            raw = data[folder + '/' + name]
            report['files'][name] = {'bytes': len(raw), 'sha256': sha256(raw).hexdigest()}
        data[report_name] = json.dumps(report).encode()
    with pytest.raises(ValueError, match='Independent gaussian reference mismatch'):
        audit(rewrite(tmp_path, change))


def test_audit_rejects_false_zero_retry_claim(tmp_path):
    def change(data):
        name = next(n for n in data if n.endswith('/report.json'))
        report = json.loads(data[name])
        report['retransmissions'] = 1
        data[name] = json.dumps(report).encode()
    with pytest.raises(ValueError, match='Counter mismatch: retransmissions'):
        audit(rewrite(tmp_path, change))


def test_audit_rejects_missing_inventory_entry(tmp_path):
    path = rewrite(tmp_path, lambda data: data.update({'unlisted.txt': b'extra'}))
    with zipfile.ZipFile(path) as bundle:
        data = {n: bundle.read(n) for n in bundle.namelist()}
    manifest = json.loads(data[PREFIX + 'SHA256.json'])
    del manifest['unlisted.txt']
    data[PREFIX + 'SHA256.json'] = json.dumps(manifest).encode()
    changed = tmp_path / 'missing-inventory.zip'
    with zipfile.ZipFile(changed, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name, raw in data.items():
            bundle.writestr(name, raw)
    with pytest.raises(ValueError, match='Inventory does not cover archive'):
        audit(changed)
