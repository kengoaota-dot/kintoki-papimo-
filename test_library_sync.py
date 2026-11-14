import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from collection_store import Store
from sync_library_data import mapped,prefer,equivalent,prepare,INDEX,RAW,STATUS


def source():
    return {'business_date':'2026-11-14','machine':301,'machine_code':'123','machine_name':'test',
      'snapshot_id':'s1','signed_difference':0,'highest_difference':100,'lowest_difference':-100,'max_range':200,
      'sign_judgment':'uncertain_near_zero','reading_status':'automatic_image_estimate','estimated_error':50,
      'bb':1,'rb':1,'total_start':100,'final_start':5,'max_payout':100,'history_count':2,'history_bb_count':1,
      'history_rb_count':1,'history_count_matches':True,'source_missing_fields':[],
      'collected_at_jst':'2026-11-14T12:00:00+09:00','errors':[],'status':'partial'}


class LibrarySyncTests(unittest.TestCase):
    def test_uncertain_sign_is_reference_not_confirmed_zero(self):
        r=mapped(source());self.assertIsNone(r['当日実差枚_推定']);self.assertEqual(r['自動読取差枚_参考'],0)
        self.assertFalse(r['分析利用可']);self.assertEqual(r['当日符号付き差枚'],'')

    def test_newer_work_record_is_not_downgraded(self):
        incoming=mapped({**source(),'signed_difference':100,'sign_judgment':'positive'})
        old={**incoming,'集計取得JST':'2026-11-14T12:30:00+09:00','当日実差枚_推定':900}
        self.assertFalse(prefer(old,incoming));self.assertTrue(prefer({**old,'営業日':'20261113'},incoming))

    def test_absent_graph_never_claims_visual_confirmation(self):
        r=mapped({**source(),'reading_status':'unavailable','signed_difference':None})
        self.assertEqual(r['差枚読取精度'],'未取得');self.assertIsNone(r['当日実差枚_推定'])

    def test_replay_preserves_original_history_and_protected_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);lib=root/'library';lib.mkdir();artifact=root/'artifact';artifact.mkdir()
            store=Store(artifact/'papimo.sqlite3')
            h=[{'row':1,'count':'1','time':'12:00','start':'0','payout':'','type':'BIG'},
               {'row':2,'count':'','time':'12:00','start':'0','payout':'','type':'REG'}]
            store.put(source(),h,'source');store.close()
            data={INDEX:{'latest':{'401':{'営業日':'20261111','protected':True}},'snapshots':{'old':{'frozen':True}},
                        '日別符号付き差枚_収集索引':{},'observed_days':['20261111']},
                  RAW:{'predictions':{'frozen':'keep'},'old_data':[1,2]},STATUS:{'old_status':[1]}}
            for name,obj in data.items():(lib/name).write_text(json.dumps(obj))
            fake={n:{'headers':['営業日','台番号'],'rows':[]} for n in ['差枚時刻別追記','グラフ再読取追記','履歴時刻別追記']}
            with patch('sync_library_data.workbook_rows',return_value=fake):
                a=prepare(artifact,lib,root/'out','1')
                self.assertEqual(a['history_rows'],2)
                out_index=json.loads((root/'out'/INDEX).read_text())
                self.assertEqual(out_index['snapshots']['old'],{'frozen':True})
                self.assertTrue(out_index['latest']['401']['protected'])
                self.assertEqual(json.loads((root/'out'/RAW).read_text())['predictions'],{'frozen':'keep'})
                b=prepare(artifact,root/'out',root/'repeat','1')
            self.assertEqual(b['new_rows'],0);self.assertEqual(b['history_rows'],0)
            self.assertEqual(b['already_seen'],1)


if __name__=='__main__':unittest.main()
