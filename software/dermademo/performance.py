"""Explicit timing labels; unavailable values never borrow a previous frame."""
def timing_text(result):
    meta = result.metadata
    values = meta.get('stage_timings_ms', {})
    labels = [('RGB上传','rgb_upload'),('首Mask等待','first_mask_wait'),('其余Mask','remaining_mask'),
              ('FINISH','finish'),('统计查询','statistics'),('本地保存','local_save'),('显示准备','display_prepare')]
    fmt = lambda v: f'{v:.2f} ms' if type(v) in (int,float) else '不可用'
    parts = [f'{name} {fmt(values.get(key))}' for name,key in labels]
    return ('；'.join(parts) + '\n' + meta.get('stage_data_status','逐层统计不可用')
            + '；操作至界面提交 ' + fmt(meta.get('user_visible_wait_ms'))
            + '（含统计、本地保存及显示提交，不含异步屏幕绘制）')
