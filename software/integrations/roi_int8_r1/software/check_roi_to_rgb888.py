"""Bounded ROI checks: geometry, EXIF, frozen-chain equivalence, mapping. No model."""
import importlib.util
import json
from pathlib import Path
import tempfile

import cv2
import numpy as np
from PIL import Image
from roi_to_rgb888 import load_roi_image,convert_roi,validate_roi,map_mask_to_original
from image_to_rgb888 import JPEG_OPTIONS


def run_checks(workdir=None):
    package=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('unmodified_full_image_reference',package/'source_snapshots/frozen_image_to_rgb888.py')
    legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
    params=[v for k,value in JPEG_OPTIONS.items() for v in (getattr(cv2,k),value)]
    rng=np.random.default_rng(42)
    fixture=rng.integers(0,256,(59,83,3),dtype=np.uint8)
    transforms={1:lambda a:a,2:lambda a:a[:,::-1],3:lambda a:a[::-1,::-1],4:lambda a:a[::-1],
        5:lambda a:a.transpose(1,0,2),6:lambda a:np.rot90(a,3),
        7:lambda a:a.transpose(1,0,2)[::-1,::-1],8:lambda a:np.rot90(a,1)}
    exif_checks=[]
    with tempfile.TemporaryDirectory(dir=workdir or Path.cwd()) as temporary:
        root=Path(temporary)
        for fmt in ('JPEG','PNG'):
            for orientation in range(1,9):
                path=root/f'orientation{orientation}.{fmt.lower()}'
                exif=Image.Exif();exif[274]=orientation
                Image.fromarray(fixture).save(path,format=fmt,exif=exif)
                unrotated=cv2.imread(str(path),cv2.IMREAD_COLOR|cv2.IMREAD_IGNORE_ORIENTATION)
                expected=np.ascontiguousarray(transforms[orientation](unrotated))
                image=load_roi_image(path)
                assert image.width==expected.shape[1] and image.height==expected.shape[0]
                assert np.array_equal(image.original_rgb(),cv2.cvtColor(expected,cv2.COLOR_BGR2RGB))
                rect=[3,4,image.width-2,image.height-3]
                x0,y0,x1,y1=rect
                expected_crop=expected[y0:y1,x0:x1]
                assert np.array_equal(image.crop_rgb(rect),cv2.cvtColor(expected_crop,cv2.COLOR_BGR2RGB))
                # Independent direct array chain, using frozen settings, no crop JPEG save.
                resized=cv2.resize(expected_crop,(256,256),interpolation=cv2.INTER_AREA)
                oracle=cv2.cvtColor(cv2.imdecode(cv2.imencode('.jpg',resized,params)[1],cv2.IMREAD_COLOR),cv2.COLOR_BGR2RGB)
                rgb,metadata=convert_roi(image,rect)
                assert np.array_equal(rgb,oracle)
                assert np.array_equal(rgb,convert_roi(image,rect)[0])
                full,_=convert_roi(image,[0,0,image.width,image.height])
                assert np.array_equal(full,legacy.prepare_input(path,input_kind='raw')[0])
                exif_checks.append(dict(format=fmt,orientation=orientation,corrected_coordinates=True,
                    no_second_rotation=True,full_roi_legacy_byte_equal=True))
        path=root/'decode_count.png';Image.fromarray(fixture).save(path)
        decode,encode=cv2.imdecode,cv2.imencode
        counts=dict(decodes=0,jpeg_encodes=0)
        def count_decode(*a,**kw):counts['decodes']+=1;return decode(*a,**kw)
        def count_encode(ext,*a,**kw):
            if ext=='.jpg':counts['jpeg_encodes']+=1
            return encode(ext,*a,**kw)
        try:
            cv2.imdecode=count_decode;cv2.imencode=count_encode
            convert_roi(path,[0,0,83,59])
        finally:cv2.imdecode=decode;cv2.imencode=encode
        assert counts==dict(decodes=2,jpeg_encodes=1)
        # One source decode + one JPEG decode. NOT two source decodes or two JPEG encodes.
        image=load_roi_image(path)
        assert image.crop_rgb([0,0,16,16]).shape==(16,16,3)
        assert convert_roi(image,[67,43,83,59])[0].shape==(256,256,3)
        invalid=[('too_narrow',[0,0,15,16]),('too_short',[0,0,16,15]),
            ('negative',[-1,0,20,20]),('beyond_right',[0,0,84,20]),('beyond_bottom',[0,0,20,60]),
            ('empty_x',[4,4,4,24]),('empty_y',[4,4,24,4]),('inverted',[24,4,4,24]),
            ('float',[0.,0,16,16]),('integral_float',[0,0,16.,16]),
            ('bool',[False,0,16,16]),('numpy_bool',[np.bool_(False),0,16,16]),
            ('wrong_length',[0,0,16])]
        rejected=[]
        for label,rect in invalid:
            try:convert_roi(image,rect)
            except (ValueError,TypeError) as exc:rejected.append(dict(case=label,error=str(exc)))
            else:raise AssertionError(f'Accepted invalid ROI: {label}')
        assert validate_roi([np.int64(0),0,16,16],83,59)==(0,0,16,16)
        # Same input-format exclusions as the unchanged full-image entry.
        unsupported=[('gray.png',Image.new('L',(32,32))),('alpha.png',Image.new('RGBA',(32,32))),
            ('palette.png',Image.new('P',(32,32))),('sixteen.png',Image.fromarray(np.zeros((32,32),np.uint16))),
            ('cmyk.jpg',Image.new('CMYK',(32,32)))]
        format_rejected=[]
        for name,im in unsupported:
            p=root/name;im.save(p)
            try:convert_roi(p,[0,0,16,16])
            except ValueError as exc:format_rejected.append(dict(case=name,error=str(exc)))
            else:raise AssertionError('Unsupported format accepted: '+name)
    mask=np.zeros((256,256),np.uint8);mask[:128,:128]=255;mask[192:,192:]=255
    roi=[7,9,39,25] # 32x16 inside 80x60; known nearest mapping.
    mapped=map_mask_to_original(mask,roi,oriented_width=80,oriented_height=60)
    oracle=mask[(np.arange(16)*256//16)[:,None],(np.arange(32)*256//32)[None,:]]
    assert np.array_equal(mapped['roi_mask'],oracle)
    assert mapped['display_canvas'].shape==(60,80) and mapped['inferred_region'].shape==(60,80)
    expected=np.zeros((60,80),np.uint8);expected[9:25,7:39]=oracle
    coverage=np.zeros((60,80),bool);coverage[9:25,7:39]=True
    assert np.array_equal(mapped['display_canvas'],expected) and np.array_equal(mapped['inferred_region'],coverage)
    assert np.count_nonzero(mapped['display_canvas'][~coverage])==0
    assert mapped['foreground_area_original_roi_pixels']==int(np.count_nonzero(oracle))
    edge=map_mask_to_original(mask,[48,44,80,60],oriented_width=80,oriented_height=60)
    assert np.array_equal(edge['roi_mask'],oracle) and edge['inferred_region'][59,79]
    return dict(exif_cases=exif_checks,roi_rejections=rejected,supported_format_boundary_rejections=format_rejected,
        exact_16_by_16_boundary_accepted=True,numpy_integer_accepted_bool_rejected=True,
        one_source_decode_plus_one_jpeg_decode=counts,nearest_mapping_independent_oracle_passed=True,
        uninferred_region_distinct_from_background=True,deterministic_conversion=True)


if __name__=='__main__':print(json.dumps(run_checks(),indent=2,ensure_ascii=False))
