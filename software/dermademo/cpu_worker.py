"""Standalone resident CPU process. Run with an explicitly selected Python runtime.

Only this process imports torch. Stdout is a line-delimited JSON protocol; package
loading and CPU thread configuration happen once before serving requests.
"""
import argparse
import base64
from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', required=True)
    args = parser.parse_args()
    package = Path(args.package).resolve()
    sys.path.insert(0, str(package/'software'))
    # Verify the supplied loader, weights and identity before importing/loading them.
    manifest = json.loads((package/'manifest.json').read_text('utf-8'))
    for entry in manifest['files']:
        path = (package/entry['path']).resolve()
        if not path.is_relative_to(package):
            raise ValueError('Package path escapes root')
        data = path.read_bytes()
        if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('CPU package hash mismatch: '+entry['path'])
    with redirect_stdout(sys.stderr):
        from cpu_fp32 import load_model, benchmark, cpu_environment
        import numpy as np
        runtime = load_model(package, intra_op=4, inter_op=1)
        environment = cpu_environment()
    def emit(value):
        print(json.dumps(value, allow_nan=False), flush=True)
    emit({'ready': True, 'load_ms':runtime.load_ms, 'identity':runtime.identity, 'environment':environment})
    for line in sys.stdin:
        try:
            req = json.loads(line)
            if req['action'] == 'close':
                return 0
            raw = base64.b64decode(req['rgb'], validate=True)
            if len(raw) != 196608:
                raise ValueError('Expected 196608 RGB888 bytes')
            rgb = np.frombuffer(raw,np.uint8).reshape(256,256,3)
            with redirect_stdout(sys.stderr):
                if req['action'] == 'infer':
                    started = time.perf_counter_ns()
                    # Preserve member2 normalization/order/sigmoid/strict threshold.
                    mask = runtime.predict_rgb(rgb)
                    elapsed = (time.perf_counter_ns()-started)/1e6
                    report = {'rgb_to_mask_ms':elapsed, 'timing_scope':'in_memory_RGB_to_mask_excludes_IPC_IO_load_GUI',
                              'warmup':False, 'steady_state_benchmark':False}
                    records = []
                elif req['action'] == 'benchmark':
                    report,records = benchmark(runtime,rgb)
                    mask = runtime.predict_rgb(rgb)
                else:
                    raise ValueError('Unknown action')
            if mask.dtype != np.uint8 or mask.shape != (256,256) or not np.isin(mask,(0,255)).all():
                raise ValueError('Invalid CPU mask')
            emit({'ok':True,'rgb_sha256':hashlib.sha256(raw).hexdigest(),
                  'mask':base64.b64encode(mask.tobytes()).decode('ascii'), 'report':report,'records':records})
        except Exception as exc:
            emit({'ok':False,'error':f'{type(exc).__name__}: {exc}'})
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'ready':False,'error':f'{type(exc).__name__}: {exc}'}),flush=True)
        raise SystemExit(1)
