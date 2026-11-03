"""Synthetic checks ONLY; no images/models/GUI/hardware are used."""
import json
import numpy as np
from roi_edge_contact import check_edge_contact, MESSAGE


def run_tests():
    cases=[]
    def accept(name, mask, expected):
        before=mask.copy()
        actual=check_edge_contact(mask)
        assert actual['side_foreground_counts']==expected,(name,actual,expected)
        sides=[side for side in ('top','bottom','left','right') if expected[side]>0]
        assert actual['touched_sides']==sides and actual['edge_contact']==bool(sides)
        assert actual['message']==MESSAGE and np.array_equal(mask,before)
        cases.append(dict(name=name,category='synthetic constructed mask',passed=True,result=actual,mask_unchanged=True))
    zero=dict(top=0,bottom=0,left=0,right=0)
    a=np.zeros((256,256),np.uint8);accept('empty',a,zero)
    a[80:100,90:120]=255;accept('interior',a,zero)
    a=np.zeros((256,256),np.uint8);a[1,1:255]=255;accept('one_pixel_inward_does_not_trigger',a,zero)
    for side,point in [('top',(0,80)),('bottom',(255,90)),('left',(70,0)),('right',(60,255))]:
        a=np.zeros((256,256),np.uint8);a[point]=255
        expected=dict(zero);expected[side]=1;accept('single_'+side,a,expected)
    a=np.zeros((256,256),np.uint8);a[0,0]=255
    accept('only_top_left_corner',a,dict(top=1,bottom=0,left=1,right=0))
    a=np.zeros((256,256),np.uint8);a[0,0]=a[0,255]=a[255,0]=a[255,255]=255
    accept('four_corners',a,dict(top=2,bottom=2,left=2,right=2))
    a=np.full((256,256),255,np.uint8);accept('all_foreground',a,dict(top=256,bottom=256,left=256,right=256))
    a=np.zeros((256,256),np.uint8);a[0,0]=255;a.setflags(write=False)
    accept('read_only',a,dict(top=1,bottom=0,left=1,right=0))
    accept('noncontiguous_view',a[:,::-1],dict(top=1,bottom=0,left=0,right=1))
    bad=[('list',[[0]*256]*256),('none',None),('bad_height',np.zeros((255,256),np.uint8)),
         ('bad_width',np.zeros((256,257),np.uint8)),('extra_channel',np.zeros((256,256,1),np.uint8)),
         ('float',np.zeros((256,256),np.float32)),('uint16',np.zeros((256,256),np.uint16)),
         ('boolean',np.zeros((256,256),bool)),('int8',np.zeros((256,256),np.int8)),
         ('value1',np.full((256,256),1,np.uint8)),('value254',np.full((256,256),254,np.uint8)),
         ('masked_array',np.ma.array(np.zeros((256,256),np.uint8),mask=False))]
    rejects=[]
    for name,mask in bad:
        try:check_edge_contact(mask)
        except (TypeError,ValueError) as exc:rejects.append(dict(name=name,passed=True,error_type=type(exc).__name__,message=str(exc)))
        else:raise AssertionError('Invalid input was accepted: '+name)
    return dict(scope='synthetic tests, distinct from real model samples',valid_cases=cases,
                invalid_cases=rejects,valid_count=len(cases),invalid_count=len(rejects),passed=True)


if __name__=='__main__':print(json.dumps(run_tests(),indent=2,ensure_ascii=False))
