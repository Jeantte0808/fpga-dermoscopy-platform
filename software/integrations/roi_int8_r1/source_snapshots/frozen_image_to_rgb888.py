"""Frozen full-image preprocessing, explicitly separate raw/processed/RGB888 inputs."""
import argparse
import hashlib
import io
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, __version__ as PIL_VERSION

VERSION = 'FULL_IMAGE_RGB888_V1_20261025'
JPEG_OPTIONS = {
    'IMWRITE_JPEG_QUALITY': 95,
    'IMWRITE_JPEG_PROGRESSIVE': 0,
    'IMWRITE_JPEG_OPTIMIZE': 0,
    'IMWRITE_JPEG_RST_INTERVAL': 0,
    'IMWRITE_JPEG_SAMPLING_FACTOR': 0x221111,  # measured 4:2:0
}
# Separate luma/chroma quality controls are intentionally absent: same as legacy.


def environment():
    build = cv2.getBuildInformation()
    return dict(opencv=cv2.__version__, numpy=np.__version__, pillow=PIL_VERSION,
                opencv_build_sha256=hashlib.sha256(build.encode()).hexdigest(),
                codec_build=[s.strip() for s in build.splitlines()
                             if any(k in s for k in ('JPEG:', 'PNG:', 'SIMD Support:'))])


def check_environment():
    """Reject unverified versions; no promise of cross-library byte equivalence."""
    actual = environment()
    expected = json.loads(Path(__file__).with_name('preprocessing_environment.json').read_text('utf-8'))
    for key in ('opencv', 'numpy', 'pillow', 'opencv_build_sha256'):
        if actual[key] != expected[key]:
            raise RuntimeError(f'Unverified preprocessing dependency {key}: '
                               f'{actual[key]!r}; required {expected[key]!r}. '
                               'Use the recorded environment and byte fixtures.')


