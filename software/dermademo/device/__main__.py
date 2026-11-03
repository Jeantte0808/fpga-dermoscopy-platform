"""Run the software laboratory without loading Qt or changing user settings."""

import argparse
import json
from pathlib import Path
import sys

from .diagnostics import save_lab_result
from .imaging import diagnostic_pattern
from .lab import LabMode, LabRequest, run_loopback
from ..diagnostic_logging import entry_session, record_exception


@entry_session("lab_cli")
def main(argv=None):
    parser = argparse.ArgumentParser(description="127.0.0.1 软件通信实验；不连接 FPGA，不加载候选 JSON 配置")
    parser.add_argument("--mode", choices=("rgb", "gaussian", "mask-fixture"), default="rgb")
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--output", type=Path, help="现有目录；创建独立诊断子目录，不覆盖处理记录")
    args = parser.parse_args(argv)
    mode = {"rgb": LabMode.RGB_BYPASS, "gaussian": LabMode.GAUSSIAN, "mask-fixture": LabMode.MASK_FIXTURE}[args.mode]
    try:
        result = run_loopback(LabRequest(diagnostic_pattern(), mode, args.timeout))
        output = save_lab_result(args.output, result) if args.output else None
    except (Exception, KeyboardInterrupt) as exc:
        record_exception("lab_failed", exc, stage="lab_cli")
        print(f"Local software laboratory failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"origin": result.origin, "hardware_verified": False, "mode": mode.name,
                      "returned_bytes": len(result.returned), "retries": 0,
                      "timings_ms": dict(result.timings_ms), "output": str(output) if output else None}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
