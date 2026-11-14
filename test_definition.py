import copy
import tempfile
import unittest
from pathlib import Path
from collection_store import Store,history_change
from definition_pipeline import quality
from graph_features import apply_review


def row(t,p='10',n='1'):
    return {'count':n,'time':t,'start':'0','payout':p,'type':'BIG','row':1,'machine':305,'business_date':'2026-11-13'}


class DefinitionTests(unittest.TestCase):
    def test_history_connection_uses_full_series(self):
        old=[row('12:00'),row('11:00')]
        self.assertEqual(history_change(old,[row('13:00')]+old)[0],'append')
        self.assertEqual(history_change(old,[row('12:00','20'),row('11:00')])[0],'full_correction')
        self.assertEqual(history_change(old,[row('12:00')])[0],'full_correction')
        # Identical time alone never removes legitimately repeated rows.
        self.assertEqual(len(history_change([], [row('12:00'),row('12:00')])[1]),2)

    def test_state_is_idempotent_and_preserves_corrections(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'state.sqlite3')
            d={'business_date':'2026-11-13','machine':305,'machine_code':'test','bb':1,'collected_at_jst':'first'}
            a,_=store.put(d,[row('12:00')],'run1')
            b,_=store.put({**d,'collected_at_jst':'second'},[row('12:00')],'run2')
            self.assertEqual(a['snapshot_id'],b['snapshot_id'])
            self.assertTrue(b['reused'])
            store.put({**d,'bb':2},[row('13:00'),row('12:00')],'run3')
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0],2)
            self.assertEqual(len(store.history(a)),1)
            store.put(d,[row('12:00')],'run4')
            self.assertEqual(store.latest('2026-11-13',305)['bb'],1)
            store.close()

    def test_history_does_not_cross_machine_or_business_date(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'state.sqlite3')
            base={'business_date':'2026-11-13','machine':305,'machine_code':'test','collected_at_jst':'first'}
            a,_=store.put(base,[row('12:00')],'run1')
            other=row('12:00');other['machine']=306
            b,_=store.put({**base,'machine':306},[other],'run1')
            self.assertNotEqual(a['history_id'],b['history_id'])
            self.assertEqual(store.history(b)[0]['machine'],306)
            store.close()

    def test_pending_graph_and_source_blank_block_completion(self):
        d={'bb':1,'rb':0,'total_start':1,'final_start':1,'max_payout':1,'machine_name':'test',
           'machine_code':'test','history_count_matches':True,'source_missing_fields':[],
           'reading_status':'candidate_unreviewed','errors':[]}
        self.assertEqual(quality(copy.deepcopy(d))['status'],'partial')
        d['reading_status']='work_visual_confirmed'
        self.assertEqual(quality(copy.deepcopy(d))['status'],'complete')
        d['source_missing_fields']=[{'row':1,'time':'12:00','field':'payout'}]
        self.assertEqual(quality(d)['status'],'partial')

    def test_review_rejects_other_image_and_conflicting_sign(self):
        f={'graph_sha256':'image1'}
        r={'machine':305,'business_date':'2026-11-13','machine_code':'test','graph_sha256':'image2',
            'signed_difference':100,'highest_difference':200,'lowest_difference':-100,'estimated_error':50,
            'rounding_unit':50,'sign_judgment':'positive','zero_line_confirmed':True,'axis_confirmed':True,
            'right_endpoint_confirmed':True,'extrema_confirmed':True,'reviewed_at_jst':'2026-11-14T01:00:00+09:00','reviewer':'Work'}
        with self.assertRaisesRegex(ValueError,'identity_mismatch'):apply_review(f,r,305,'2026-11-13','test')
        r['graph_sha256']='image1'
        self.assertEqual(apply_review(f,r,305,'2026-11-13','test')['reading_status'],'work_visual_confirmed')
        r['sign_judgment']='negative'
        with self.assertRaisesRegex(ValueError,'sign_conflict'):apply_review(f,r,305,'2026-11-13','test')


if __name__=='__main__':unittest.main()
