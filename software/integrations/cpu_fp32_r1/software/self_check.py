"""Packaged three-sample CPU-only sanity check; not a dataset accuracy evaluation."""
import hashlib
import json
from pathlib import Path
import torch
import cv2
import numpy as np
from cpu_fp32 import load_model, read_rgb888


def main():
    def forbidden(*args, **kwargs):
        raise AssertionError('CUDA initialization / pretrained download is forbidden in CPU entry.')
    torch.cuda._lazy_init = forbidden
    torch.cuda.is_available = forbidden
    torch.hub.load_state_dict_from_url = forbidden
    root = Path(__file__).resolve().parents[1]
    runner = load_model(root)
    model_id = id(runner.model)
    rows = json.loads((root/'samples.json').read_text('utf-8'))
    results = []
    for row in rows:
        folder = root/row['directory']
        rgb = read_rgb888(folder/'input.rgb888.bin')
        assert hashlib.sha256(rgb.tobytes()).hexdigest() == row['rgb888_sha256']
        x = runner.prepare_tensor(rgb)
        assert x.shape == (1,3,256,256) and x.dtype == torch.float32 and x.device.type == 'cpu'
        logits = runner.logits_rgb(rgb)
        mask = runner.predict_rgb(rgb)
        assert mask.shape == (256,256) and mask.dtype == np.uint8 and set(np.unique(mask)).issubset({0,255})
        assert mask.tobytes() == (folder/'cpu_mask.bin').read_bytes()
        assert np.array_equal(mask, cv2.imread(str(folder/'cpu_mask.png'),cv2.IMREAD_GRAYSCALE))
        assert id(runner.model) == model_id
        results.append(dict(sample=row['name'], cpu_mask_matches_saved=True, finite_logits=bool(torch.isfinite(logits).all())))
    print(json.dumps(dict(samples=results, model_loaded_once_and_reused=True,
        cuda_init_and_cuda_availability_queries_blocked=True, pretrained_download_blocked=True,
        device='cpu',dtype='float32',eval=True,inference_mode=True, full_dataset_accuracy_evaluation=False)))


if __name__ == '__main__': main()
