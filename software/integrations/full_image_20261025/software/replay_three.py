"""Replay three full-image fixtures with the unchanged official CUDA integer core."""
import argparse
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np
import torch
from image_to_rgb888 import prepare_input
from official_reference_loader import load_official


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delivery', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    identity = json.loads((root/'model_identity.json').read_text('utf-8'))
    reference = load_official(Path(args.delivery), identity)
    rows = json.loads((root/'samples.json').read_text('utf-8'))
    with torch.inference_mode():
        for row in rows:
            case = root/row['directory']
            rgb, _ = prepare_input(case/row['input_file'], input_kind='raw')
            data = (case/'input.rgb888.bin').read_bytes()
            assert rgb.tobytes() == data
            assert np.array_equal(prepare_input(case/'input.rgb888.bin', input_kind='rgb888')[0], rgb)
            preview = cv2.cvtColor(cv2.imread(str(case/'input_rgb_preview.png')), cv2.COLOR_BGR2RGB)
            assert np.array_equal(preview, rgb)
            logits = reference.run(np.frombuffer(data, np.uint8).reshape(256,256,3))
            assert hashlib.sha256(logits.cpu().numpy().tobytes()).hexdigest() == row['logits_sha256']
            mask = (logits[0,0].cpu().numpy() >= reference.spec['mask_foreground_min_integer']).astype(np.uint8)*255
            assert mask.tobytes() == (case/'mask.bin').read_bytes()
            assert np.array_equal(mask, cv2.imread(str(case/'mask.png'), cv2.IMREAD_GRAYSCALE))
    assert reference.overflow == 0
    print(json.dumps(dict(samples=len(rows), byte_and_mask_checks=True, accumulator_overflows=int(reference.overflow))))


if __name__ == '__main__': main()
