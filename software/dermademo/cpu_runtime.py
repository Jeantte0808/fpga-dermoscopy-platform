"""One checked local Python path shared by every PC model workflow."""
import json
from pathlib import Path
import subprocess
from time import monotonic


PROBE='''import json,sys
import numpy,PIL,torch,torchvision
torch.set_num_threads(1)
x=torch.tensor([1.,2.],device='cpu')
assert torch.isfinite(torch.softmax(x,0)).all()
print(json.dumps(dict(ok=True,executable=sys.executable,device='cpu',versions=dict(torch=torch.__version__,torchvision=torchvision.__version__,numpy=numpy.__version__,pillow=PIL.__version__))))
'''


def validate_path(value):
    if not isinstance(value,str) or any(c in value for c in ('\r','\n','\x00')):raise ValueError('请选择本机python.exe完整路径')
    path=Path(value.strip())
    if not path.is_absolute() or path.name.lower()!='python.exe' or not path.is_file():
        raise ValueError('Python路径已失效或不是本机python.exe，请重新选择；这里不填写网址。')
    return path.resolve()


def selected_python(preferences):
    saved=preferences.settings.value('cpu/python','')
    if saved:
        try:return str(validate_path(saved))
        except ValueError:return ''
    from .cpu_baseline import default_python
    return default_python()


def probe_python(value,*,cancel=None):
    path=validate_path(value)
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    process=subprocess.Popen([str(path),'-I','-B','-c',PROBE],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=flags)
    end=monotonic()+60
    try:
        while True:
            if cancel is not None and cancel.is_set():raise InterruptedError('CPU依赖检查已取消')
            if monotonic()>end:raise TimeoutError('CPU依赖检查超时，请重新选择已安装依赖的Python')
            try:
                output,_=process.communicate(timeout=.1);break
            except subprocess.TimeoutExpired:pass
        if process.returncode or len(output)>32768:raise ValueError('CPU依赖检查失败，需要torch、torchvision、NumPy和Pillow；未自动安装依赖。')
        result=json.loads(output)
        if result.get('ok') is not True or result.get('device')!='cpu':raise ValueError('CPU环境检查结果无效')
        if Path(result['executable']).resolve()!=path:raise ValueError('实际Python与所选路径不符')
        return dict(path=str(path),probe=result)
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)


def save_checked(preferences,value):
    path=validate_path(value['path']);probe=value['probe']
    if probe.get('ok') is not True or probe.get('device')!='cpu' or Path(probe['executable']).resolve()!=path:
        raise ValueError('必须先检查当前Python依赖成功，再保存路径')
    if not preferences._write({'cpu/python':str(path),'cpu/probe':json.dumps(probe,ensure_ascii=False)}):
        raise OSError('CPU Python路径保存失败；原配置继续保留')
    return str(path)
