import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from app import catalog_view, rows
from app.errors import ApiError
import test_crawlee_integration as integration
film = integration.film
from app.scraper import state

class SourceTests(unittest.TestCase):
    def setUp(self):
        self.a = dict(id='a', sources=['sinemalar'], title='Film', type='movie', year=2026, overview='', popularity=2)
        self.b = dict(id='b', sources=['yabancidizi'], title='Film B', type='movie', year=2026, overview='', popularity=1)
        self.snap = SimpleNamespace(items=[self.a,self.b], by_id={'a':self.a,'b':self.b}, by_genre={'x':[self.a,self.b]}, newest=[self.a], top10=[self.a,self.b], upcoming={'id':'yakinda','items':[self.a]},search_index=[('film',self.a),('film b',self.b)])
    def test_filter_does_not_mutate_shared_snapshot(self):
        result=catalog_view.select(self.snap,'yabancidizi')
        self.assertEqual(result.items,[self.b]); self.assertEqual(result.newest,[self.b])
        self.assertEqual(result.by_genre['x'],[self.b]); self.assertEqual(result.upcoming['items'],[])
        self.assertEqual(len(self.snap.items),2); self.assertEqual(self.snap.newest,[self.a])
        self.assertIs(catalog_view.select(self.snap),self.snap)
    def test_unknown_source_rejected(self):
        with self.assertRaises(ApiError): catalog_view.select(self.snap,'missing')
    def test_search_is_source_scoped(self):
        with patch.object(rows,'get_cache',return_value=self.snap):
            result=rows.search('film','',20,'yabancidizi')
        self.assertEqual([i['id'] for i in result['items']],['b'])

class ReportTests(unittest.TestCase):
    setUp = integration.IngestTests.setUp
    ingest = integration.IngestTests.ingest
    def test_counts_and_persisted_history(self):
        first=self.ingest([film(),film()]);second=self.ingest([film()]);third=self.ingest([film(title='Changed')])
        self.assertEqual((first['added'],first['duplicates']),(1,1))
        self.assertEqual(second['unchanged'],1);self.assertEqual(third['updated'],1)
        reports=state.get_site_state('yabancidizi')['reports']
        self.assertEqual(len(reports),3);self.assertEqual(reports[-1]['status'],'success')
        self.assertEqual(reports[0]['collections'][0]['count'],2)
        self.assertGreaterEqual(reports[0]['duration_seconds'],0)
        self.assertNotEqual(reports[0]['run_id'],reports[1]['run_id'])
    def test_failed_run_recorded(self):
        result=self.ingest([film()],drift=True)
        self.assertEqual(result['status'],'error')
        self.assertEqual(state.get_site_state('yabancidizi')['reports'][-1]['status'],'error')
    def test_partial_collection_report(self):
        from app.library import ingest as module
        from app.scraper.runner import RunResult
        from app.scraper import config as scfg
        cfg=scfg.load_site('yabancidizi')
        original=cfg.data['collections']
        cfg.data['collections']=original+[{'id':'extra','title':'Extra','path':'/extra','role':'genre','genre':'extra'}]
        def fetch_collection(_cfg, collection, _limit):
            if collection['id'] == 'extra': raise RuntimeError('HTTP 503')
            return []
        with patch.object(module.scfg,'load_site',return_value=cfg), patch.object(module,'run_site',return_value=RunResult('yabancidizi',items=[film()],drift={'drift':False})), patch.object(module,'_fetch_collection',side_effect=fetch_collection), patch.object(module.tmdb,'enabled',return_value=False):
            result=module.ingest_source('yabancidizi')
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['ingested'],1)
        self.assertEqual(result['collections'][-1]['error'],'HTTP 503')
