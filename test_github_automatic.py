import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from openpyxl import load_workbook
from collection_store import Store
from definition_pipeline import quality,reconcile_one_count
from graph_features import automatic_estimate,GRAPH_METHOD_VERSION
from github_workbook import build_workbook
from github_pipeline import finalize_export


def candidate():
    return {'method':GRAPH_METHOD_VERSION,'graph_sha256':'a'*64,'axis_labels':[{}, {}, {}],
      'candidate_signed_difference':100,'candidate_highest':200,'candidate_lowest':-100,
      'estimated_error':50,'rounding_unit':50,'sign_candidate':'positive','curve_clipped':False}


def record():
    return {'business_date':'2026-11-14','machine':305,'machine_code':'test','machine_name':'model',
      'bb':1,'rb':0,'total_start':100,'final_start':1,'max_payout':50,'history_count':1,
      'history_count_matches':True,'source_missing_fields':[], 'collected_at_jst':'2026-11-14T11:30:00+09:00',
      'collection_mode':'github_automatic','errors':[],**automatic_estimate(candidate())}


class AutomaticTests(unittest.TestCase):
    def test_history_priority_after_stable_single_event_recheck(self):
        history=[{'type':'BIG','time':'12:00'},{'type':'REG','time':'11:00'}]
        old={**record(),'machine':309,'bb':2,'rb':1,'history_bb_count':1,'history_rb_count':1,
             'history_count':2,'history_count_matches':False,'errors':['history_count_or_type_mismatch'],
             'snapshot_id':'prior-mismatch'}
        current=reconcile_one_count(copy.deepcopy(old),history,old)
        self.assertEqual((current['bb'],current['rb']),(1,1))
        self.assertEqual((current['display_bb'],current['display_rb']),(2,1))
        self.assertTrue(current['history_count_matches'])
        self.assertFalse(current['source_history_count_matches'])
        self.assertTrue(current['history_count_reconciled'])
        self.assertEqual(current['history_count_reconciliation']['bb_delta'],-1)
        self.assertEqual(current['errors'],[])
        self.assertEqual(len(history),2)
        self.assertEqual(quality(current)['status'],'complete')

    def test_history_priority_requires_stable_recheck_and_single_event(self):
        history=[{'type':'BIG'},{'type':'REG'}]
        base={**record(),'machine':309,'bb':2,'rb':1,'history_bb_count':1,'history_rb_count':1,
              'history_count':2,'history_count_matches':False,'errors':['history_count_or_type_mismatch']}
        self.assertFalse(reconcile_one_count(copy.deepcopy(base),history)['history_count_reconciled'])
        dual={**base,'bb':2,'rb':2}
        self.assertFalse(reconcile_one_count(copy.deepcopy(dual),history,dual)['history_count_reconciled'])
        drift={**base,'history_count':3}
        self.assertFalse(reconcile_one_count(copy.deepcopy(drift),history,base)['history_count_reconciled'])
        corrected={**base,'history_count_matches':True,'bb':1,'rb':1}
        self.assertFalse(reconcile_one_count(copy.deepcopy(corrected),history,base)['history_count_reconciled'])
        malformed={**base,'errors':['history_count_or_type_mismatch','malformed_history_row']}
        self.assertFalse(reconcile_one_count(copy.deepcopy(malformed),history,malformed)['history_count_reconciled'])

    def test_recheck_only_mismatches_once_and_keeps_original(self):
        import collect_papimo
        from definition_pipeline import recheck_history_mismatches
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=Store(root/'state.sqlite3');out=root/'out';out.mkdir()
            first,_=store.put(quality({**record(),'history_count_matches':False,
                'errors':['history_count_or_type_mismatch']}),[{'type':'BIG'}],'initial')
            good={**first,'machine':306,'history_count_matches':True,'errors':[]}
            def retry(*args,**kwargs):
                self.assertEqual(args[1],305);self.assertEqual(kwargs['recheck_of']['snapshot_id'],first['snapshot_id'])
                return store.put(quality({**record(),'collected_at_jst':'2026-11-15T00:10:00+09:00'}),
                    [{'type':'BIG'}],args[5])[0]
            with patch('definition_pipeline.one',side_effect=retry) as one,patch('definition_pipeline.time.sleep'):
                result=recheck_history_mismatches(collect_papimo,store,'2026-11-14',[first,good],{},'run',out)
            self.assertEqual(one.call_count,1);self.assertTrue(result[0]['history_count_matches'])
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM snapshots WHERE id=?',(first['snapshot_id'],)).fetchone()[0],1)
            self.assertEqual(store.history(first),[{'type':'BIG'}]);store.close()

    def test_persistent_mismatch_has_no_synthetic_history_or_retry_loop(self):
        import collect_papimo,json
        from definition_pipeline import recheck_history_mismatches
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=Store(root/'state.sqlite3');out=root/'out';out.mkdir()
            first,_=store.put(quality({**record(),'bb':2,'history_count_matches':False,
                'errors':['history_count_or_type_mismatch']}),[{'type':'BIG'}],'initial')
            with patch('definition_pipeline.one',return_value=first) as one,patch('definition_pipeline.time.sleep'):
                result=recheck_history_mismatches(collect_papimo,store,'2026-11-14',[first],{},'run',out)
            self.assertEqual(one.call_count,1);self.assertFalse(result[0]['history_count_matches'])
            self.assertEqual(len(store.history(result[0])),1)
            self.assertEqual(json.loads((out/'history_recheck_audit.json').read_text())[0]['reason'],'source_summary_history_still_disagree');store.close()

    def test_failed_recheck_retains_usable_initial_pointer(self):
        import collect_papimo
        from definition_pipeline import recheck_history_mismatches
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=Store(root/'state.sqlite3');out=root/'out';out.mkdir()
            first,_=store.put(quality({**record(),'history_count_matches':False,
                'errors':['history_count_or_type_mismatch']}),[{'type':'BIG'}],'initial')
            def retry(*args,**kwargs):
                return store.put(quality({**record(),'errors':['TimeoutError'],
                    'collected_at_jst':'2026-11-15T00:10:00+09:00'}),[],args[5])[0]
            with patch('definition_pipeline.one',side_effect=retry),patch('definition_pipeline.time.sleep'):
                result=recheck_history_mismatches(collect_papimo,store,'2026-11-14',[first],{},'run',out)
            self.assertEqual(result[0]['snapshot_id'],first['snapshot_id'])
            self.assertEqual(store.latest('2026-11-14',305)['snapshot_id'],first['snapshot_id'])
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0],2);store.close()

    def test_history_numeric_normalization_and_source_blank_evidence(self):
        import collect_papimo
        html='<div id="tab-history-index"><table class="history"><tbody><tr><td>1</td><td>12:00</td><td>１，２００</td><td><span></span></td><td>BIG</td></tr></tbody></table></div>'
        data,rows=collect_papimo.parse_page(html,305,'2026-11-14')
        self.assertEqual(collect_papimo.number(rows[0]['start']),1200)
        self.assertEqual(len(data['source_missing_fields']),1)
        self.assertEqual(data['source_missing_fields'][0]['classification'],'source_blank')
        self.assertIn('<span>',data['source_missing_fields'][0]['raw_cell_html'])
        bad,_=collect_papimo.parse_page(html.replace('<span></span>','not-a-number'),305,'2026-11-14')
        self.assertEqual(bad['source_missing_fields'],[])
        self.assertEqual(bad['history_field_audit'][0]['classification'],'unparsed_nonblank')
        self.assertIn('unparsed_history_value:payout',bad['errors'])

    def test_relative_graph_url_resolves_before_fetch(self):
        import collect_papimo
        from definition_pipeline import one
        from types import SimpleNamespace
        from urllib.parse import urljoin
        calls=[]
        def fetch(url):
            calls.append(url)
            return b'<a href="/ps/123/top">model</a>' if len(calls)==1 else b'image'
        parsed={**record(),'machine_code':'123','graph_url':'/h/slp/example.png','errors':[]}
        collector=SimpleNamespace(JST=collect_papimo.JST,HALL_ID=collect_papimo.HALL_ID,
           fetch=fetch,parse_page=lambda *args:(copy.deepcopy(parsed),[]),urljoin=urljoin,
           validate_png=lambda raw:None,save_json=collect_papimo.save_json)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=Store(root/'state.sqlite3')
            with patch.dict(os.environ,{'PAPIMO_AUTOMATIC':'1'}),patch('definition_pipeline.extract_candidates',return_value=candidate()):
                result=one(collector,305,'2026-11-14',store,{},'run',root/'output')
            self.assertEqual(calls[1],'https://papimo.jp/h/slp/example.png')
            self.assertEqual(result['reading_status'],'automatic_image_estimate')
            self.assertEqual(result['errors'],[]);store.close()

    def test_automatic_estimate_is_not_visual_confirmation(self):
        f=automatic_estimate(candidate())
        self.assertEqual(f['signed_difference'],100)
        self.assertEqual(f['reading_status'],'automatic_image_estimate')
        self.assertFalse(f['visual_confirmation'])
        self.assertEqual(quality(record())['status'],'complete')
        rejected=automatic_estimate({**candidate(),'curve_clipped':True})
        self.assertNotIn('signed_difference',rejected)
        self.assertNotIn('signed_difference',automatic_estimate({**candidate(),'method':'old'}))

    def test_excel_revisions_idempotence_and_existing_formula(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=Store(root/'papimo.sqlite3')
            h=[{'row':1,'count':'1','time':'11:00','start':'100','payout':'','type':'BIG'}]
            r,_=store.put(quality(record()),h,'one')
            a=build_workbook(store,r['business_date'],'one',{},root,root/'no-restore')
            wb=load_workbook(root/'papimo.xlsx');ws=wb.create_sheet('既存分析');ws['A1']='=1+2';wb.save(root/'papimo.xlsx');wb.close()
            b=build_workbook(store,r['business_date'],'one',{},root,root/'no-restore')
            self.assertEqual(b['appended']['台データ'],0)
            self.assertEqual(b['appended']['大当たり履歴'],0)
            store.put({**r,'bb':2},h+[dict(h[0],row=2)],'two')
            c=build_workbook(store,r['business_date'],'two',{},root,root/'no-restore')
            wb=load_workbook(root/'papimo.xlsx');self.assertEqual(wb['既存分析']['A1'].value,'=1+2')
            self.assertEqual(wb['台データ'].max_row,3)
            self.assertIsNone(wb['大当たり履歴']['J2'].value)
            wb.close();store.close()

    def test_source_blank_and_near_zero_warn_but_export_finishes(self):
        import collect_papimo
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=Store(root/'state'/'papimo.sqlite3');out=root/'output';out.mkdir()
            r=quality({**record(),'source_missing_fields':[{'row':1,'field':'payout'}],
                       'signed_difference':0,'sign_judgment':'uncertain_near_zero'})
            r,_=store.put(r,[],'run')
            base={'required_machines':1,'history_count_matches':1,'source_missing_fields':1,'work_confirmed_difference':0}
            with patch.dict(os.environ,{'GITHUB_STEP_SUMMARY':''}):
                audit=finalize_export(collect_papimo,store,r['business_date'],out,[r],'run',base)
            self.assertTrue(audit['automation_complete']);self.assertFalse(audit['data_complete'])
            self.assertEqual(audit['uncertain_signs'],1)
            self.assertEqual(audit['excel_status'],'github_workbook_verified');store.close()

    def test_real_graph_failure_does_not_look_successful(self):
        import collect_papimo
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=Store(root/'state'/'papimo.sqlite3');out=root/'output';out.mkdir()
            r=quality({**record(),'reading_status':'candidate_failed','errors':['axis_missing']})
            r,_=store.put(r,[],'run')
            base={'required_machines':1,'history_count_matches':1,'source_missing_fields':0,'work_confirmed_difference':0}
            with patch.dict(os.environ,{'GITHUB_STEP_SUMMARY':''}):
                audit=finalize_export(collect_papimo,store,r['business_date'],out,[r],'run',base)
            self.assertFalse(audit['automation_complete']);self.assertTrue(audit['technical_failures']);store.close()

    def test_site_count_discrepancy_stays_incomplete_without_fabrication(self):
        import collect_papimo
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=Store(root/'state'/'papimo.sqlite3');out=root/'output';out.mkdir()
            data=quality({**record(),'bb':2,'history_count':1,'history_bb_count':1,'history_rb_count':0,
                'history_count_matches':False,'errors':['history_count_or_type_mismatch']})
            original=[{'row':1,'count':'1','time':'11:00','start':'100','payout':'10','type':'BIG'}]
            r,_=store.put(data,original,'run')
            base={'required_machines':1,'history_count_matches':0,'source_missing_fields':0,'work_confirmed_difference':0}
            with patch.dict(os.environ,{'GITHUB_STEP_SUMMARY':''}):
                audit=finalize_export(collect_papimo,store,r['business_date'],out,[r],'run',base)
            self.assertTrue(audit['automation_complete']);self.assertFalse(audit['data_complete'])
            self.assertEqual(len(audit['source_history_mismatches']),1)
            self.assertEqual(len(store.history(r)),1)
            self.assertIn('history_count_or_type_mismatch',r['errors']);store.close()


if __name__=='__main__':unittest.main()

