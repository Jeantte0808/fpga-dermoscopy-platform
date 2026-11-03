"""Explicit experiment runner and read-only inspect/reopen entry points."""
import argparse
import ipaddress
import json
from pathlib import Path
from .hardware_lab import run,reopen,evidence
from .device.formal_session import FormalConfig
from .hardware import DEPLOYMENT_PROFILE
from .runtime_paths import default_output


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('run','reopen','inspect','report'))
    parser.add_argument('--record',type=Path);parser.add_argument('--experiment',type=Path)
    parser.add_argument('--image',type=Path,nargs='+');parser.add_argument('--output',type=Path)
    parser.add_argument('--python',default='');parser.add_argument('--mode',choices=('paired','fpga_sequence'),default='paired')
    parser.add_argument('--warmup',type=int,default=5);parser.add_argument('--repeat',type=int,default=20)
    parser.add_argument('--pc',default='192.168.0.3');parser.add_argument('--pc-port',type=int,default=6102)
    parser.add_argument('--board',default='192.168.0.2');parser.add_argument('--board-port',type=int,default=5000)
    parser.add_argument('--reuse-model',action='store_true');parser.add_argument('--confirm-live',action='store_true')
    parser.add_argument('--telemetry-mode',choices=('summary','full'),default='full');parser.add_argument('--state-dir',type=Path)
    args=parser.parse_args(argv)
    try:
        if args.action=='report':
            if args.experiment is None:raise ValueError('需要--experiment')
            from .gui import create_application
            from .hardware_lab_pdf import export
            app=create_application()
            value=dict(pdf=str(export(args.experiment,historical=True)),inference_performed=False,cloud_calls=0)
        elif args.action=='reopen':
            if args.experiment is None:raise ValueError('需要--experiment')
            value=reopen(args.experiment)
        elif args.action=='inspect':
            if args.record is None:raise ValueError('需要--record')
            from .records import load_record
            r=load_record(args.record);value=evidence((r.request,r.result))
        else:
            if not args.image:raise ValueError('需要--image')
            if not ipaddress.ip_address(args.board).is_loopback and not args.confirm_live:
                raise ValueError('真实板卡实验须显式--confirm-live，先确认部署和模型加载设置')
            from .formal_input import prepare_image
            prepared=[prepare_image(p) for p in args.image]
            config=FormalConfig(pc=args.pc,pc_port=args.pc_port,board=args.board,board_port=args.board_port,
                reuse_model=args.reuse_model,telemetry_mode=args.telemetry_mode,hardware_profile=DEPLOYMENT_PROFILE)
            folder,doc=run(prepared,config,args.output or default_output(),python=args.python,
                warmup=args.warmup,repeats=args.repeat,mode=args.mode,state_dir=args.state_dir)
            value=dict(directory=str(folder),document=doc)
            print(json.dumps(value,ensure_ascii=True));return 0 if doc['status']=='completed' else 1
        print(json.dumps(value,ensure_ascii=True));return 0
    except Exception as exc:
        print(json.dumps(dict(error=f'{type(exc).__name__}: {exc}'),ensure_ascii=True));return 1


if __name__=='__main__':raise SystemExit(main())
