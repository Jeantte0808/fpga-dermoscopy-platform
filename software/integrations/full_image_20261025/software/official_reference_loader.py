"""Replay exactly the packaged validation samples with an unchanged official core."""
import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
import torch


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1048576),b''):h.update(block)
    return h.hexdigest()


def load_official(delivery,identity):
    delivery=Path(delivery).resolve()
    assert sha(delivery/'model/parameter_files.json')==identity['parameter_manifest_sha256']
    assert sha(delivery/'model/quantization.json')==identity['quantization_sha256']
    core=delivery/'software/integer_reference.py'
    assert sha(core)==identity['integer_reference_sha256']
    files=json.loads((delivery/'model/parameter_files.json').read_text(encoding='utf-8'))
    for row in files:
        path=delivery/'model'/row['path']
        assert path.stat().st_size==row['bytes'] and sha(path)==row['sha256']
    spec=importlib.util.spec_from_file_location('official_validation20_integer_core',core)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.IntegerReference(delivery/'model')


def verify_sample_files(package):
    for row in json.loads((package/'manifest.json').read_text(encoding='utf-8'))['files']:
        p=package/row['path']
        assert p.stat().st_size==row['bytes'] and sha(p)==row['sha256'],p


def replay(package,delivery):
    identity=json.loads((package/'model_identity.json').read_text(encoding='utf-8'))
    reference=load_official(delivery,identity)
    with (package/'samples.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    assert len(rows)==20 and [r['filename'] for r in rows]==sorted(r['filename'] for r in rows)
    with torch.inference_mode():
        for row in rows:
            path=package/row['rgb888_file']
            assert path.stat().st_size==196608
            rgb=np.fromfile(path,np.uint8).reshape(256,256,3)
            image=cv2.imread(str(package/row['image_file']),cv2.IMREAD_COLOR)
            assert image is not None and np.array_equal(cv2.cvtColor(image,cv2.COLOR_BGR2RGB),rgb)
            gt=cv2.imread(str(package/row['ground_truth_file']),cv2.IMREAD_GRAYSCALE)
            assert gt.shape==(256,256) and set(np.unique(gt)).issubset({0,255})
            q=reference.run(rgb)
            mask=(q[0,0].cpu().numpy()>=reference.spec['mask_foreground_min_integer']).astype(np.uint8)*255
            actual=np.fromfile(package/row['reference_mask_bin'],np.uint8).reshape(256,256)
            png=cv2.imread(str(package/row['reference_mask_png']),cv2.IMREAD_GRAYSCALE)
            assert np.array_equal(mask,actual) and np.array_equal(mask,png),row['filename']
            assert hashlib.sha256(q.cpu().numpy().tobytes()).hexdigest()==row['final_int8_logits_sha256']
    return dict(samples=20,all_rgb_bytes_match_decoded_images=True,all_mask_png_binary_and_native_reference_equal=True,
                formal_parameter_manifest_sha256=identity['parameter_manifest_sha256'],
                accumulator_overflows=int(reference.overflow),version=identity['formal_delivery_version'],
                numerical_candidate=identity['numerical_candidate_version'],new_independent_test_result=False,
                layer_tensors_exported=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample-pack',default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--delivery',required=True,help='Existing formal HW_R1 delivery directory')
    parser.add_argument('--verify-files',action='store_true')
    parser.add_argument('--report-out')
    args=parser.parse_args();package=Path(args.sample_pack).resolve()
    if args.verify_files:verify_sample_files(package)
    result=replay(package,Path(args.delivery))
    if args.report_out:
        path=Path(args.report_out)
        if path.exists():raise FileExistsError(path)
        path.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(result,indent=2,ensure_ascii=False))


if __name__=='__main__':main()
