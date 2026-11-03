"""Same-input FP32/INT8 comparison workspace; no FPGA transport commands."""
from hashlib import sha256
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QPushButton,
                               QLineEdit,QFileDialog,QScrollArea,QWidget)

from .cpu_baseline import CPUClient,default_python,agreement,save_comparison,load_comparison
from .formal_input import prepare_image,EDUCATION_NOTICE
from .records import load_record
from .rgb_device_ui import RGBWorker
from .gui import ImageView
from ._fluent.style import set_fluent_property
from .cpu_runtime import selected_python


class CPUDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent)
        self.setWindowTitle('同模型CPU FP32对照')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.client=None;self._thread=None;self._completion=None;self._closing=False
        self.rgb=self.fpga_mask=self.fpga_metadata=self.success=self.last_export=None
        outer=QVBoxLayout(self);outer.setContentsMargins(16,14,16,14)
        notice=QLabel('同模型FP32 CPU · sigmoid(logits)>0.5；FPGA INT8 · q>=1。'
                      '对照必须使用同一份实际RGB；Mask差异不等于错误，Dice一致率不是标注精度。')
        notice.setWordWrap(True);notice.setObjectName('algorithmNotice');outer.addWidget(notice)
        runtime=QHBoxLayout();runtime.addWidget(QLabel('CPU Python'))
        self.settings=parent.preferences.settings
        self.python_edit=QLineEdit(selected_python(parent.preferences))
        self.python_edit.setAccessibleName('独立CPU推理环境Python路径')
        self.python_edit.setPlaceholderText('选择已安装CPU依赖的python.exe；依赖安装见随包说明')
        self.python_button=QPushButton('选择解释器');runtime.addWidget(self.python_edit,1);runtime.addWidget(self.python_button)
        outer.addLayout(runtime)
        self.runtime_save=QPushButton('检查 / 保存共享CPU Python配置');runtime.addWidget(self.runtime_save)
        self.runtime_save.clicked.connect(lambda:parent.open_cpu_settings(self.python_edit.text()))
        parent.cpu_runtime_changed.connect(self.python_edit.setText)
        commands=QHBoxLayout()
        self.image_button=QPushButton('打开JPEG/PNG')
        self.record_button=QPushButton('采用FPGA记录的实际RGB')
        self.current_button=QPushButton('采用主窗当前FPGA结果')
        self.open_button=QPushButton('重开CPU对照记录')
        for w in (self.image_button,self.record_button,self.current_button,self.open_button):commands.addWidget(w)
        outer.addLayout(commands)
        self.input_label=QLabel('尚未载入实际256×256 RGB888；本工作区不连接FPGA。')
        self.input_label.setWordWrap(True);self.input_label.setTextFormat(Qt.TextFormat.PlainText);outer.addWidget(self.input_label)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content=QWidget();grid=QGridLayout(content);self.views=[]
        for i,title in enumerate(('实际模型RGB输入','CPU FP32 Mask','FPGA INT8 Mask（已保存/当前帧）','差异像素 · 白色表示不同')):
            label=QLabel(title);label.setWordWrap(True);grid.addWidget(label,(i//2)*2,i%2)
            view=ImageView('待运行/载入',smooth=i==0);view.setMinimumHeight(150)
            grid.addWidget(view,(i//2)*2+1,i%2);self.views.append(view)
        scroll.setWidget(content);outer.addWidget(scroll,1)
        self.result_label=QLabel('加载一次，后续输入复用同一CPU进程；基准测试每个范围预热5次、测量20次。')
        self.result_label.setWordWrap(True);self.result_label.setTextFormat(Qt.TextFormat.PlainText);outer.addWidget(self.result_label)
        self.status=QLabel('就绪 · Enter运行CPU；Ctrl+S保存最近成功对照。不自动计算加速比。')
        self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText);outer.addWidget(self.status)
        actions=QHBoxLayout()
        self.run_button=QPushButton('运行CPU FP32');self.run_button.setProperty('fluentAppearance','primary')
        self.benchmark_button=QPushButton('预热5次 / 测量20次')
        self.save_button=QPushButton('保存对照和原始计时');self.cancel_button=QPushButton('取消CPU任务');self.close_button=QPushButton('关闭')
        for w in (self.run_button,self.benchmark_button,self.save_button,self.cancel_button,self.close_button):
            w.setAutoDefault(False);actions.addWidget(w)
        outer.addLayout(actions)
        footer=QLabel(EDUCATION_NOTICE);footer.setObjectName('muted');footer.setWordWrap(True);outer.addWidget(footer)
        self.python_button.clicked.connect(self.choose_python);self.image_button.clicked.connect(self.choose_image)
        self.record_button.clicked.connect(self.choose_record);self.current_button.clicked.connect(self.use_current)
        self.open_button.clicked.connect(self.choose_open);self.run_button.clicked.connect(lambda:self.run_cpu(False))
        self.benchmark_button.clicked.connect(lambda:self.run_cpu(True));self.save_button.clicked.connect(self.choose_save)
        self.cancel_button.clicked.connect(self.cancel);self.close_button.clicked.connect(self.close)
        for key,fn in ((Qt.Key.Key_Return,self.run_button.click),(Qt.Key.Key_Enter,self.run_button.click),('Ctrl+S',self.save_button.click)):
            shortcut=QShortcut(QKeySequence(key),self);shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.setAutoRepeat(False);shortcut.activated.connect(fn)
        screen=self.screen().availableGeometry();self.resize(min(1100,screen.width()-40),min(800,screen.height()-60))
        self._refresh()
        if parent.preferences.settings.value('cpu/python','') and not self.python_edit.text():self.status.setText('已保存的Python路径失效，请重新选择并检查共享CPU配置。')

    @property
    def busy(self):return self._thread is not None

    def _refresh(self):
        idle=not self.busy and not self._closing
        for w in (self.image_button,self.record_button,self.current_button,self.open_button,self.python_edit,self.python_button,self.runtime_save):w.setEnabled(idle)
        for w in (self.run_button,self.benchmark_button):w.setEnabled(idle and self.rgb is not None)
        self.save_button.setEnabled(idle and self.success is not None)
        self.cancel_button.setEnabled(self.busy and not self._thread.cancel.is_set())

    def choose_python(self):
        name,_=QFileDialog.getOpenFileName(self,'选择CPU环境python.exe','','Python (python.exe);;Executable (*.exe)')
        if name:self.python_edit.setText(name)

    def choose_image(self):
        name,_=QFileDialog.getOpenFileName(self,'按已确认整图规则准备CPU输入','','Image (*.jpg *.jpeg *.png)')
        if name:self.load_image(Path(name))

    def load_image(self,path):
        def action(cancel,progress):
            p=prepare_image(path)
            return p.rgb,None,None,f'整图 {path.name}'
        self._job('input',action)

    def choose_record(self):
        name=QFileDialog.getExistingDirectory(self,'选择FPGA record.json所在目录')
        if name:self.load_fpga_record(Path(name))

    def load_fpga_record(self,path):
        def action(cancel,progress):
            r=load_record(path)
            return self._pair_input((r.request,r.result))
        self._job('input',action)

    @staticmethod
    def _pair_input(pair):
        request,result=pair
        if result.capabilities.key!='fpga_formal_v5':raise ValueError('请选择正式FPGA模型记录，不能用临时CPU算法冒充')
        result.validate(request)
        rgb=np.ascontiguousarray(request.image_bgr[:,:,::-1]).tobytes()
        if len(rgb)!=196608 or sha256(rgb).hexdigest()!=result.metadata['input_sha256']:
            raise ValueError('FPGA实际输入摘要无效')
        meta={key:result.metadata.get(key) for key in ('input_sha256','frame_id','generation','source_kind','model_version','input_context')}
        meta['timings_ms']=dict(result.timings_ms)
        meta['stage_data_status']=result.metadata.get('stage_data_status','逐层统计不可用')
        return rgb,result.mask.tobytes(),meta,f"帧 {meta['frame_id']} · {meta['source_kind']} · 已有结果，本次未连接板卡"

    def use_current(self):
        pair=(getattr(self.parent(),'request',None),getattr(self.parent(),'result',None))
        self._job('input',lambda cancel,progress:self._pair_input(pair))

    def _set_input(self,rgb,mask,meta,label):
        self.rgb,self.fpga_mask,self.fpga_metadata=rgb,mask,meta
        self.success=None
        self.views[0].set_image(cv2.cvtColor(np.frombuffer(rgb,np.uint8).reshape(256,256,3),cv2.COLOR_RGB2BGR))
        for view in self.views[1:]:view.clear_image()
        if mask is not None:self.views[2].set_image(np.frombuffer(mask,np.uint8).reshape(256,256))
        self.input_label.setText(f'{label}\n实际RGB SHA256 {sha256(rgb).hexdigest()}')
        self.result_label.setText('当前输入尚未运行CPU；FPGA数据来源保留在对照记录中。')

    def run_cpu(self,benchmark=False):
        if self.busy or self.rgb is None:return
        python=self.python_edit.text().strip();rgb,fpga,meta=self.rgb,self.fpga_mask,self.fpga_metadata
        from .cpu_runtime import validate_path
        try:validate_path(python)
        except ValueError as exc:self.status.setText(str(exc));return
        def action(cancel,progress):
            if self.client is None or str(self.client.python)!=str(Path(python).resolve()):
                if self.client:self.client.close()
                self.client=CPUClient(python)
            mask,report=self.client.run(rgb,benchmark=benchmark,cancel=cancel)
            return rgb,mask,report,fpga,meta
        self._job('cpu',action)

    def _show_success(self,value):
        self.success=value;rgb,mask,report,fpga,meta=value
        self.views[1].set_image(np.frombuffer(mask,np.uint8).reshape(256,256))
        text=f"CPU FP32 · 初次模型加载 {report['runtime']['load_ms']:.2f} ms（非系统冷启动）\n"
        bench=report['report']
        if 'forward' in bench:
            text+='常驻范围各预热5 / 测量20；均值 / 中位 / P95：\n'
            for key,title in (('forward','纯forward'),('rgb_to_mask','RGB→Mask')):
                row=bench[key];text+=f"{title} {row['mean_ms']:.2f} / {row['median_ms']:.2f} / {row['p95_ms']:.2f} ms\n"
        else:text+=f"本次内存RGB→Mask {bench['rgb_to_mask_ms']:.2f} ms（单次，未作为稳态基准）\n"
        if fpga is not None:
            stats=agreement(mask,fpga);diff=np.where(np.frombuffer(mask,np.uint8)!=np.frombuffer(fpga,np.uint8),255,0).astype(np.uint8).reshape(256,256)
            self.views[3].set_image(diff)
            text+=f"差异 {stats['different_pixels']} px · Dice一致率 {stats['dice_agreement']:.6f}，非标注精度。\n"
            timings=meta['timings_ms'];text+=f"FPGA已有帧：板内图 {timings.get('fpga_graph','不可用')} ms / 图像E2E {timings.get('image_transport_e2e','不可用')} ms；范围不同，不自动算加速比。"
            text+='\n'+meta.get('stage_data_status','逐层统计不可用')
        self.result_label.setText(text)

    def choose_save(self):
        folder=QFileDialog.getExistingDirectory(self,'保存CPU对照、RAW和原始计时')
        if folder:self.export(Path(folder))

    def export(self,parent):
        if self.success is None or self.busy:return
        rgb,mask,report,fpga,meta=self.success
        self._job('save',lambda cancel,progress:save_comparison(parent,rgb,mask,report,fpga_mask=fpga,fpga_metadata=meta))

    def choose_open(self):
        folder=QFileDialog.getExistingDirectory(self,'重开comparison.json所在目录（不运行模型）')
        if folder:self.open_folder(Path(folder))

    def open_folder(self,folder):self._job('open',lambda cancel,progress:load_comparison(folder))

    def _job(self,kind,action):
        if self.busy or self._closing:return
        self._kind=kind;self._completion=None;self._thread=RGBWorker(action,self)
        self._thread.succeeded.connect(self._success);self._thread.failed.connect(self._failure);self._thread.finished.connect(self._finished)
        self.status.setText({'input':'正在校验实际RGB输入（不连接FPGA）','cpu':'正在CPU推理/测量；加载后的模型会常驻复用',
                             'save':'正在完整保存CPU对照','open':'正在校验重开CPU对照（不推理）'}[kind])
        self._refresh();self._thread.start()

    @Slot(object)
    def _success(self,value):self._completion=True,value
    @Slot(str)
    def _failure(self,message):self._completion=False,message
    @Slot()
    def _finished(self):
        thread,self._thread=self._thread,None;thread.deleteLater()
        try:
            if self._completion is None or not self._completion[0]:raise RuntimeError(self._completion[1] if self._completion else '任务未返回')
            value=self._completion[1]
            if self._kind=='input':self._set_input(*value)
            elif self._kind=='cpu':
                self._show_success(value)
            elif self._kind=='save':self.last_export=value
            elif self._kind=='open':
                rgb,mask,report,fpga,meta=value;self._set_input(rgb,fpga,meta,'CPU对照记录重开；本次未推理')
                self._show_success(value)
            self.status.setText(f'已完成 · {value}' if self._kind=='save' else '已完成；本工作区未发送FPGA命令。')
            set_fluent_property(self.status,'fluentSeverity','info')
        except Exception as exc:
            self.status.setText('失败 · '+str(exc)+'；上次成功结果仍可保存。')
            set_fluent_property(self.status,'fluentSeverity','danger')
        self._refresh()
        if self._closing:self.close()

    def cancel(self):
        if self.busy:self._thread.cancel.set();self.status.setText('正在结束CPU工作进程，不修改FPGA状态。');self._refresh()

    def reject(self):self.close()
    def closeEvent(self,event):
        if self.busy:self._closing=True;self.cancel();event.ignore()
        else:
            if self.client:self.client.close()
            event.accept()
