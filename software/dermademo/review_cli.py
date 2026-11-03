"""Read-only inspection or offline export of an explicitly selected review."""
import argparse,json
from pathlib import Path


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('inspect').add_argument('--record',type=Path,required=True)
    sub.add_parser('export').add_argument('--review',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='inspect':
            from .review import prepare
            result=prepare(args.record)
            value=dict(status='PASS',identity=result['identity'],measurement=result['measurement'],quality=result['quality'])
        else:
            from .gui import create_application
            from .review_pdf import export_review_pdf
            app=create_application();value=dict(status='PASS',pdf=str(export_review_pdf(args.review)))
        print(json.dumps(value,ensure_ascii=False,indent=2));return 0
    except Exception as exc:
        print(json.dumps(dict(status='failed',error=str(exc)),ensure_ascii=False));return 1