def inspect_image(data):
    """Metadata validation only. Pillow does not supply model pixels."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            fmt = image.format
            if fmt not in ('JPEG', 'PNG'):
                raise ValueError('Only 8-bit three-channel JPEG or PNG is supported.')
            if image.mode != 'RGB' or image.info.get('transparency') is not None:
                raise ValueError(f'Unsupported {image.mode} / transparency: supply an opaque '
                                 '8-bit RGB JPEG/PNG; no implicit grayscale/alpha/palette conversion.')
            if getattr(image, 'n_frames', 1) != 1:
                raise ValueError('Animated/multi-frame images are unsupported.')
            if image.info.get('icc_profile'):
                raise ValueError('Embedded ICC profiles are unsupported; no automatic color-profile conversion.')
            if fmt == 'PNG' and (data[24] != 8 or data[25] != 2):
                raise ValueError('PNG must have bit depth 8 and truecolor type 2; no 16-bit truncation.')
            if fmt == 'JPEG' and (getattr(image, 'bits', None) != 8 or image.layers != 3):
                raise ValueError('JPEG must have 8-bit precision and three components; CMYK is unsupported.')
            orientation = image.getexif().get(274, 1)
            if orientation not in range(1, 9):
                raise ValueError(f'Invalid EXIF orientation {orientation!r}.')
            return dict(format=fmt, stored_width=image.width, stored_height=image.height,
                        exif_orientation=int(orientation), mode=image.mode)
    except (OSError, SyntaxError) as exc:
        raise ValueError(f'Invalid JPEG/PNG: {exc}') from exc


def decode_original_rgb(path):
    """GUI full-resolution preview in the SAME oriented coordinate frame as raw input.

    Do not apply a second EXIF transpose. Return RGB, not GUI-library-native BGR.
    """
    check_environment()
    data = Path(path).read_bytes()
    inspect_image(data)
    bgr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError('OpenCV could not decode this image.')
    return np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


def prepare_input(path, *, input_kind):
    """Return (uint8 RGB HWC array, metadata). input_kind is MANDATORY.

    raw: decode+EXIF once, INTER_AREA stretch, JPEG roundtrip, BGR->RGB.
    processed: decode existing 256x256 image, BGR->RGB ONLY.
    rgb888: read exactly 196608 packed HWC bytes, no image decode or transforms.
    """
    check_environment()
    if input_kind not in ('raw', 'processed', 'rgb888'):
        raise ValueError('input_kind must explicitly be raw, processed, or rgb888.')
    data = Path(path).read_bytes()
    metadata = dict(version=VERSION, input_kind=input_kind,
                    input_sha256=hashlib.sha256(data).hexdigest(), jpeg_roundtrip=False)
    if input_kind == 'rgb888':
        if len(data) != 196608:
            raise ValueError(f'RGB888 requires exactly 196608 bytes; received {len(data)}.')
        rgb = np.frombuffer(data, np.uint8).reshape(256, 256, 3).copy()
    else:
        info = inspect_image(data)
        metadata.update(info)
        if input_kind == 'processed' and info['exif_orientation'] != 1:
            raise ValueError('Processed inputs must already be oriented (EXIF absent/1).')
        # IMREAD_COLOR applies EXIF once, including PNG eXIf in this pinned build.
        # Same decoder/flag as cv2.imread(path) in prepare_dataset.py.
        bgr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError('OpenCV could not decode this image.')
        metadata['oriented_width'], metadata['oriented_height'] = bgr.shape[1], bgr.shape[0]
        if input_kind == 'raw':
            bgr = cv2.resize(bgr, (256, 256), interpolation=cv2.INTER_AREA)
            params = [v for name, value in JPEG_OPTIONS.items() for v in (getattr(cv2, name), value)]
            ok, encoded = cv2.imencode('.jpg', bgr, params)
            if not ok:
                raise RuntimeError('Intermediate JPEG encoding failed.')
            metadata.update(jpeg_roundtrip=True, jpeg_options=JPEG_OPTIONS,
                            intermediate_jpeg_sha256=hashlib.sha256(encoded.tobytes()).hexdigest())
            bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        elif bgr.shape != (256, 256, 3):
            raise ValueError(f'Processed input must already be 256x256x3, got {bgr.shape}.')
        rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), dtype=np.uint8)
    if rgb.shape != (256, 256, 3) or rgb.dtype != np.uint8 or not rgb.flags.c_contiguous:
        raise RuntimeError('Invalid RGB888 output.')
    metadata.update(output_shape=[256, 256, 3], output_bytes=rgb.nbytes,
                    rgb888_sha256=hashlib.sha256(rgb.tobytes()).hexdigest(),
                    host_normalization=False, augmentation=False)
    return rgb, metadata


def to_rgb888(path, *, input_kind):
    """GUI entry: return a contiguous (256,256,3) uint8 RGB NumPy array."""
    return prepare_input(path, input_kind=input_kind)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--input-kind', required=True, choices=['raw', 'processed', 'rgb888'])
    parser.add_argument('--output', required=True, help='New raw RGB888 binary path')
    parser.add_argument('--preview', help='Optional new lossless PNG preview')
    parser.add_argument('--metadata', help='Optional new JSON metadata')
    args = parser.parse_args()
    targets = [Path(p).resolve() for p in (args.output, args.preview, args.metadata) if p]
    if len(set(targets)) != len(targets) or any(p.exists() for p in targets):
        raise FileExistsError('Output paths must be distinct and new; existing inputs/results are never overwritten.')
    if args.preview and Path(args.preview).suffix.lower() != '.png':
        raise ValueError('Preview must be PNG (no lossy re-encoding).')
    rgb, metadata = prepare_input(args.input, input_kind=args.input_kind)
    Path(args.output).write_bytes(rgb.tobytes(order='C'))
    if args.preview:
        ok, png = cv2.imencode('.png', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        if not ok: raise RuntimeError('PNG encoding failed.')
        Path(args.preview).write_bytes(png.tobytes())
    if args.metadata:
        Path(args.metadata).write_text(json.dumps(metadata, indent=2, ensure_ascii=False), 'utf-8')
    print(json.dumps(metadata, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
