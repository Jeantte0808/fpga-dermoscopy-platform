"""Small preprocessing-only checks; no model, calibration, or dataset evaluation."""
import io
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from image_to_rgb888 import JPEG_OPTIONS, prepare_input, decode_original_rgb


def run_checks(workdir=None):
    params = [v for name, value in JPEG_OPTIONS.items() for v in (getattr(cv2, name), value)]
    rng = np.random.default_rng(42)
    fixture = rng.integers(0, 256, (117, 193, 3), dtype=np.uint8)
    default = cv2.imencode('.jpg', fixture)[1]
    quality_matches = [q for q in range(101) if np.array_equal(
        default, cv2.imencode('.jpg', fixture, [cv2.IMWRITE_JPEG_QUALITY, q])[1])]
    assert quality_matches == [95]
    assert np.array_equal(default, cv2.imencode('.jpg', fixture, params)[1])
    with Image.open(io.BytesIO(default.tobytes())) as encoded:
        assert [(layer[1], layer[2]) for layer in encoded.layer] == [(2, 2), (1, 1), (1, 1)]
        assert not encoded.info.get('progressive') and not encoded.info.get('progression')
    transforms = {
        1: lambda a: a, 2: lambda a: a[:, ::-1], 3: lambda a: a[::-1, ::-1],
        4: lambda a: a[::-1], 5: lambda a: a.transpose(1, 0, 2),
        6: lambda a: np.rot90(a, 3), 7: lambda a: a.transpose(1, 0, 2)[::-1, ::-1],
        8: lambda a: np.rot90(a, 1)}
    orientation_checks = []
    rejected = []
    with tempfile.TemporaryDirectory(dir=workdir) as directory:
        root = Path(directory)
        for fmt in ('JPEG', 'PNG'):
            for orientation in range(1, 9):
                path = root / f'orientation_{orientation}.{fmt.lower()}'
                exif = Image.Exif(); exif[274] = orientation
                Image.fromarray(fixture[:31, :47]).save(path, format=fmt, exif=exif)
                raw = cv2.imread(str(path), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
                expected = np.ascontiguousarray(transforms[orientation](raw))
                displayed = decode_original_rgb(path)
                assert np.array_equal(displayed, cv2.cvtColor(expected, cv2.COLOR_BGR2RGB))
                expected = cv2.resize(expected, (256, 256), interpolation=cv2.INTER_AREA)
                expected = cv2.cvtColor(cv2.imdecode(cv2.imencode('.jpg', expected, params)[1],
                                                    cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
                rgb, meta = prepare_input(path, input_kind='raw')
                assert np.array_equal(rgb, expected)
                assert np.array_equal(rgb, prepare_input(path, input_kind='raw')[0])
                orientation_checks.append(dict(format=fmt, orientation=orientation, passed=True))
        invalid = [('gray.png', Image.new('L', (9, 7))),
                   ('alpha.png', Image.new('RGBA', (9, 7))),
                   ('palette.png', Image.new('P', (9, 7))),
                   ('sixteen.png', Image.fromarray(np.zeros((7, 9), np.uint16))),
                   ('cmyk.jpg', Image.new('CMYK', (9, 7)))]
        for name, image in invalid:
            path = root / name; image.save(path)
            try: prepare_input(path, input_kind='raw')
            except ValueError as exc: rejected.append(dict(file=name, error=str(exc)))
            else: raise AssertionError(f'Should have rejected {name}')
        p = root / 'transparency.png'
        Image.new('RGB', (9, 7)).save(p, transparency=(0, 0, 0))
        try: prepare_input(p, input_kind='raw')
        except ValueError as exc: rejected.append(dict(file=p.name, error=str(exc)))
        else: raise AssertionError('RGB transparency silently accepted')
        # Explicit modes do not resize or recompress already-processed bytes.
        processed = root / 'processed.png'
        a = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
        cv2.imwrite(str(processed), a)
        expected = cv2.cvtColor(a, cv2.COLOR_BGR2RGB)
        rgb, metadata = prepare_input(processed, input_kind='processed')
        assert np.array_equal(rgb, expected) and not metadata['jpeg_roundtrip']
        binary = root / 'input.bin'; binary.write_bytes(rgb.tobytes())
        assert np.array_equal(rgb, prepare_input(binary, input_kind='rgb888')[0])
        try: prepare_input(processed)
        except TypeError: pass
        else: raise AssertionError('input_kind must be explicit')
    return dict(default_jpeg_quality_matches=quality_matches, explicit_jpeg_equal_default=True,
                measured_subsampling='4:2:0', orientation_checks=orientation_checks,
                unsupported_inputs_rejected=rejected, processed_and_binary_bypass_verified=True,
                repeated_conversion_byte_equal=True)


if __name__ == '__main__':
    import json
    print(json.dumps(run_checks(), indent=2))
