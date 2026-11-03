from io import BytesIO
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
from PIL import Image
import pytest

from dermademo import isic2016
from dermademo.batch import BatchOptions, run_batch
from dermademo.storage import file_sha256


def encoded_pair(index):
    image = np.full((24, 32, 3), 180 + index, dtype=np.uint8)
    mask = np.zeros((24, 32), dtype=np.uint8)
    mask[3:8 + index, 5:13 + index] = 255
    image[mask != 0] = 35 + index
    original, annotation = BytesIO(), BytesIO()
    Image.fromarray(image).save(original, format="JPEG")
    Image.fromarray(mask).save(annotation, format="PNG")
    return original.getvalue(), annotation.getvalue()


@pytest.fixture
def packages(tmp_path):
    directory = tmp_path / "中文压缩包"
    directory.mkdir()
    for key, filename in isic2016.PACKAGES.items():
        indexes = range(10) if key.startswith("training") else range(10, 14)
        with ZipFile(directory / filename, "w") as archive:
            for index in indexes:
                pair = encoded_pair(index)
                suffix = "_Segmentation.png" if key.endswith("masks") else ".jpg"
                archive.writestr(f"official-folder/ISIC_{index:07d}{suffix}", pair[key.endswith("masks")])
    return directory


def prepare(packages, output, **kwargs):
    return isic2016.prepare_dataset(packages, output, expected_counts={"training": 10, "test": 4},
                                   demo_count=2, progress=lambda _: None, **kwargs)


def rewrite_archive(path, transform):
    with ZipFile(path) as archive:
        entries = [(info.filename, archive.read(info)) for info in archive.infolist()]
    with ZipFile(path, "w") as archive:
        for name, data in transform(entries):
            archive.writestr(name, data)


def test_prepared_data_preserves_bytes_and_is_usable_by_real_batch(packages, tmp_path):
    output = tmp_path / "中文数据"
    manifest = prepare(packages, output)
    assert manifest["counts"] == {"train": 8, "validation": 2, "test": 4, "excluded_test_duplicate": 0}
    assert len(manifest["demo"]["ids"]) == 2
    assert not (output / "INCOMPLETE.txt").exists()
    assert json.loads((output / "manifest.json").read_text(encoding="utf-8")) == manifest
    groups = {split: {item["id"] for item in manifest["items"] if item["split"] == split}
              for split in ("train", "validation", "test")}
    assert not groups["train"] & groups["validation"]
    assert not groups["train"] & groups["test"]
    assert set(manifest["demo"]["ids"]) <= groups["validation"]
    for item in manifest["items"]:
        assert file_sha256(output / item["image"]) == item["image_sha256"]
        assert file_sha256(output / item["label"]) == item["mask_sha256"]
        with ZipFile(packages / isic2016.PACKAGES[item["source_partition"] + "_masks"]) as archive:
            assert (output / item["label"]).read_bytes() == archive.read(item["source_mask_member"])
    for image_id in manifest["demo"]["ids"]:
        assert (output / "demo/images" / f"{image_id}.jpg").read_bytes() == (output / "validation/images" / f"{image_id}.jpg").read_bytes()
    outcome = run_batch(BatchOptions(output / "demo/images", tmp_path / "reports",
                                    labels_dir=output / "demo/labels", warmup=0, repeat=2))
    assert outcome.exit_code == 0
    assert outcome.report["accuracy"]["count"] == 2
    assert all(not item["is_synthetic"] and item["results_stable"] for item in outcome.report["items"])


def test_splits_and_demo_are_reproducible_independent_of_zip_member_order(packages, tmp_path):
    first = prepare(packages, tmp_path / "one")
    for path in packages.iterdir():
        rewrite_archive(path, lambda entries: reversed(entries))
    second = prepare(packages, tmp_path / "two")
    assert first["items"] == second["items"]
    assert first["demo"] == second["demo"]
    assert first["split_method"] == second["split_method"]
    other = isic2016.split_training([item["id"] for item in first["items"] if item["source_partition"] == "training"],
                                    seed="other", validation_fraction=0.2)
    assert other != {item["id"]: item["split"] for item in first["items"] if item["source_partition"] == "training"}


