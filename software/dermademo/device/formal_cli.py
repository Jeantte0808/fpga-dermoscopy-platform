"""Formal Mask console entry; inspect/replay never create a network connection."""

import argparse
import json
from pathlib import Path

from ..formal_results import replay_pair
from ..runtime_paths import default_output
from ..storage import save_result
from .formal_protocol import assets
from .formal_session import FormalConfig, run_formal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'replay', 'run', 'preprocess-check', 'roi-check', 'edge-check', 'prepare'), nargs='?', default='inspect')
    parser.add_argument('--roi',nargs=4,type=int,metavar=('X0','Y0','X1','Y1'),help='方向校正原图整数半开区间，各边至少16像素')
    parser.add_argument('--capture', type=Path)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--input', type=Path, help='仅紧密 256×256 RGB888；默认冻结样例')
    source.add_argument('--image', type=Path, help='原始8位不透明RGB JPEG/PNG，按冻结整图入口转换')
    parser.add_argument('--output', type=Path, default=default_output())
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--source-state', type=Path)
    parser.add_argument('--reuse-model', action='store_true')
    parser.add_argument('--telemetry-mode', choices=('summary', 'full'), default='summary', help='常规演示3页 / 完整诊断249页')
    parser.add_argument('--no-operation-trace', action='store_true', help='关闭逐call内存采样（仍保留分项计时）')
    parser.add_argument('--hardware-profile', choices=('unspecified', 'v6-r1-flash', 'p7', 'p7-flash'), default='unspecified',
                        help='显式声明，非自动识别；p7=临时下载，p7-flash=用户确认Flash固化，DDR首次仍加载模型')
    parser.add_argument('--pc', default='192.168.0.3')
    parser.add_argument('--pc-port', type=int, default=6102)
    parser.add_argument('--board', default='192.168.0.2')
    parser.add_argument('--board-port', type=int, default=5000)
    parser.add_argument('--timeout', type=float, default=.5)
    parser.add_argument('--operation-timeout', type=float, default=30)
    args = parser.parse_args(argv)
    try:
        manifest, blob, sample = assets()
        from ..formal_input import prepare_image, verify_preprocessing
        def prepare_selected():
            if args.roi is None:return prepare_image(args.image)
            from ..roi import decode_source,get_roi_processor
            return get_roi_processor().prepare(decode_source(args.image),tuple(args.roi))
        if args.roi is not None and args.image is None:
            raise ValueError('--roi 必须与 --image 一起使用')
        if args.action == 'edge-check':
            from ..roi_edge import verify_edge_samples
            result = verify_edge_samples()
        elif args.action == 'roi-check':
            from ..roi_preprocessing import verify_roi_preprocessing
            result = verify_roi_preprocessing()
        elif args.action == 'preprocess-check':
            result = verify_preprocessing()
        elif args.action == 'prepare':
            if args.image is None:
                raise ValueError('prepare 需要 --image')
            verify_preprocessing()
            prepared = prepare_selected()
            args.output.mkdir(parents=True, exist_ok=True)
            # New directory only: never overwrite a prior input or record.
            from uuid import uuid4
            target = args.output / ('prepared_' + uuid4().hex[:12])
            target.mkdir(exist_ok=False)
            from .formal_session import write_bytes, write_json
            from .formal_capture import png_bytes
            write_bytes(target / 'input.rgb', prepared.rgb)
            write_bytes(target / 'input.png', png_bytes(prepared.rgb, 3))
            write_bytes(target / 'source_image.bin', prepared.source_bytes)
            write_json(target / 'input_context.json', prepared.context)
            result = dict(prepared=True, network_used=False, directory=str(target), input_context=prepared.context)
        elif args.action == 'inspect':
            result = dict(assets_valid=True, image_id=manifest['image_id'], parameter_bytes=len(blob),
                          fragments=len(manifest['fragments']), network_used=False)
        elif args.action == 'replay':
            request, value = replay_pair(args.capture)
            args.output.mkdir(parents=True, exist_ok=True)
            saved = save_result(args.output, request, value)
            result = dict(complete=True, source_kind=value.metadata['source_kind'], network_used=False,
                          record_directory=str(saved), graph_elapsed_ms=value.metadata['graph_elapsed_ms'],
                          image_E2E_ms=value.metadata['image_E2E_ms'], five_fps_accepted=False)
        else:
            prepared = None
            if args.image:
                verify_preprocessing()
                prepared = prepare_selected()
            config = FormalConfig(args.pc, args.pc_port, args.board, args.board_port,
                                  args.timeout, args.operation_timeout, args.reuse_model,
                                  args.telemetry_mode, not args.no_operation_trace, args.hardware_profile)
            extra = {'input_context': prepared.context, 'source_bytes': prepared.source_bytes,
                     'cpu_preprocessing_ms': prepared.cpu_preprocessing_ms} if prepared else {}
            raw = prepared.rgb if prepared else args.input.read_bytes() if args.input else sample
            outcome = run_formal(raw, config, args.output,
                                 state_dir=args.state_dir, source_state=args.source_state, **extra)
            result = dict(outcome.report, capture_directory=str(outcome.directory),
                          graph_elapsed_ms=outcome.profile.get('graph_elapsed_ms') if outcome.profile else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('complete') is False else 0
    except Exception as exc:
        print(json.dumps(dict(complete=False, error=type(exc).__name__ + ': ' + str(exc)), ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
