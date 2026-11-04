import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import corpus
import douk
import integrations
import intelligence
import store


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old_db=store.DB_PATH
        if store._CONN:store._CONN.close()
        store._CONN=None
        store.DB_PATH=Path(self.tmp.name)/'monitor.db'
        douk.init_tables()
        corpus.init()

    def tearDown(self):
        if store._CONN:store._CONN.close()
        store._CONN=None
        store.DB_PATH=self.old_db
        self.tmp.cleanup()

    def test_platform_identity_never_collides(self):
        for platform in ('bili','ks'):
            row=corpus.normalize({'video_id':'123','title':platform,'create_time':1790000000},platform)
            corpus.save_content(row,{},'mcpro_python','job')
        self.assertEqual(corpus.contents()['total'],2)
        self.assertEqual({r['platform'] for r in corpus.contents()['items']},{'bili','ks'})

    def test_douyin_missing_views_remain_unknown(self):
        row=corpus.normalize({'aweme_id':'7703151458754472787','liked_count':'100','create_time':1790000000000},'dy')
        self.assertEqual(row['digg_count'],100)
        self.assertIsNone(row['play_count'])
        self.assertTrue(row['published_at'].startswith('2026-'))

    def test_job_import_uses_exact_acquisition_not_timestamp(self):
        path=Path(self.tmp.name)/'native.db'
        c=sqlite3.connect(path)
        c.executescript('CREATE TABLE douyin_aweme(aweme_id TEXT);CREATE TABLE douyin_aweme_comment(comment_id TEXT);CREATE TABLE vox_crawl_observation(operation_id TEXT,platform TEXT,kind TEXT,payload TEXT);')
        for operation,aid in [('one','7703151458754472787'),('two','7703151458754472788')]:
            c.execute('INSERT INTO vox_crawl_observation VALUES(?,?,?,?)',(operation,'dy','topic',json.dumps({'aweme_id':aid,'title':operation,'liked_count':10})))
        c.commit();c.close()
        result=corpus.import_native(path,'dy',job_id='one')
        self.assertEqual([r['title'] for r in result['data']],['one'])
        self.assertEqual(corpus.contents()['total'],1)

    def test_unknown_anonymized_author_is_not_assigned_to_own_account(self):
        row=corpus.normalize({'aweme_id':'7703151458754472787','nickname':'创作者***','creator_hash':'private-hash'},'dy')
        corpus.save_content(row,{},'mcpro_python','job')
        self.assertEqual(intelligence.list_tracked_accounts(),[])
        self.assertEqual(corpus.contents()['total'],1)

    def test_asset_paths_reject_traversal_and_credentials(self):
        root=Path(self.tmp.name)/'assets';root.mkdir()
        (root/'sample.mp4').write_bytes(b'example')
        (root/'secret.json').write_text('{}')
        with patch.object(integrations,'asset_roots',return_value={'test':(root,'Test')}):
            self.assertEqual(integrations.asset_path('test','sample.mp4'),root/'sample.mp4')
            for name in ('../monitor.db','secret.json','missing.mp4'):
                with self.assertRaises(ValueError):integrations.asset_path('test',name)
            self.assertEqual(len(integrations.assets()['items']),1)

    def test_paid_job_cannot_be_retried_without_new_preview(self):
        with patch.object(douk,'read',return_value={'endpoint':'tikhub','allow_charges':True}):
            with self.assertRaisesRegex(ValueError,'重新预览'):douk.retry('1111111111111111')


if __name__=='__main__':unittest.main()