@pytest.mark.parametrize("bad_name", ["../ISIC_0000000.jpg", "C:/ISIC_0000000.jpg", "/ISIC_0000000.jpg",
                                    "other-folder/ISIC_0000000.jpg", "unknown.txt"])
def test_unexpected_unsafe_or_duplicate_members_are_rejected(packages, tmp_path, bad_name):
    path = packages / isic2016.PACKAGES["training_images"]
    with ZipFile(path, "a") as archive:
        archive.writestr(bad_name, encoded_pair(0)[0])
    output = tmp_path / "rejected"
    with pytest.raises(ValueError):
        prepare(packages, output)
    assert not output.exists()
    assert not (tmp_path / "ISIC_0000000.jpg").exists()


def test_missing_pair_and_wrong_count_are_rejected(packages, tmp_path):
    path = packages / isic2016.PACKAGES["training_masks"]
    rewrite_archive(path, lambda entries: entries[1:])
    with pytest.raises(ValueError, match="pair"):
        prepare(packages, tmp_path / "missing")
    rewrite_archive(packages / isic2016.PACKAGES["training_images"], lambda entries: entries[1:])
    with pytest.raises(ValueError, match="count"):
        isic2016.prepare_dataset(packages, tmp_path / "count", demo_count=2)


def test_overlapping_train_test_ids_are_rejected(packages, tmp_path):
    for key in ("test_images", "test_masks"):
        rewrite_archive(packages / isic2016.PACKAGES[key],
                        lambda entries: [(name.replace("ISIC_0000010", "ISIC_0000000"), data) for name, data in entries])
    with pytest.raises(ValueError, match="IDs overlap"):
        prepare(packages, tmp_path / "overlap")


@pytest.mark.parametrize("mode,size,value", [("RGB", (32, 24), 255), ("L", (32, 24), 1), ("L", (16, 12), 255)])
def test_bad_masks_leave_marked_output_without_publishing(packages, tmp_path, mode, size, value):
    mask = BytesIO()
    Image.new(mode, size, value).save(mask, format="PNG")
    rewrite_archive(packages / isic2016.PACKAGES["training_masks"],
                    lambda entries: [(name, mask.getvalue()) if index == 0 else (name, data)
                                     for index, (name, data) in enumerate(entries)])
    output = tmp_path / "broken"
    with pytest.raises(ValueError, match="ISIC_0000000"):
        prepare(packages, output)
    assert not output.exists()
    partials = list(tmp_path.glob("broken.preparing-*"))
    assert len(partials) == 1 and (partials[0] / "INCOMPLETE.txt").exists()
    assert not (partials[0] / "manifest.json").exists()


def test_nontrivial_exif_orientation_is_rejected_instead_of_misalignment():
    stream = BytesIO()
    image = Image.new("RGB", (32, 24))
    exif = Image.Exif()
    exif[274] = 6
    image.save(stream, format="JPEG", exif=exif)
    with pytest.raises(ValueError, match="orientation"):
        isic2016.validate_pair(stream.getvalue(), encoded_pair(1)[1])


def test_official_training_test_content_overlap_is_quarantined(packages, tmp_path):
    duplicate = encoded_pair(0)
    for key in ("test_images", "test_masks"):
        rewrite_archive(packages / isic2016.PACKAGES[key],
                        lambda entries, mask=key.endswith("masks"): [(name, duplicate[mask]) if index == 0 else (name, data)
                                for index, (name, data) in enumerate(entries)])
    output = tmp_path / "duplicate"
    manifest = prepare(packages, output)
    assert manifest["counts"] == {"train": 7, "validation": 2, "test": 4, "excluded_test_duplicate": 1}
    by_id = {item["id"]: item for item in manifest["items"]}
    assert by_id["ISIC_0000000"]["split"] == "excluded_test_duplicate"
    assert by_id["ISIC_0000010"]["split"] == "test"
    assert len(manifest["duplicate_content_groups"]) == 1
    assert (output / by_id["ISIC_0000000"]["image"]).read_bytes() == duplicate[0]


