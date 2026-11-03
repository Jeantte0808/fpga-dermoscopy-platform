"""Prepare official ISIC 2016 Task 1 data without changing pixels or GUI state."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
from time import sleep
from urllib.request import Request, urlopen
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

import numpy as np
from PIL import Image

from .imaging import MAX_IMAGE_PIXELS
from .storage import file_sha256
from .diagnostic_logging import entry_session, record_exception


SOURCE_PAGE = "https://challenge.isic-archive.com/data/#2016"
ARCHIVE_BASE = "https://isic-archive.s3.amazonaws.com/challenges/2016/"
PACKAGES = {
    "training_images": "ISBI2016_ISIC_Part1_Training_Data.zip",
    "training_masks": "ISBI2016_ISIC_Part1_Training_GroundTruth.zip",
    "test_images": "ISBI2016_ISIC_Part1_Test_Data.zip",
    "test_masks": "ISBI2016_ISIC_Part1_Test_GroundTruth.zip",
}
EXPECTED_COUNTS = {"training": 900, "test": 379}
DEFAULT_SEED = "alien-home-isic2016-v1"
CITATION = ('Gutman et al. Skin Lesion Analysis toward Melanoma Detection: '
            'A Challenge at ISBI 2016, hosted by ISIC. arXiv:1605.01397 (2016).')


def download_segmented(url: str, target: Path, *, connections: int, progress=print,
                       chunk_size: int = 8 * 1024 ** 2) -> None:
    """Pin the S3 version via ETag; resume independently completed ranges."""
    with urlopen(Request(url, method="HEAD"), timeout=60) as response:
        total = int(response.headers["Content-Length"])
        etag = response.headers["ETag"]
        if response.headers.get("Accept-Ranges") != "bytes" or total <= 0:
            raise ValueError("Server does not support fixed byte ranges; use --connections 1")
    identity = sha256(f"{url}:{total}:{etag}".encode()).hexdigest()[:12]
    cache = target.parent / (target.name + f".chunks-{identity}")
    cache.mkdir(parents=True, exist_ok=True)
    ranges = [(index, start, min(start + chunk_size, total) - 1)
              for index, start in enumerate(range(0, total, chunk_size))]

    def fetch(part):
        index, start, end = part
        complete = cache / f"{index:05d}.bin"
        if complete.exists() and complete.stat().st_size == end - start + 1:
            return complete
        partial = cache / f"{index:05d}.part"
        for attempt in range(3):
            try:
                headers = {"Range": f"bytes={start}-{end}", "If-Match": etag}
                with urlopen(Request(url, headers=headers), timeout=60) as response:
                    expected_range = f"bytes {start}-{end}/{total}"
                    if (response.status != 206 or response.headers.get("Content-Range") != expected_range
                            or response.headers.get("ETag") != etag):
                        raise ValueError("Server returned a different range/version")
                    remaining = end - start + 1
                    with partial.open("wb") as stream:
                        while remaining:
                            data = response.read(min(256 * 1024, remaining))
                            if not data:
                                raise OSError("Incomplete range response")
                            stream.write(data)
                            remaining -= len(data)
                        if response.read(1):
                            raise ValueError("Range response longer than requested")
                partial.replace(complete)
                return complete
            except OSError:
                if attempt == 2:
                    raise
                sleep(attempt + 1)

    with ThreadPoolExecutor(max_workers=connections) as pool:
        futures = [pool.submit(fetch, part) for part in ranges]
        for count, future in enumerate(as_completed(futures), 1):
            future.result()
            progress(f"Downloaded ranges {target.name}: {count}/{len(ranges)}")
    assembled = target.with_suffix(".zip.assembled")
    with assembled.open("wb") as stream:
        for index, _, _ in ranges:
            with (cache / f"{index:05d}.bin").open("rb") as part:
                shutil.copyfileobj(part, stream)
    with ZipFile(assembled) as archive:
        if bad := archive.testzip():
            raise ValueError(f"Archive CRC failed: {bad}; remove range cache and retry")
    assembled.rename(target)
    # Remove only this downloader's exact temporary files, without recursive deletion.
    for index, _, _ in ranges:
        (cache / f"{index:05d}.bin").unlink()
    cache.rmdir()
    old_partial = target.with_suffix(".zip.part")
    old_partial.unlink(missing_ok=True)


def download_archives(directory: Path, *, connections: int = 1, progress=print) -> None:
    """Download only the four official packages; interrupted downloads can resume."""
    if type(connections) is not int or not 1 <= connections <= 8:
        raise ValueError("Connections must be between 1 and 8")
    directory.mkdir(parents=True, exist_ok=True)
    for filename in PACKAGES.values():
        target = directory / filename
        if target.exists():
            # Full content/CRC and item validation happens in prepare_dataset.
            with ZipFile(target):
                pass
            progress(f"Cached: {filename}")
            continue
        if connections > 1:
            download_segmented(ARCHIVE_BASE + filename, target, connections=connections, progress=progress)
            continue
        partial = target.with_suffix(".zip.part")
        # A download can be interrupted after the final byte, before rename.
        if partial.exists():
            try:
                with ZipFile(partial) as archive:
                    complete_download = archive.testzip() is None
                if complete_download:
                    partial.rename(target)
                    progress(f"Recovered completed download: {filename}")
                    continue
            except BadZipFile:
                pass
        for attempt in range(3):
            try:
                offset = partial.stat().st_size if partial.exists() else 0
                headers = {"User-Agent": "AlienHome-DermaDemo/0.4 ISIC-data-preparation"}
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                with urlopen(Request(ARCHIVE_BASE + filename, headers=headers), timeout=60) as response:
                    if response.status == 206:
                        content_range = response.headers.get("Content-Range", "")
                        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                        if not match or int(match[1]) != offset:
                            raise ValueError("Unexpected download range")
                        expected_size = int(match[3])
                        mode = "ab"
                    elif response.status == 200:
                        offset = 0
                        mode = "wb"
                        length = response.headers.get("Content-Length")
                        expected_size = int(length) if length else None
                    else:
                        raise ValueError(f"Unexpected HTTP status: {response.status}")
                    next_update = offset
                    with partial.open(mode) as stream:
                        while chunk := response.read(1024 * 1024):
                            stream.write(chunk)
                            offset += len(chunk)
                            if offset >= next_update:
                                progress(f"Downloading {filename}: {offset / 1024 ** 2:.1f} MiB")
                                next_update = offset + 32 * 1024 ** 2
                if expected_size is not None and partial.stat().st_size != expected_size:
                    raise OSError("Incomplete download; rerun to resume")
                with ZipFile(partial) as archive:
                    bad = archive.testzip()
                    if bad:
                        raise ValueError(f"Archive CRC failed: {bad}")
                partial.rename(target)
                progress(f"Downloaded: {filename}")
                break
            except OSError:
                if attempt == 2:
                    raise
                sleep(attempt + 1)


def index_archive(archive: ZipFile, *, masks: bool) -> dict:
    """Read allowed members by ID, never extract caller-provided paths."""
    pattern = re.compile(r"(ISIC_\d{7})_Segmentation\.png" if masks else r"(ISIC_\d{7})\.jpg")
    indexed = {}
    for member in archive.infolist():
        path = PurePosixPath(member.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or any(":" in part for part in path.parts):
            raise ValueError(f"Unsafe archive member: {member.filename}")
        if member.is_dir() or "__MACOSX" in path.parts or path.name.startswith("."):
            continue
        match = pattern.fullmatch(path.name)
        if not match:
            raise ValueError(f"Unexpected archive member: {member.filename}")
        image_id = match[1]
        if image_id in indexed:
            raise ValueError(f"Duplicate image ID: {image_id}")
        if not 0 < member.file_size <= 100 * 1024 ** 2:
            raise ValueError(f"Invalid member size: {member.filename}")
        indexed[image_id] = member
    return indexed


def split_training(ids: list[str], *, seed: str, validation_fraction: float) -> dict[str, str]:
    if not 0 < validation_fraction < 1:
        raise ValueError("Validation fraction must be between 0 and 1")
    ordered = sorted(ids, key=lambda image_id: (sha256(f"{seed}:{image_id}".encode()).hexdigest(), image_id))
    count = round(len(ids) * validation_fraction)
    if not 0 < count < len(ids):
        raise ValueError("Training and validation sets must both be nonempty")
    validation = set(ordered[:count])
    return {image_id: "validation" if image_id in validation else "train" for image_id in ids}


def select_demo(items: list[dict], count: int) -> list[str]:
    """Evenly spaced lesion-coverage ranks; no picking by predicted accuracy."""
    unique = {}
    for item in sorted(items, key=lambda item: item["id"]):
        if item["split"] == "validation":
            unique.setdefault(item.get("image_sha256", item["id"]), item)
    candidates = sorted(unique.values(), key=lambda item: (item["lesion_fraction"], item["id"]))
    if type(count) is not int or not 1 <= count <= len(candidates):
        raise ValueError("Gallery count must fit within the validation set")
    indexes = [round(index * (len(candidates) - 1) / (count - 1)) for index in range(count)] if count > 1 else [len(candidates) // 2]
    return [candidates[index]["id"] for index in indexes]


def split_by_content(items: list[dict], *, seed: str, validation_fraction: float) -> tuple[dict, list]:
    """Quarantine official training/test overlap; keep duplicate originals together."""
    if not 0 < validation_fraction < 1:
        raise ValueError("Validation fraction must be between 0 and 1")
    all_groups = {}
    for item in items:
        all_groups.setdefault(item["image_sha256"], []).append(item)
    test_hashes = {item["image_sha256"] for item in items if item["source_partition"] == "test"}
    assignments, eligible = {}, {}
    for item in items:
        if item["source_partition"] == "test":
            assignments[item["id"]] = "test"
        elif item["image_sha256"] in test_hashes:
            assignments[item["id"]] = "excluded_test_duplicate"
        else:
            eligible.setdefault(item["image_sha256"], []).append(item["id"])
    groups = [sorted(ids) for ids in eligible.values()]
    groups.sort(key=lambda ids: (sha256(f"{seed}:{ids[0]}".encode()).hexdigest(), ids[0]))
    count = sum(map(len, groups))
    target = round(count * validation_fraction)
    selected = 0
    for ids in groups:
        # Whole groups only, never exceed the requested validation target.
        split = "validation" if selected + len(ids) <= target else "train"
        if split == "validation":
            selected += len(ids)
        assignments.update({image_id: split for image_id in ids})
    if not 0 < selected < count:
        raise ValueError("Cannot create nonempty train/validation sets without separating duplicate images")
    duplicates = [{"image_sha256": digest, "members": [
        {"id": item["id"], "source_partition": item["source_partition"], "split": assignments[item["id"]],
         "mask_sha256": item["mask_sha256"]} for item in sorted(group, key=lambda item: item["id"])]}
        for digest, group in sorted(all_groups.items()) if len(group) > 1]
    return assignments, duplicates


def validate_pair(image_bytes: bytes, mask_bytes: bytes) -> tuple[int, int, int]:
    with Image.open(BytesIO(image_bytes)) as image:
        if image.format != "JPEG" or image.mode != "RGB":
            raise ValueError("Original must be an RGB JPEG")
        if image.getexif().get(274, 1) != 1:
            raise ValueError("Unexpected EXIF orientation; do not silently rotate only the original")
        width, height = image.size
        if not 0 < width * height <= MAX_IMAGE_PIXELS:
            raise ValueError("Image exceeds the supported pixel limit")
        image.load()
    with Image.open(BytesIO(mask_bytes)) as mask:
        if mask.format != "PNG" or mask.mode != "L" or mask.size != (width, height):
            raise ValueError("Mask must be a same-size, single-channel PNG")
        values = np.asarray(mask)
        if not np.isin(values, (0, 255)).all():
            raise ValueError("Mask must contain only 0 and 255")
        area = int(np.count_nonzero(values))
    return width, height, area


def prepare_dataset(archive_dir: Path, destination: Path, *, seed: str = DEFAULT_SEED,
                    validation_fraction: float = 0.2, demo_count: int = 30,
                    expected_counts: dict | None = None, progress=print) -> dict:
    """Create a new dataset only; leave incomplete outputs marked on failure."""
    archive_dir, destination = archive_dir.resolve(), destination.resolve()
    if destination.exists():
        raise ValueError("Destination already exists; choose a new directory (no overwrite)")
    if not seed or type(demo_count) is not int or demo_count < 1:
        raise ValueError("Seed and demo count are required")
    counts = EXPECTED_COUNTS if expected_counts is None else expected_counts
    with ExitStack() as stack:
        archives = {key: stack.enter_context(ZipFile(archive_dir / filename)) for key, filename in PACKAGES.items()}
        indexes = {key: index_archive(archive, masks=key.endswith("masks")) for key, archive in archives.items()}
        for partition in ("training", "test"):
            ids = set(indexes[f"{partition}_images"])
            if ids != set(indexes[f"{partition}_masks"]):
                raise ValueError(f"Images and masks do not pair exactly: {partition}")
            if len(ids) != counts[partition]:
                raise ValueError(f"Unexpected {partition} count: {len(ids)} (expected {counts[partition]})")
        if set(indexes["training_images"]) & set(indexes["test_images"]):
            raise ValueError("Training/test image IDs overlap")
        splits = split_training(list(indexes["training_images"]), seed=seed, validation_fraction=validation_fraction)
        if demo_count > sum(value == "validation" for value in splits.values()):
            raise ValueError("Gallery count exceeds validation count")
        destination.parent.mkdir(parents=True, exist_ok=True)
        working = destination.parent / f"{destination.name}.preparing-{uuid4().hex[:8]}"
        working.mkdir()
        marker = working / "INCOMPLETE.txt"
        marker.write_text("Dataset preparation incomplete. Do not use for evaluation.\n", encoding="utf-8")
        items = []
        # Each member is read through ZipFile, including CRC verification.
        for partition in ("training", "test"):
            for image_id in sorted(indexes[f"{partition}_images"]):
                image_member = indexes[f"{partition}_images"][image_id]
                mask_member = indexes[f"{partition}_masks"][image_id]
                image_bytes = archives[f"{partition}_images"].read(image_member)
                mask_bytes = archives[f"{partition}_masks"].read(mask_member)
                try:
                    width, height, area = validate_pair(image_bytes, mask_bytes)
                except Exception as exc:
                    raise ValueError(f"{image_id}: {exc}; incomplete output: {working}") from exc
                # Validate all source data before assigning duplicate-safe splits.
                split = partition
                image_path = Path(split) / "images" / f"{image_id}.jpg"
                mask_path = Path(split) / "labels" / f"{image_id}.jpg.mask.png"
                for relative, data in ((image_path, image_bytes), (mask_path, mask_bytes)):
                    target = working / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                items.append({"id": image_id, "source_partition": partition, "split": split,
                    "image": image_path.as_posix(), "label": mask_path.as_posix(),
                    "source_image_member": image_member.filename, "source_mask_member": mask_member.filename,
                    "image_sha256": sha256(image_bytes).hexdigest(), "mask_sha256": sha256(mask_bytes).hexdigest(),
                    "width": width, "height": height, "area_px2": area,
                    "lesion_fraction": area / (width * height)})
                if len(items) % 50 == 0:
                    progress(f"Validated {len(items)} image/mask pairs")
        assignments, duplicates = split_by_content(items, seed=seed, validation_fraction=validation_fraction)
        for item in items:
            split = assignments[item["id"]]
            for key, folder in (("image", "images"), ("label", "labels")):
                old_path = Path(item[key])
                new_path = Path(split) / folder / old_path.name
                if old_path != new_path:
                    (working / new_path).parent.mkdir(parents=True, exist_ok=True)
                    (working / old_path).rename(working / new_path)
                    item[key] = new_path.as_posix()
            item["split"] = split
        # Remove only the two known empty staging folders, not recursively.
        for folder in ("images", "labels"):
            (working / "training" / folder).rmdir()
        (working / "training").rmdir()
        demo_ids = select_demo(items, demo_count)
        for item in items:
            if item["id"] in demo_ids:
                for key, folder in (("image", "images"), ("label", "labels")):
                    target = working / "demo" / folder / Path(item[key]).name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(working / item[key], target)
        manifest = {
            "schema_version": "1.0", "dataset": "ISIC 2016 Task 1", "source": SOURCE_PAGE,
            "license": "CC0 (as listed on the official challenge download page)", "citation": CITATION,
            "prepared_at": datetime.now().astimezone().isoformat(),
            "archives": {key: {"filename": filename, "url": ARCHIVE_BASE + filename,
                               "sha256": file_sha256(archive_dir / filename)} for key, filename in PACKAGES.items()},
            "split_method": {"seed": seed, "validation_fraction": validation_fraction,
                             "algorithm": "quarantine training originals matching test SHA256; group remaining training by SHA256; "
                                          "order by sha256(seed + ':' + smallest_group_id); greedily assign whole groups fitting round(clean_N*fraction) to validation",
                             "unit": "byte-identical original groups; no patient/lesion grouping metadata provided by these packages"},
            "source_counts": dict(counts),
            "counts": {split: sum(item["split"] == split for item in items)
                       for split in ("train", "validation", "test", "excluded_test_duplicate")},
            "duplicate_content_groups": duplicates,
            "demo": {"count": demo_count, "source_split": "validation", "ids": demo_ids,
                     "selection": "one smallest ID per original SHA256, then evenly spaced ranks by ground-truth lesion fraction; independent of model predictions",
                     "purpose": "fixed GUI and engineering validation subset; not a representative final test score"},
            "pixel_policy": "original bytes and resolution retained; mask filename adapted only; no physical calibration",
            "items": items,
        }
        (working / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        (working / "SOURCE.txt").write_text(f"{SOURCE_PAGE}\n{CITATION}\nLicense: CC0 (official page).\n"
            "demo is a subset of validation, not an independent test set.\n"
            "excluded_test_duplicate contains official training images matching test originals; do not train on them.\n"
            "Do not train on validation/test. Image-level split does not establish patient independence.\n", encoding="utf-8")
        # Publish with marker present; a failed rename still leaves a marked directory.
        working.rename(destination)
        (destination / "INCOMPLETE.txt").unlink()
        progress(f"Prepared: {destination}; {manifest['counts']}; demo={demo_count}")
        return manifest


@entry_session("dataset_cli")
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ISIC 2016 Task 1 数据准备（原像素保留）")
    parser.add_argument("--archives", type=Path, default=Path("datasets/isic2016/archives"))
    parser.add_argument("--output", type=Path, default=Path("datasets/isic2016/prepared"))
    parser.add_argument("--download", action="store_true", help="下载官方四个 ZIP，已有完整 ZIP 使用缓存")
    parser.add_argument("--download-only", action="store_true", help="只下载，不创建数据目录")
    parser.add_argument("--connections", type=int, default=4, help="下载连接数 1–8，默认 4；1 使用单连接续传")
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--demo-count", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        if args.download or args.download_only:
            download_archives(args.archives, connections=args.connections, progress=lambda message: print(message, flush=True))
        if not args.download_only:
            prepare_dataset(args.archives, args.output, seed=args.seed, validation_fraction=args.validation_fraction,
                            demo_count=args.demo_count, progress=lambda message: print(message, flush=True))
        return 0
    except KeyboardInterrupt:
        print("已取消；下载 .part 可续传，带 INCOMPLETE.txt 的数据目录不可使用。", file=sys.stderr)
        return 130
    except Exception as exc:
        record_exception("dataset_failed", exc, stage="dataset_cli")
        print(f"ISIC 数据准备失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
