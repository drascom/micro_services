import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app import catalog, config, rows


def item(i, kind='movie', state='ready', title=None, year=2025):
    return dict(id=i,type=kind,title=title or i,year=year,overview='',genres=['Dram'],
                added_at=1,availability=dict(state=state,reason=None,has_trailer=False))


class CatalogTests(unittest.TestCase):
    def test_card_reports_real_backdrop_availability(self):
        entry = item('art')
        self.assertFalse(rows.item_json(entry)['has_backdrop'])
        entry['backdrop_url'] = 'https://images.example/wide.jpg'
        self.assertTrue(rows.item_json(entry)['has_backdrop'])

    def test_trailers_and_disabled_sources_do_not_make_title_playable(self):
        self.assertEqual(catalog.availability([dict(kind='trailer',status='healthy')]),
                         dict(state='unavailable',reason='no_video_source',has_trailer=True))
        self.assertEqual(catalog.availability([dict(kind='movie',status='disabled')])['reason'],'sources_unavailable')
        self.assertEqual(catalog.availability([dict(kind='episode',status='suspect')])['state'],'check_required')
        self.assertEqual(catalog.availability([dict(kind='movie',status='healthy'),dict(kind='movie',status='broken')])['state'],'ready')

    @patch.object(rows, 'progress_map', return_value={})
    def test_filter_before_pagination_does_not_mutate_snapshot(self, _):
        source=[item('c'),item('a'),item('b'),item('series','series'),item('missing',state='unavailable')]
        snap=SimpleNamespace(items=source)
        result=catalog.list_items(snap,'p',kind='movie',genre='drama',available='ready',sort='title',offset=1,limit=1)
        self.assertEqual(result['total'],3)
        self.assertEqual(result['items'][0]['id'],'b')
        self.assertEqual([i['id'] for i in snap.items],['c','a','b','series','missing'])

    @patch.object(rows, 'progress_map', return_value={})
    def test_search_includes_metadata_only_original_title(self, _):
        entry=item('a',state='unavailable');entry['original_title']='Original Film'
        self.assertEqual(catalog.list_items(SimpleNamespace(items=[entry]),'p',q='original')['total'],1)

    @patch.object(rows, 'mylist_ids', return_value=['metadata'])
    def test_home_keeps_saved_metadata_but_does_not_feature_it(self, _):
        # my list is the profile's own list (kept even without a source); the rest of the home lists playable titles only
        entry=item('metadata',state='unavailable')
        result=rows.tv_boot(SimpleNamespace(items=[entry],by_id={'metadata':entry}),'p',{})
        self.assertIsNone(result['hero'])
        self.assertEqual([r['id'] for r in result['rows']],['mylist'])

    @patch.object(rows, 'mylist_ids', return_value=[])
    def test_home_type_rows_sort_by_addition_not_year_or_availability(self, _):
        old=item('old',year=2026);old['added_at']=10
        recent=item('recent',state='unavailable',year=1990);recent['added_at']=20
        series=item('series','series',state='unavailable')
        snap=SimpleNamespace(items=[old,series,recent],by_id={i['id']:i for i in [old,series,recent]})
        # HOME_ONLY_READY (default): titles without a playable source are not on the home
        result=rows.tv_boot(snap,'p',{})
        self.assertEqual([r['id'] for r in result['rows']],['trending_movies','movies'])
        self.assertEqual([i['id'] for i in result['rows'][1]['items']],['old'])
        # without the filter: every title, "Tüm X" newest ADDED first (not by year, not by availability)
        with patch.object(config,'HOME_ONLY_READY',False):
            result=rows.tv_boot(snap,'p',{})
            self.assertEqual([r['id'] for r in result['rows']],['trending_series','series','trending_movies','movies'])
            self.assertEqual([i['id'] for i in rows.row_pool(snap,'movies','p',{})],['recent','old'])
            self.assertEqual([i['id'] for i in result['rows'][3]['items']],['recent','old'])

    @patch.object(rows, 'mylist_ids', return_value=[])
    def test_home_places_trending_series_before_all_series(self, _):
        trend=item('trend','series');latest=item('latest','series')
        latest['seasons']=[dict(season=1,title='1. Sezon',episodes=[dict(id='latest:s1:e2',season=1,episode=2,title='İki',air_date=None,
                            availability=dict(state='ready',reason=None,has_trailer=False))])]
        snap=SimpleNamespace(items=[trend,latest],by_id={'trend':trend,'latest':latest},source='library')
        def query(_sql, params):  # role lists are read by prefix: params[0] = '<role>_*' (GLOB)
            return ([{'list_id':'trending_yabancidizi','canonical_id':'trend'}] if params[0]=='trending_*'
                    else [{'list_id':'latest_episodes_yabancidizi','canonical_id':'latest'}] if params[0]=='latest_episodes_*' else [])
        with patch.object(rows.db,'query',side_effect=query):
            result=rows.tv_boot(snap,'p',{})
        self.assertEqual([r['id'] for r in result['rows'][:2]],['trending_series','series'])
        self.assertEqual(result['rows'][0]['title'],'Haftanın Trendleri · Diziler')
        self.assertEqual([i['id'] for i in result['rows'][0]['items']],['trend','latest'])   # list first, then a scored title
        # the "new episodes" role list scores titles now; its row is no home row but still answers /api/row/new_episodes
        with patch.object(rows.db,'query',side_effect=query):
            card=rows.row_pool(snap,'new_episodes','p',{})[0]
            card=rows.item_json(card)
        self.assertEqual((card['id'],card['card_kind'],card['episode_id'],card['episode_label']),('latest','episode','latest:s1:e2','S01 B02 · İki'))

    @patch.object(rows, 'mylist_ids', return_value=[])
    def test_home_places_noteworthy_movies_between_trending_and_all_movies(self, _):
        latest=item('latest');noteworthy=item('noteworthy')
        snap=SimpleNamespace(items=[latest,noteworthy],by_id={'latest':latest,'noteworthy':noteworthy},source='library')
        def query(_sql, params):
            return ([{'list_id':'latest_movies_yabancidizi','canonical_id':'latest'}] if params[0]=='latest_movies_*'
                    else [{'list_id':'noteworthy_movies_yabancidizi','canonical_id':'noteworthy'}] if params[0]=='noteworthy_movies_*' else [])
        with patch.object(rows.db,'query',side_effect=query):
            result=rows.tv_boot(snap,'p',{})
        ids=[r['id'] for r in result['rows']]
        self.assertEqual(ids,['trending_movies','noteworthy_movies','movies'])
        self.assertEqual(result['rows'][1]['title'],'Dikkate Değer Filmler')
        self.assertEqual([i['id'] for i in result['rows'][1]['items']],['noteworthy'])
        self.assertEqual(sorted(i['id'] for i in result['rows'][2]['items']),['latest','noteworthy'])   # "Tüm Filmler": ALL movies

    @patch.object(rows, 'progress_map', return_value={})
    def test_home_row_endpoint_pages_series(self, _):
        from app.routers import rows as route
        entries=[dict(item(str(n),'series'),added_at=n) for n in range(25)]
        snap=SimpleNamespace(items=entries)
        with patch.object(route.cache,'get',return_value=snap), patch.object(rows,'get_cache',return_value=snap), patch.object(rows.catalog_view,'select',return_value=snap):
            result=route.get_row('series',profile='p',offset=20,limit=20,source='')
        self.assertEqual(result['total'],25)
        self.assertEqual([i['id'] for i in result['items']],['4','3','2','1','0'])
