"""Recover saved responses locally; no MainWindow, credential read or network call."""
import argparse
import json
from pathlib import Path


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task',type=Path,required=True,help='原AI任务目录；保留不改动')
    parser.add_argument('--record',type=Path,required=True,help='包含record.json的对应本地原记录目录')
    args=parser.parse_args(argv)
    from .gui import create_application
    from .ai_service import recover_saved_response
    app=create_application()
    try:
        result=recover_saved_response(args.task,args.record)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result['meta']['pdf_complete'] else 1
    except Exception as exc:
        from .ai_diagnostics import exception_diagnostic
        print(json.dumps({'status':'offline_recovery_failed','diagnostic':exception_diagnostic(exc,'offline.recover',include_message=False)},ensure_ascii=False,indent=2))
        return 1
