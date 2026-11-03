"""Qt-native report tables with repeated headers and bounded page fragments."""
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QTextLayout, QTextOption


class ReportLayout:
    accent=QColor('black')
    ink=QColor('black')
    muted=QColor('#333333')
    border=QColor('#555555')

    def __init__(self,writer,painter,*,historical,cancel=None):
        self.writer,self.painter=writer,painter
        self.width,self.height=writer.width(),writer.height()
        self.bottom=self.height-33
        self.historical,self.cancel=historical,cancel
        self.page=0;self.y=0;self.title='图像分析报告'

    def check(self):
        if self.cancel is not None and self.cancel.is_set():raise InterruptedError('报告导出已取消')

    @staticmethod
    def font(size=9,bold=False):
        font=QFont('SimSun',9,QFont.Weight.Bold if bold else QFont.Weight.Normal)
        font.setPointSizeF(size)
        return font

    def wrap(self,text,width,font):
        lines=[]
        for paragraph in str(text).split('\n'):
            if not paragraph:lines.append('');continue
            layout=QTextLayout(paragraph,font)
            option=QTextOption();option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            layout.setTextOption(option);layout.beginLayout()
            # QTextLine positions are UTF-16 offsets, including supplementary characters.
            encoded=paragraph.encode('utf-16-le')
            while True:
                line=layout.createLine()
                if not line.isValid():break
                line.setLineWidth(max(1,width))
                start,length=line.textStart(),line.textLength()
                lines.append(encoded[2*start:2*(start+length)].decode('utf-16-le'))
            layout.endLayout()
        return lines or ['']

    def footer(self):
        p=self.painter;p.setPen(self.border)
        p.drawLine(QPointF(0,self.height-27),QPointF(self.width,self.height-27))
        p.setFont(self.font(7.5));p.setPen(self.muted)
        p.drawText(QRectF(0,self.height-22,self.width-50,18),Qt.AlignmentFlag.AlignLeft,
                   '仅用于工程教学与算法演示，不作为临床诊断依据。')
        p.drawText(QRectF(self.width-50,self.height-22,50,18),Qt.AlignmentFlag.AlignRight,f'第{self.page}页')

    def new_page(self,title=None):
        self.check()
        if self.page:
            self.footer()
            if not self.writer.newPage():raise OSError('无法创建PDF页面')
        self.page+=1
        if title is not None:self.title=str(title)
        p=self.painter;p.fillRect(QRectF(0,0,self.width,self.height),QColor('white'))
        p.setPen(self.ink);p.setFont(self.font(17,True))
        report_title='皮肤镜分类分析报告单' if self.title.startswith('整图分类分析') else '皮肤镜图像分析报告单'
        p.drawText(QRectF(0,0,self.width,28),Qt.AlignmentFlag.AlignCenter,report_title)
        p.setFont(self.font(9))
        p.drawText(QRectF(0,39,self.width,17),Qt.AlignmentFlag.AlignLeft,
                   '姓名：____________    性别：______    年龄：______    检查部位：____________')
        p.drawLine(QPointF(0,61),QPointF(self.width,61))
        p.setFont(self.font(8.5))
        # Only show section identity; source filenames stay in the saved record.
        title_lines=self.wrap(self.title.split(' · ',1)[0],self.width-70,self.font(8.5))
        self.y=67
        for text in title_lines:
            p.drawText(QPointF(0,self.y+QFontMetricsF(self.font(8.5)).ascent()),text);self.y+=13
        p.drawText(QRectF(self.width-70,67,70,16),Qt.AlignmentFlag.AlignRight,'历史记录' if self.historical else '已保存结果')
        self.y+=8;self.content_top=self.y

    def ensure(self,height):
        self.check()
        if self.y+height>self.bottom:self.new_page(self.title)

    def section(self,title):
        self.ensure(85)
        self.y+=4;p=self.painter;p.setFont(self.font(11,True));p.setPen(self.accent)
        p.drawText(QRectF(0,self.y,self.width,20),Qt.AlignmentFlag.AlignLeft,title);self.y+=24

    def table(self,headers,rows,ratios=None,*,font_size=9,show_header=True,padding=5,gap=9,label_columns=()):
        """Split oversized rows at line boundaries; repeat column labels on every page."""
        count=len(headers);ratios=ratios or [1]*count
        widths=[self.width*r/sum(ratios) for r in ratios]
        font=self.font(font_size);metrics=QFontMetricsF(font);spacing=metrics.lineSpacing()
        pad=padding
        def draw(cells,height,*,header=False,alternate=False):
            p=self.painter;x=0
            for column,(texts,width) in enumerate(zip(cells,widths)):
                label=header or column in label_columns
                p.setFont(self.font(font_size,label))
                rect=QRectF(x,self.y,width,height)
                p.fillRect(rect,QColor('#f2f2f2') if label else QColor('white'))
                p.setPen(self.border);p.drawRect(rect)
                p.setPen(self.accent if header else self.ink)
                for i,text in enumerate(texts):
                    p.drawText(QPointF(x+pad,self.y+pad+metrics.ascent()+i*spacing),text)
                x+=width
            self.y+=height
        labels=[self.wrap(h,w-2*pad,self.font(font_size,True)) for h,w in zip(headers,widths)]
        header_height=max(map(len,labels))*spacing+pad*2 if show_header else 0
        def draw_header():
            if show_header:draw(labels,header_height,header=True)
        rows=list(rows)
        first_height=(max(len(self.wrap(value,width-2*pad,font)) for value,width in zip(rows[0],widths))*spacing+pad*2
                      if rows else spacing+pad*2)
        self.ensure(header_height+min(first_height,self.bottom-140))
        draw_header()
        for index,row in enumerate(rows):
            if len(row)!=count:raise ValueError('报告表格列数不符')
            cells=[self.wrap(value,width-2*pad,self.font(font_size,column in label_columns))
                   for column,(value,width) in enumerate(zip(row,widths))]
            # Keep ordinary rows together; split only rows taller than a fresh page.
            required=max(map(len,cells))*spacing+pad*2
            fresh_capacity=self.bottom-self.content_top-header_height
            if self.y+required>self.bottom and required<fresh_capacity:
                self.new_page(self.title);draw_header()
            while any(cells):
                self.check()
                capacity=int((self.bottom-self.y-pad*2)//spacing)
                if capacity<1:
                    self.new_page(self.title);draw_header()
                    capacity=int((self.bottom-self.y-pad*2)//spacing)
                take=min(max(map(len,cells)),capacity)
                draw([text[:take] for text in cells],take*spacing+pad*2,alternate=index%2==1)
                cells=[text[take:] for text in cells]
                if any(cells):
                    self.new_page(self.title);draw_header()
                    if 0 in label_columns and not cells[0]:
                        cells[0]=self.wrap(str(row[0])+'（续）',widths[0]-2*pad,self.font(font_size,True))
        self.y+=gap

    def measurements(self,pairs):
        """Aligned label/value cells, including an empty final pair for odd counts."""
        rows=[]
        for index in range(0,len(pairs),2):
            left=pairs[index];right=pairs[index+1] if index+1<len(pairs) else ('','')
            rows.append((*left,*right))
        self.table(('项目','结果','项目','结果'),rows,(1.1,1.35,1.1,1.75),
                   show_header=False,padding=4,gap=5,label_columns=(0,2))

    def pairs(self,pairs):
        font=self.font(9);metrics=QFontMetricsF(font)
        for i in range(0,len(pairs),2):
            entries=pairs[i:i+2]
            wrapped=[self.wrap(f'{label}：{value}',self.width/2-12,font) for label,value in entries]
            height=max(map(len,wrapped))*metrics.lineSpacing()+9
            self.ensure(height);p=self.painter;p.setFont(font);p.setPen(self.ink)
            for column,lines in enumerate(wrapped):
                for n,text in enumerate(lines):p.drawText(QPointF(column*self.width/2,self.y+metrics.ascent()+n*metrics.lineSpacing()),text)
            self.y+=height
        self.y+=7

    def images(self,items,*,height=145):
        """Items: caption, BGR/uint8 mask, explanatory caption, binary-nearest flag."""
        import numpy as np
        gap=12;cell_width=(self.width-gap)/2
        for offset in range(0,len(items),2):
            pair=items[offset:offset+2]
            font=self.font(8)
            captions=[self.wrap(item[2],cell_width-16,font) for item in pair]
            note_height=max(map(len,captions))*QFontMetricsF(font).lineSpacing()
            block=height+35+note_height+8
            self.ensure(block)
            for col,((title,array,_note,binary),notes) in enumerate(zip(pair,captions)):
                self.check();x=col*(cell_width+gap);p=self.painter
                p.setPen(self.border);p.drawRect(QRectF(x,self.y,cell_width,block))
                p.drawLine(QPointF(x,self.y+23),QPointF(x+cell_width,self.y+23))
                p.setFont(self.font(9));p.setPen(self.ink)
                if title[:2].isdigit():title=title[3:]
                p.drawText(QRectF(x+8,self.y+6,cell_width-16,18),Qt.AlignmentFlag.AlignCenter,title)
                rgb=np.repeat(array[:,:,None],3,axis=2) if array.ndim==2 else array[:,:,::-1]
                rgb=np.ascontiguousarray(rgb)
                image=QImage(rgb.data,rgb.shape[1],rgb.shape[0],rgb.strides[0],QImage.Format.Format_RGB888).copy()
                scale=min((cell_width-16)/image.width(),height/image.height())
                w,h=image.width()*scale,image.height()*scale
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,not binary)
                p.drawImage(QRectF(x+(cell_width-w)/2,self.y+27+(height-h)/2,w,h),image)
                p.setFont(font);p.setPen(self.muted)
                for i,text in enumerate(notes):
                    p.drawText(QPointF(x+8,self.y+height+32+QFontMetricsF(font).ascent()+i*QFontMetricsF(font).lineSpacing()),text)
            self.y+=block+4

    def finish(self):
        self.check();self.footer()

    def clinical_text(self,label,text,*,size=9):
        """A ruled label/content row; oversized content continues without truncation."""
        self.table(('栏目','内容'),[(label,str(text))],(.75,4.55),font_size=size,
                   show_header=False,padding=3,gap=0,label_columns=(0,))

    def reviewer(self):
        self.ensure(QFontMetricsF(self.font()).lineSpacing()*3+16)
        self.table(('栏目','内容'),[('审核意见','\n'),('审核签署','审核人：________________    日期：________________')],
                   (.75,4.55),show_header=False,padding=4,gap=0,label_columns=(0,))
