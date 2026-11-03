"""Canonical console dispatcher, shared by source and frozen entry points."""

import argparse
import json
from pathlib import Path
import sys

from . import __version__
from .diagnostic_logging import entry_session, record_exception
from .runtime_paths import default_output, distribution_root


@entry_session("tools")
def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="PC 工具（启动不会自动连接板卡）",
        epilog="冻结版 batch/rgb-udp 未指定 --output 时写用户 Documents/AlienHomeDerma/outputs；源码版仍为 outputs。")
    parser.add_argument("command", choices=("batch", "lab", "rgb-udp", "formal-mask", "cpu-baseline", "dataset", "verify-resources", "acceptance-check", "diagnostics", "ai-transport-check", "ai-recover-response", "classify", "review", "references", "hardware-lab"))
    parser.add_argument("--version", action="version", version=__version__)
    if not arguments or arguments[0] in {"-h", "--help", "--version"}:
        parser.parse_args(arguments)
    command = parser.parse_args(arguments[:1]).command
    rest = arguments[1:]
    if command in {"batch", "rgb-udp"} and getattr(sys, "frozen", False):
        if not any(value == "--output" or value.startswith("--output=") for value in rest):
            rest += ["--output", str(default_output())]
    if command=='hardware-lab':
        from .hardware_lab_cli import main as run
    elif command=='references':
        from .reference_library import main as run
    elif command == "ai-transport-check":
        from .ai_transport import main as run
    elif command == "ai-recover-response":
        from .ai_recover_cli import main as run
    elif command == "classify":
        from .classification import main as run
    elif command == "review":
        from .review_cli import main as run
    elif command == "batch":
        from .batch import main as run
    elif command == "lab":
        from .device.__main__ import main as run
    elif command == "rgb-udp":
        from .device.rgb_udp import main as run
    elif command == 'formal-mask':
        from .device.formal_cli import main as run
    elif command == 'cpu-baseline':
        from .cpu_cli import main as run
    elif command == "dataset":
        from .isic2016 import main as run
    else:
        subparser = argparse.ArgumentParser(prog=f"AlienHomeTools {command}")
        if command == "acceptance-check":
            subparser.add_argument("directory", nargs="?", type=Path, default=distribution_root() / "acceptance")
        elif command == "diagnostics":
            subparser.add_argument("directory", type=Path, help="已存在的本地文件夹；仅导出脱敏事件，不附图片")
        args = subparser.parse_args(rest)
        try:
            if command == "verify-resources":
                from .resources import verify_resources
                value = verify_resources()
            elif command == "acceptance-check":
                from .acceptance import verify_suite
                value = verify_suite(args.directory)
            else:
                from .diagnostic_bundle import export_diagnostics
                value = {"diagnostic_zip": str(export_diagnostics(args.directory))}
            print(json.dumps(value, ensure_ascii=False, indent=2))
            return 0
        except Exception as exc:
            record_exception("tool_failed", exc, stage=command)
            print(f"{command} failed: {exc}", file=sys.stderr)
            return 1
    return run(rest)


if __name__ == "__main__":
    raise SystemExit(main())
