import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from openpyxl import load_workbook
from collection_store import Store
from definition_pipeline import quality
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


if __name__=='__main__':unittest.main()
