"""CPU entry shared by source/frozen console; never opens a network session."""
import argparse
import json
from pathlib import Path

from .cpu_baseline import CPUClient,save_comparison,load_comparison


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('infer','benchmark','reopen'))
    parser.add_argument('--python',type=Path)
    parser.add_argument('--input',type=Path,help='196608-byte actual RGB888')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--record',type=Path)
    args=parser.parse_args(argv)
    client=None
    try:
        if args.action=='reopen':
            if args.record is None:raise ValueError('reopen requires --record')
            rgb,mask,report,fpga,meta=load_comparison(args.record)
            value={'complete':True,'record_directory':str(args.record),'model_inference_performed':False,
                   'network_used':False,'rgb_sha256':report['rgb_sha256']}
        else:
            if args.python is None or args.input is None or args.output is None:
                raise ValueError('infer/benchmark require --python --input --output')
            client=CPUClient(args.python)
            rgb=args.input.read_bytes();mask,report=client.run(rgb,benchmark=args.action=='benchmark')
            args.output.mkdir(parents=True,exist_ok=True)
            saved=save_comparison(args.output,rgb,mask,report)
            value={'complete':True,'record_directory':str(saved),'device':'cpu',
                   'network_used':False,'report':report}
        print(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False));return 0
    except Exception as exc:
        print(json.dumps({'complete':False,'network_used':False,'error':str(exc)},ensure_ascii=False));return 1
    finally:
        if client is not None:client.close()
