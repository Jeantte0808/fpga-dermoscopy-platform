"""External resident CPU classifier. Never import Torch inside the GUI process."""
import argparse,base64,io,json,sys
from pathlib import Path

def main():
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser();p.add_argument('--package',type=Path,required=True);a=p.parse_args()
    sys.path.insert(0,str(a.package/'software'))
    from classification_core import load_model,decode_classification_image
    runner=load_model(a.package/'best_model.pth',threads=4,interop_threads=1)
    print(json.dumps(dict(ready=True,environment=dict(device='cpu'))),flush=True)
    for line in sys.stdin:
        try:
            request=json.loads(line);result=runner.predict(Path(request['path']))
            preview,identity=decode_classification_image(request['path'])
            if identity!=result['identity']:raise ValueError('classification source changed')
            data=io.BytesIO();preview.save(data,format='PNG')
            print(json.dumps(dict(result=result,preview=base64.b64encode(data.getvalue()).decode('ascii')),ensure_ascii=False),flush=True)
        except Exception as exc:print(json.dumps(dict(error=type(exc).__name__+': '+str(exc)),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
