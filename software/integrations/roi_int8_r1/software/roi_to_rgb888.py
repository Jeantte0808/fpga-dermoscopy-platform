"""Integer half-open ROI -> frozen RGB888; no model or GUI dependency."""
import argparse
from dataclasses import dataclass
import hashlib
import json
from numbers import Integral
from pathlib import Path

import cv2
import numpy as np
from image_to_rgb888 import decode_original_bgr, decoded_bgr_to_rgb888, JPEG_OPTIONS, VERSION as FULL_VERSION

VERSION = 'ROI_RGB888_V1_20261026'
MIN_SIDE = 16


def validate_roi(roi, width, height):
    if not isinstance(roi, (tuple, list, np.ndarray)) or len(roi) != 4:
        raise ValueError('ROI must contain exactly four integer coordinates [x0,y0,x1,y1).')
    if any(isinstance(v, (bool, np.bool_)) or not isinstance(v, Integral) for v in roi):
        raise TypeError('ROI coordinates must be integers; floats and booleans are rejected without rounding.')
    x0,y0,x1,y1 = map(int,roi)
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError(f'Invalid ROI {list(roi)}: require 0<=x0<x1<=W={width}, 0<=y0<y1<=H={height}; no clipping or swapping.')
    if x1-x0 < MIN_SIDE or y1-y0 < MIN_SIDE:
        raise ValueError(f'ROI width and height must each be >= {MIN_SIDE} original-image pixels (software operation limit).')
    return x0,y0,x1,y1


@dataclass(frozen=True)
class ROIImage:
    """Cached, EXIF-corrected pixels. Created only by load_roi_image()."""
    _bgr: np.ndarray
    source_metadata: dict

    @property
    def width(self): return self._bgr.shape[1]

    @property
    def height(self): return self._bgr.shape[0]

    def original_rgb(self):
        """GUI original preview, already oriented. Do not apply EXIF again."""
        return np.ascontiguousarray(cv2.cvtColor(self._bgr,cv2.COLOR_BGR2RGB))

    def crop_rgb(self, roi):
        x0,y0,x1,y1 = validate_roi(roi,self.width,self.height)
        return np.ascontiguousarray(cv2.cvtColor(self._bgr[y0:y1,x0:x1],cv2.COLOR_BGR2RGB))


def load_roi_image(path):
    """Decode/EXIF exactly once. Reuse result across GUI selections."""
    bgr,metadata = decode_original_bgr(path)
    bgr.setflags(write=False)
    return ROIImage(bgr,metadata)


def convert_roi(source, roi):
    """Return (uint8 [256,256,3] RGB, metadata); source is file path or ROIImage.

    ROI is in oriented original integer coordinates, not GUI or model pixels.
    No cropped-JPEG save pass is inserted. No float normalization is performed.
    """
    image = source if isinstance(source,ROIImage) else load_roi_image(source)
    x0,y0,x1,y1 = validate_roi(roi,image.width,image.height)
    crop = image._bgr[y0:y1,x0:x1]
    rgb,jpeg_metadata = decoded_bgr_to_rgb888(crop)
    metadata = dict(image.source_metadata)
    metadata.update(version=VERSION,base_full_image_version=FULL_VERSION,
        oriented_width=image.width,oriented_height=image.height,
        roi=[x0,y0,x1,y1],roi_width=x1-x0,roi_height=y1-y0,
        coordinate_space='EXIF-oriented original image',interval='integer half-open [x0,y0,x1,y1)',
        minimum_side_original_pixels=MIN_SIDE,minimum_is_software_limit_not_medical_reliability=True,
        augmentation=False,host_normalization=False,jpeg_roundtrip=True,jpeg_options=JPEG_OPTIONS,
        jpeg_encode_count_after_crop=1,output_shape=[256,256,3],output_dtype='uint8',output_bytes=196608,
        rgb888_sha256=hashlib.sha256(rgb.tobytes()).hexdigest(),**jpeg_metadata)
    return rgb,metadata


def map_mask_to_original(mask256, roi, *, oriented_width, oriented_height):
    """Return dict with display_canvas, inferred_region(bool), roi_mask, areas.

    Zeros OUTSIDE inferred_region mean UNINFERRED, NOT predicted background.
    nearest only, no morphological postprocessing. Areas are pixel counts.
    """
    if not isinstance(mask256,np.ndarray) or mask256.shape != (256,256) or mask256.dtype != np.uint8:
        raise ValueError('mask256 must be 256x256 uint8 binary mask.')
    if not set(np.unique(mask256)).issubset({0,255}): raise ValueError('Mask values must be 0/255.')
    if any(isinstance(v,(bool,np.bool_)) or not isinstance(v,Integral) or v<1 for v in (oriented_width,oriented_height)):
        raise ValueError('Oriented dimensions must be positive integers.')
    w,h = int(oriented_width),int(oriented_height)
    x0,y0,x1,y1 = validate_roi(roi,w,h)
    resized = cv2.resize(mask256,(x1-x0,y1-y0),interpolation=cv2.INTER_NEAREST)
    canvas = np.zeros((h,w),np.uint8)
    coverage = np.zeros((h,w),bool)
    canvas[y0:y1,x0:x1] = resized
    coverage[y0:y1,x0:x1] = True
    return dict(display_canvas=canvas,inferred_region=coverage,roi_mask=resized,
        foreground_area_256_grid_pixels=int(np.count_nonzero(mask256)),
        foreground_area_original_roi_pixels=int(np.count_nonzero(resized)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,help='Original supported JPEG/PNG, never preprocessed RGB')
    parser.add_argument('--roi',required=True,nargs=4,type=int,metavar=('X0','Y0','X1','Y1'))
    parser.add_argument('--output-dir',required=True,help='New directory; existing results are never overwritten')
    args=parser.parse_args()
    out=Path(args.output_dir)
    if out.exists(): raise FileExistsError(out)
    image=load_roi_image(args.input)
    rgb,metadata=convert_roi(image,args.roi)
    out.mkdir(parents=True)
    (out/'input.rgb888.bin').write_bytes(rgb.tobytes(order='C'))
    for name,a in [('roi_preview.png',image.crop_rgb(args.roi)),('input_rgb_preview.png',rgb)]:
        ok,png=cv2.imencode('.png',cv2.cvtColor(a,cv2.COLOR_RGB2BGR))
        if not ok: raise RuntimeError('PNG encoding failed.')
        (out/name).write_bytes(png.tobytes())
    (out/'conversion.json').write_text(json.dumps(metadata,indent=2,ensure_ascii=False),'utf-8')
    print(json.dumps(metadata,indent=2,ensure_ascii=False))


if __name__=='__main__':main()