def test_existing_outputs_are_never_overwritten(packages, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    (output / "my.txt").write_text("keep")
    with pytest.raises(ValueError, match="already exists"):
        prepare(packages, output)
    assert (output / "my.txt").read_text() == "keep"


@pytest.mark.parametrize("fraction", [0, 1, -0.1, float("nan"), 0.01])
def test_invalid_or_empty_splits_are_rejected(fraction):
    with pytest.raises(ValueError):
        isic2016.split_training(["one", "two"], seed="fixed", validation_fraction=fraction)


def test_demo_selection_covers_lesion_fraction_extremes_without_using_predictions():
    candidates = [{"id": str(index), "split": "validation", "lesion_fraction": index / 10} for index in range(10)]
    candidates.append({"id": "held_out_test", "split": "test", "lesion_fraction": 0.5})
    assert isic2016.select_demo(candidates, 3) == ["0", "4", "9"]
    assert len(set(isic2016.select_demo(candidates, 10))) == 10
    with pytest.raises(ValueError):
        isic2016.select_demo(candidates, 11)


def test_training_content_duplicates_stay_together_and_demo_has_no_duplicate_originals(packages, tmp_path):
    for key in ("training_images", "training_masks"):
        rewrite_archive(packages / isic2016.PACKAGES[key],
                        lambda entries, masks=key.endswith("masks"): [(name, encoded_pair(0)[masks]) if index == 1 else (name, data)
                                     for index, (name, data) in enumerate(entries)])
    manifest = prepare(packages, tmp_path / "grouped")
    by_id = {item["id"]: item for item in manifest["items"]}
    assert by_id["ISIC_0000000"]["split"] == by_id["ISIC_0000001"]["split"]
    assert len(manifest["duplicate_content_groups"]) == 1
    hashes = [by_id[image_id]["image_sha256"] for image_id in manifest["demo"]["ids"]]
    assert len(set(hashes)) == len(hashes)
    candidates = [{"id": "a", "split": "validation", "image_sha256": "same", "lesion_fraction": 0.1},
                  {"id": "b", "split": "validation", "image_sha256": "same", "lesion_fraction": 0.2},
                  {"id": "c", "split": "validation", "image_sha256": "different", "lesion_fraction": 0.3}]
    assert isic2016.select_demo(candidates, 2) == ["a", "c"]


class Response(BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers if headers is not None else {"Content-Length": str(len(data))}


@pytest.mark.parametrize("range_supported", [True, False])
def test_download_resumes_or_restarts_and_then_uses_cache(packages, tmp_path, monkeypatch, range_supported):
    filename = isic2016.PACKAGES["training_images"]
    monkeypatch.setattr(isic2016, "PACKAGES", {"training_images": filename})
    data = (packages / filename).read_bytes()
    directory = tmp_path / "download"
    directory.mkdir()
    (directory / (filename + ".part")).write_bytes(data[:100])
    calls = []
    def opened(request, timeout):
        calls.append(request)
        assert request.full_url == isic2016.ARCHIVE_BASE + filename
        assert request.headers["Range"] == "bytes=100-"
        if range_supported:
            return Response(data[100:], 206, {"Content-Range": f"bytes 100-{len(data)-1}/{len(data)}"})
        return Response(data)
    monkeypatch.setattr(isic2016, "urlopen", opened)
    isic2016.download_archives(directory, progress=lambda _: None)
    assert (directory / filename).read_bytes() == data
    assert not (directory / (filename + ".part")).exists()
    isic2016.download_archives(directory, progress=lambda _: None)
    assert len(calls) == 1


def test_failed_download_retains_partial_and_never_publishes_zip(packages, tmp_path, monkeypatch):
    filename = isic2016.PACKAGES["training_images"]
    monkeypatch.setattr(isic2016, "PACKAGES", {"training_images": filename})
    monkeypatch.setattr(isic2016, "sleep", lambda _: None)
    def opened(request, timeout):
        return Response(b"partial", headers={"Content-Length": "100"})
    monkeypatch.setattr(isic2016, "urlopen", opened)
    with pytest.raises(OSError, match="Incomplete"):
        isic2016.download_archives(tmp_path / "download", progress=lambda _: None)
    assert not (tmp_path / "download" / filename).exists()
    assert (tmp_path / "download" / (filename + ".part")).read_bytes() == b"partial"


def test_cli_missing_archives_returns_failure_without_creating_data(tmp_path, capsys):
    assert isic2016.main(["--archives", str(tmp_path / "missing"), "--output", str(tmp_path / "out")]) == 2
    assert "ISIC" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_completed_single_download_is_recovered_after_interrupted_rename(packages, tmp_path, monkeypatch):
    filename = isic2016.PACKAGES["training_images"]
    monkeypatch.setattr(isic2016, "PACKAGES", {"training_images": filename})
    directory = tmp_path / "cached"
    directory.mkdir()
    data = (packages / filename).read_bytes()
    (directory / (filename + ".part")).write_bytes(data)
    monkeypatch.setattr(isic2016, "urlopen", lambda *args, **kwargs: pytest.fail("Should recover completed ZIP offline"))
    isic2016.download_archives(directory, progress=lambda _: None)
    assert (directory / filename).read_bytes() == data


def test_segmented_download_pins_version_resumes_chunks_and_assembles_in_order(packages, tmp_path, monkeypatch):
    data = (packages / isic2016.PACKAGES["training_masks"]).read_bytes()
    url, etag, chunk_size = "https://official.example/data.zip", '"version-1"', 1000
    target = tmp_path / "download.zip"
    identity = sha256(f"{url}:{len(data)}:{etag}".encode()).hexdigest()[:12]
    cache = tmp_path / f"download.zip.chunks-{identity}"
    cache.mkdir()
    (cache / "00000.bin").write_bytes(data[:chunk_size])
    requests = []
    def opened(request, timeout):
        if request.get_method() == "HEAD":
            return Response(b"", headers={"Content-Length": str(len(data)), "ETag": etag, "Accept-Ranges": "bytes"})
        assert request.get_header("If-match") == etag
        start, end = map(int, request.get_header("Range").removeprefix("bytes=").split("-"))
        requests.append((start, end))
        return Response(data[start:end+1], 206, {"ETag": etag, "Content-Range": f"bytes {start}-{end}/{len(data)}"})
    monkeypatch.setattr(isic2016, "urlopen", opened)
    isic2016.download_segmented(url, target, connections=3, chunk_size=chunk_size, progress=lambda _: None)
    assert target.read_bytes() == data
    assert all(start >= chunk_size for start, _ in requests)
    assert not cache.exists() and not target.with_suffix(".zip.assembled").exists()


@pytest.mark.parametrize("fault", ["range", "etag", "short"])
def test_segmented_download_never_publishes_inconsistent_or_short_responses(packages, tmp_path, monkeypatch, fault):
    data = (packages / isic2016.PACKAGES["training_masks"]).read_bytes()
    etag = '"version-1"'
    monkeypatch.setattr(isic2016, "sleep", lambda _: None)
    def opened(request, timeout):
        if request.get_method() == "HEAD":
            return Response(b"", headers={"Content-Length": str(len(data)), "ETag": etag, "Accept-Ranges": "bytes"})
        content_range = f"bytes 0-{len(data)-1}/{len(data)}" if fault != "range" else "bytes 1-2/3"
        return Response(data[:-10] if fault == "short" else data, 206,
                        {"ETag": etag if fault != "etag" else '"changed"', "Content-Range": content_range})
    monkeypatch.setattr(isic2016, "urlopen", opened)
    target = tmp_path / "download.zip"
    with pytest.raises((ValueError, OSError)):
        isic2016.download_segmented("https://official.example/data.zip", target, connections=2,
                                  chunk_size=len(data), progress=lambda _: None)
    assert not target.exists()
