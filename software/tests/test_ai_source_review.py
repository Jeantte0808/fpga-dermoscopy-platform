import json,sys,importlib,csv
from pathlib import Path
import pytest
from dermademo.ai_service import run_analysis,load_analysis,regenerate_pdf,bundle
from dermademo.ai_protocol import request_body,validate_answer,SYSTEM
from test_ai import app,record,KEY,response,ANSWER

@pytest.mark.parametrize('historical',[False,True])
def test_actual_upload_and_saved_facts_preserve_analysis_context(app,tmp_path,historical):
    source=record(tmp_path);original=(source/'record.json').read_bytes();sent=[]
    def post(_base,_key,payload):sent.append(json.loads(payload));return response()
    result=run_analysis(source,'https://dashscope.aliyuncs.com',KEY,historical=historical,post=post)
    assert result['meta']['cloud_complete'] and result['meta']['pdf_complete']
    task=Path(result['task']);facts=json.loads((task/'facts.json').read_text('utf-8'))
    uploaded=json.loads(sent[0]['messages'][1]['content'][0]['text'].split('\n',1)[1])
    assert uploaded==facts
    assert facts['analysis_context']==('historical_record' if historical else 'current_result')
    assert facts['source_kind']=='saved_FPGA_capture' and facts['result_display_mode']=='offline_replay'
    assert 'source_record' not in facts and 'record_sha256' not in facts
    assert 'historical_record' in sent[0]['messages'][0]['content']
    assert (source/'record.json').read_bytes()==original
    before=(task/'facts.json').read_bytes();load_analysis(task);regenerate_pdf(task)
    assert (task/'facts.json').read_bytes()==before and len(sent)==1
    facts['analysis_context']='current_result' if historical else 'historical_record'
    (task/'facts.json').write_text(json.dumps(facts),'utf-8')
    with pytest.raises(ValueError,match='上下文'):load_analysis(task)


def test_legacy_ai_facts_not_rewritten_and_checker_is_not_semantic_review(app,tmp_path):
    source=record(tmp_path)
    result=run_analysis(source,'https://dashscope.aliyuncs.com',KEY,historical=True,post=lambda *_:response())
    task=Path(result['task']);meta=result['meta'];meta.pop('analysis_context');meta['prompt_version']='legacy_test_fixture'
    for k in ('facts_version','context_signature','context_sha256','analysis_snapshot'):meta.pop(k,None)
    facts=json.loads((task/'facts.json').read_text('utf-8'))
    for k in ('analysis_context','source_kind','result_display_mode'):facts.pop(k)
    facts.pop('analyses',None);facts['classification']='not_enabled'
    (task/'facts.json').write_text(json.dumps(facts),'utf-8');(task/'analysis_report.json').write_text(json.dumps(meta),'utf-8')
    before=(task/'facts.json').read_bytes();assert regenerate_pdf(task)['meta']['pdf_complete']
    assert (task/'facts.json').read_bytes()==before
    answer=dict(ANSWER,teaching_summary='存在两处区域需复核。这是离线语义校验反例。')
    assert validate_answer(answer,facts)['teaching_summary']==answer['teaching_summary']  # Finite rules cannot prove factual correctness.
    answer['teaching_summary']='面积123像素'
    from dermademo.ai_content_policy import OMITTED
    assert validate_answer(answer,facts)['teaching_summary']==OMITTED


def test_member_materials_two_sample_goldens_and_quality_cases():
    root=Path('integrations/pc_algorithm_r1');software=root/'software'
    sys.path.insert(0,str(software.resolve()))
    try:alg=importlib.import_module('algorithm_reference')
    finally:sys.path.pop(0)
    def compare(actual,expected,path=""):
        if isinstance(expected,dict):
            assert set(actual)==set(expected)
            for k in expected:compare(actual[k],expected[k],path+"."+k)
        elif isinstance(expected,list):
            assert len(actual)==len(expected)
            for a,e in zip(actual,expected):compare(a,e,path)
        elif isinstance(expected,float):
            import cv2
            if path=='ISIC_0000127.symmetry.minor_axis_dice' and cv2.__version__=='4.14.0':
                # Explicitly audited incompatibility, NOT golden qualification; module is not GUI enabled.
                assert expected==pytest.approx(0.8919162125681148,abs=1e-12)
                assert actual==pytest.approx(0.8919385076865392,abs=1e-12)
            else:assert actual==pytest.approx(expected,rel=0,abs=1e-10)
        else:assert actual==expected
    from dermademo.imaging import max_feret_diameter
    scores={r['sample']:r for r in csv.DictReader((root/'reports/teaching_score_checks.csv').read_text('utf-8-sig').splitlines())}
    for sample in ('ISIC_0000091','ISIC_0000127'):
        folder=root/'samples'/sample;rgb=alg.read_rgb(folder/'input.rgb888.bin');mask=alg.read_mask(folder/'prediction_original.png')
        old_rgb,old_mask=rgb.tobytes(),mask.tobytes()
        expected=json.loads((folder/'expected_measurement.json').read_text('utf-8'))
        actual=alg.measure(rgb,mask,mask_source=expected['mask_source'])
        compare(actual,expected,sample);assert actual['max_feret_px']==max_feret_diameter(mask)
        compare(alg.quality(rgb),json.loads((folder/'expected_quality.json').read_text('utf-8')))
        scored=alg.score_masks(mask,alg.read_mask(folder/'annotation256.png'));expected_score=scores[sample]
        assert scored['dice']==pytest.approx(float(expected_score['dice']),rel=0,abs=1e-12)
        assert scored['iou']==pytest.approx(float(expected_score['iou']),rel=0,abs=1e-12)
        assert scored['fp']==int(expected_score['fp']) and scored['fn']==int(expected_score['fn'])
        assert (rgb.tobytes(),mask.tobytes())==(old_rgb,old_mask)
    for path in (root/'samples/quality').glob('*.expected.json'):
        compare(alg.quality(alg.read_rgb(path.with_name(path.name.replace('.expected.json','.rgb888.bin')))),json.loads(path.read_text('utf-8')))


def test_historical_analysis_of_original_live_record_keeps_both_sources(app,tmp_path):
    from dermademo.formal_results import replay_pair
    from dermademo.storage import save_result
    pair=replay_pair();pair[1].metadata.update(source_kind='live_FPGA_session',display_mode='live_result')
    source=save_result(tmp_path,*pair)
    result=run_analysis(source,'https://dashscope.aliyuncs.com',KEY,historical=True,post=lambda *_:response())
    task=Path(result['task']);facts=json.loads((task/'facts.json').read_text('utf-8'))
    assert facts['source_kind']=='live_FPGA_session' and facts['result_display_mode']=='live_result'
    assert facts['analysis_context']=='historical_record'
    meta=json.loads((task/'analysis_report.json').read_text('utf-8'));meta['historical']=False
    (task/'analysis_report.json').write_text(json.dumps(meta),'utf-8')
    with pytest.raises(ValueError,match='上下文'):load_analysis(task)
