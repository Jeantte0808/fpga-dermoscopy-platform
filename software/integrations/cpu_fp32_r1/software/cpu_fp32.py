"""Resident frozen FP32 segmentation on CPU. No CUDA calls, downloads, or INT8 LUT."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import time

import cv2
import numpy as np
import torch
import torchvision

from folded_model_loader import load_folded_model

PACKAGE = Path(__file__).resolve().parents[1]


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''): h.update(block)
    return h.hexdigest()


def configure_cpu(intra_op=4, inter_op=1):
    if intra_op < 1 or inter_op < 1:
        raise ValueError('Thread counts must be positive.')
    torch.set_num_threads(intra_op)
    if torch.get_num_interop_threads() != inter_op:
        try: torch.set_num_interop_threads(inter_op)
        except RuntimeError as exc:
            raise RuntimeError('Configure inter-op threads before any inference; restart the process to change it.') from exc
    # Keep CPU convolutions FP32; never permit CPU BF16/TF32 precision shortcuts.
    torch.backends.fp32_precision = 'ieee'
    torch.backends.mkldnn.conv.fp32_precision = 'ieee'


def read_rgb888(path):
    data = Path(path).read_bytes()
    if len(data) != 196608:
        raise ValueError(f'Expected 196608 RGB888 bytes, got {len(data)}.')
    return np.frombuffer(data, np.uint8).reshape(256, 256, 3).copy()


def validate_rgb(rgb):
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.shape != (256,256,3):
        raise ValueError('Input must be uint8 RGB HWC NumPy array with shape (256,256,3).')


class CPUFP32:
    """Create once at GUI startup; reuse predict_rgb for subsequent images.

    CPU normalization follows the original folding/evaluation implementation.
    This object does not reread weights or create another network per image.
    """
    def __init__(self, package=PACKAGE, *, intra_op=4, inter_op=1):
        self.package = Path(package).resolve()
        configure_cpu(intra_op, inter_op)
        identity = json.loads((self.package/'model_identity.json').read_text('utf-8'))
        # Timing excludes imports, thread setup, JSON/metadata checks and file hashing.
        start = time.perf_counter_ns()
        self.model = load_folded_model(self.package/'model', device='cpu')
        self.load_ms = (time.perf_counter_ns()-start)/1e6
        self.device = torch.device('cpu')
        self.identity = identity
        assert sha256(self.package/'model/folded_model.pth') == identity['folded_weights_sha256']
        assert all(p.device.type == 'cpu' and p.dtype == torch.float32 for p in self.model.parameters())
        assert not any(module.training for module in self.model.modules())
        settings = json.loads((self.package/'model/comparison_settings.json').read_text('utf-8'))
        assert settings['threshold'] == 'sigmoid(logits)>0.5'
        self.mean = torch.tensor(settings['mean'],dtype=torch.float32).view(1,3,1,1)
        self.std = torch.tensor(settings['std'],dtype=torch.float32).view(1,3,1,1)

    def prepare_tensor(self, rgb):
        validate_rgb(rgb)
        # Deliberately identical order to the folding evaluation: NumPy /255,
        # CHW contiguous, then CPU FP32 torch mean/std. No INT8 LUT.
        chw = np.ascontiguousarray((rgb.astype(np.float32)/255).transpose(2,0,1))
        x = torch.from_numpy(chw).unsqueeze(0)
        return ((x-self.mean)/self.std).contiguous()

    @torch.inference_mode()
    def logits_rgb(self, rgb):
        with torch.autocast(device_type='cpu', enabled=False):
            output = self.model(self.prepare_tensor(rgb))
        if output.shape != (1,1,256,256) or output.dtype != torch.float32 or output.device.type != 'cpu':
            raise RuntimeError('Invalid FP32 CPU logits.')
        if not torch.isfinite(output).all(): raise RuntimeError('Non-finite logits.')
        return output

    @torch.inference_mode()
    def predict_rgb(self, rgb):
        """Memory uint8 RGB -> CPU float conversion/normalize/forward/sigmoid -> uint8 mask.

        Does not read files, decode images, save results or reload the model.
        """
        with torch.autocast(device_type='cpu', enabled=False):
            logits = self.model(self.prepare_tensor(rgb))
            mask = ((logits.sigmoid() > 0.5)[0,0].to(torch.uint8)*255).numpy()
        return np.ascontiguousarray(mask)


def load_model(package=PACKAGE, *, intra_op=4, inter_op=1):
    return CPUFP32(package, intra_op=intra_op, inter_op=inter_op)


def save_mask(mask, prefix):
    if mask.dtype != np.uint8 or mask.shape != (256,256) or not set(np.unique(mask)).issubset({0,255}):
        raise ValueError('Mask must be 256x256 uint8 0/255.')
    prefix = Path(prefix)
    png, binary = Path(str(prefix)+'.png'), Path(str(prefix)+'.bin')
    if png.exists() or binary.exists(): raise FileExistsError('Refusing to overwrite a mask.')
    png.parent.mkdir(parents=True, exist_ok=True)
    ok, data = cv2.imencode('.png', mask)
    if not ok: raise RuntimeError('PNG encoding failed.')
    png.write_bytes(data.tobytes()); binary.write_bytes(mask.tobytes(order='C'))
    assert binary.stat().st_size == 65536


def cpu_environment():
    name = platform.processor()
    source = 'platform.processor'
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                name = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
                source = 'Windows registry ProcessorNameString'
        except OSError: pass
    return dict(cpu=name, cpu_name_source=source, logical_cpus=os.cpu_count(),
                system=platform.platform(), windows_version=platform.win32_ver(),
                python=platform.python_version(), python_executable=os.sys.executable,
                pytorch=torch.__version__, torchvision=torchvision.__version__, numpy=np.__version__, opencv=cv2.__version__,
                device='cpu', dtype='float32', eval=True, inference_mode=True, autocast=False, batch_size=1,
                intra_op_threads=torch.get_num_threads(), inter_op_threads=torch.get_num_interop_threads(),
                mkldnn_enabled=torch.backends.mkldnn.enabled, mkldnn_conv_fp32_precision=torch.backends.mkldnn.conv.fp32_precision,
                global_fp32_precision=torch.backends.fp32_precision,
                timing_clock='time.perf_counter_ns (high-resolution wall clock)',
                affinity_and_power_plan='uncontrolled; no thread search',
                torch_config=torch.__config__.show())


def benchmark(runtime, rgb, *, warmup=5, repeats=20):
    if warmup != 5 or repeats != 20: raise ValueError('Frozen comparison protocol: warmup=5, repeats=20.')
    x = runtime.prepare_tensor(rgb)
    records = []
    with torch.inference_mode(), torch.autocast(device_type='cpu', enabled=False):
        # Each phase separately gets five warmups. No calls to CUDA/synchronize.
        for _ in range(warmup): runtime.model(x)
        for i in range(repeats):
            start = time.perf_counter_ns()
            logits = runtime.model(x)
            elapsed = (time.perf_counter_ns()-start)/1e6
            records.append(dict(scope='forward', repeat=i+1, milliseconds=elapsed))
        assert logits.dtype == torch.float32 and torch.isfinite(logits).all()
        for _ in range(warmup): runtime.predict_rgb(rgb)
        for i in range(repeats):
            start = time.perf_counter_ns()
            mask = runtime.predict_rgb(rgb)
            elapsed = (time.perf_counter_ns()-start)/1e6
            records.append(dict(scope='rgb_to_mask', repeat=i+1, milliseconds=elapsed))
    assert mask.shape == (256,256) and mask.dtype == np.uint8
    result = dict(environment=cpu_environment(), fixed_sample_rgb_sha256=hashlib.sha256(rgb.tobytes()).hexdigest(),
                  load_ms=runtime.load_ms, load_observations=1,
                  load_scope='network creation + torch.load/read state + strict load + cpu/eval; excludes imports, SHA checks and thread setup',
                  load_cache_status='OS file cache uncontrolled; source copy/previous access may warm it; NOT system cold start',
                  forward_scope='prepared CPU FP32 NCHW tensor -> model(x) only; excludes normalization, threshold and saving',
                  rgb_to_mask_scope='in-memory uint8 RGB -> FP32/255/CHW/ImageNet normalize + forward + sigmoid>0.5 + uint8 mask; excludes file IO, decode, load, GUI, save',
                  warmups_per_scope=warmup, measured_repeats_per_scope=repeats,
                  p95_method='NumPy percentile(q=95, method=linear)',
                  repeated_sample_is_not_accuracy_evaluation=True,
                  local_measurement_is_not_final_team_same_PC_comparison=True)
    for scope in ('forward','rgb_to_mask'):
        values = [r['milliseconds'] for r in records if r['scope'] == scope]
        result[scope] = dict(mean_ms=float(np.mean(values)), median_ms=float(np.median(values)),
                             p95_ms=float(np.percentile(values,95,method='linear')), count=len(values))
    return result, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', default=str(PACKAGE))
    parser.add_argument('--input', required=True, help='196608-byte formal RGB888 file')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--interop', type=int, default=1)
    actions = parser.add_subparsers(dest='action', required=True)
    infer = actions.add_parser('infer'); infer.add_argument('--output-prefix', required=True)
    bench = actions.add_parser('benchmark'); bench.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    if args.action == 'benchmark':
        out = Path(args.output_dir)
        if out.exists(): raise FileExistsError('Use a new benchmark output directory.')
    rgb = read_rgb888(args.input)
    runtime = load_model(args.package, intra_op=args.threads, inter_op=args.interop)
    if args.action == 'infer':
        save_mask(runtime.predict_rgb(rgb), args.output_prefix)
        print(json.dumps(dict(device='cpu', load_ms=runtime.load_ms, mask_bytes=65536, threshold='sigmoid(logits)>0.5')))
    else:
        report, records = benchmark(runtime, rgb)
        report['input_file'] = str(Path(args.input).resolve())
        report['weights_sha256'] = runtime.identity['folded_weights_sha256']
        out.mkdir(parents=True)
        (out/'benchmark.json').write_text(json.dumps(report,indent=2,ensure_ascii=False), 'utf-8')
        # Include the single load observation as well as all 40 steady-state timings.
        with (out/'timings.csv').open('w',encoding='utf-8-sig',newline='') as f:
            w = csv.DictWriter(f,fieldnames=['scope','repeat','milliseconds']); w.writeheader()
            w.writerow(dict(scope='first_model_load', repeat=1, milliseconds=runtime.load_ms)); w.writerows(records)
        print(json.dumps({k:report[k] for k in ('load_ms','forward','rgb_to_mask')},indent=2))


if __name__ == '__main__': main()
