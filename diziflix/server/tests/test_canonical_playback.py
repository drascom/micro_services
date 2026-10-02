import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
from contextlib import closing
import json
import unittest
from unittest.mock import patch
from app import db, cache, rows
from app.library import identity, videos, tmdb
from app.routers import streams as stream_routes
from app.sources.library import LibrarySource
from app.scraper import config as scfg
import test_crawlee_integration as integration


class CanonicalTests(unittest.TestCase):
    setUp=integration.IngestTests.setUp
    ingest=integration.IngestTests.ingest

    def test_featured_membership_deduplicates_and_keeps_continue_below_hero(self):
        self.ingest([{**integration.film(), 'featured':'poster-media', 'backdrop_url':'/uploads/series/cover/mayday.jpg'},integration.film()])
        self.assertEqual(len(db.query("SELECT * FROM library_items")),1)
        self.assertEqual(len(db.query("SELECT * FROM library_lists WHERE list_id='featured_yabancidizi'")),1)
        snap=cache.Snapshot(LibrarySource())
        cid=snap.items[0]['id']
        with patch.object(rows,'mylist_ids',return_value=[]):
            boot=rows.tv_boot(snap,'p1',{cid:dict(episode_id=cid,position=20,duration=100,updated_at=1)})
        self.assertEqual(boot['heroes'][0]['title'],'Mayday')
        self.assertEqual(boot['rows'][0]['id'],'continue')

    def test_detail_discovery_enriches_existing_foreign_title(self):
        from copy import deepcopy
        from app.library import ingest as ingestion
        self.ingest([integration.film()])
        cfg=deepcopy(scfg.load_site('sinemalar'))
        cfg.data['collections']=[]
        cfg.data['detail_pages']=['/film/123/mayday']
        html='<h1>Mayday</h1><meta property="og:title" content="Mayday (Mayday) - Film, 2026"><meta property="og:image" content="https://cdn.sinemalar.com/images/movie/123/poster/a.jpg"><meta property="og:video:url" content="https://www.sinemalar.com/embed/123"><div class="text-content summary">A source synopsis</div>'
        with patch.object(scfg,'load_site',return_value=cfg), patch.object(ingestion.fetch,'page',return_value=html):
            result=self.ingest([],site='sinemalar')
        self.assertIsNone(result['error'])
        self.assertEqual(len(db.query('SELECT * FROM library_items')),1)
        row=db.query_one('SELECT * FROM library_items')
        self.assertEqual(row['id'],'mayday-2026')
        self.assertEqual(row['overview'],'A source synopsis')
        snap=cache.Snapshot(LibrarySource())
        with patch.object(rows,'get_cache',return_value=snap):
            detail=rows.detail(row['id'],'p1')
        self.assertEqual(set(detail['sources']),{'sinemalar','yabancidizi'})
        self.assertTrue(detail['availability']['has_trailer'])
        self.assertEqual(detail['overview'],'A source synopsis')

    def test_missing_movie_description_is_hydrated_only_when_requested(self):
        from app.library import ingest as ingestion
        raw = {**integration.film(), 'detail_url': '/film/123/mayday'}
        self.ingest([raw], site='sinemalar')
        html = '''<h1>Mayday</h1>
        <meta property="og:title" content="Mayday (Mayday) - Film, 2026">
        <meta property="og:image" content="https://cdn.sinemalar.com/images/movie/123/poster/a.jpg">
        <div class="text-content summary">Detay sayfasından gelen film açıklaması.</div>'''
        with patch.object(ingestion.fetch, 'page', return_value=html) as fetch_page:
            changed = ingestion.hydrate_item_metadata('mayday-2026')
        self.assertTrue(changed)
        fetch_page.assert_called_once()
        self.assertEqual(
            db.query_one("SELECT overview FROM library_items WHERE id='mayday-2026'")['overview'],
            'Detay sayfasından gelen film açıklaması.',
        )

    def test_yabancidizi_movie_detail_hydrates_facts_and_trailer(self):
        from app.library import ingest as ingestion
        self.ingest([{**integration.film(),
                      'detail_url': '/film/one-night-in-miami-izle-1'}])
        html = '''
        <div class="bg-cover-bg"><img src="/uploads/series/cover/miami.jpg"></div>
        <h1 class="page-title">Miami'de Bir Gece... <span>(2021)</span></h1>
        <img class="series-profile-thumb" src="/uploads/series/miami.jpg"
             alt="One Night in Miami...">
        <div id="series-profile-content-wrapper">
          <p id="tv-series-desc">Film açıklaması</p>
          <a href="/film/tur/dram-izle">Dram</a>
          <a href="/oyuncu/aldis-hodge">Aldis Hodge</a>
          <div class="media-meta"><table><tr>
            <td><div>Ülke</div><div>US</div></td>
            <td><div>Süre</div><div>110 dk</div></td>
            <td><div>Takipçiler</div><div>27</div></td>
            <td><div>IMDb Puanı</div><div>7.1</div></td>
            <td><div>Yapım Yılı</div><div>2021</div></td>
          </tr></table></div>
          <div class="media-trailer" data-yt="K8vf_Cmh9nY"></div>
        </div>'''
        with patch.object(ingestion.fetch, 'page', return_value=html):
            self.assertTrue(ingestion.hydrate_item_metadata('mayday-2026'))
        movie = db.query_one("SELECT * FROM library_items WHERE id='mayday-2026'")
        self.assertEqual(movie['overview'], 'Film açıklaması')
        self.assertEqual(movie['country'], 'US')
        self.assertEqual(movie['runtime'], 110)
        self.assertEqual(json.loads(movie['cast']), ['Aldis Hodge'])
        trailer = db.query_one("SELECT * FROM video_sources WHERE kind='trailer'")
        self.assertEqual(trailer['locator'], 'https://www.youtube.com/embed/K8vf_Cmh9nY')
        self.assertEqual(trailer['resolver'], 'embed')

    def test_description_prefers_sinemalar_then_original_discoverer(self):
        from app.library.ingest import merge_canonical
        self.ingest([{**integration.film(), 'synopsis':'Original description'}])
        cid='mayday-2026'
        with closing(db.connect()) as conn, conn:
            for source, text, added in [('tmdb','Other description',9999999999),('sinemalar','Sinemalar description',9999999999)]:
                conn.execute('INSERT INTO source_items(source,source_key,canonical_id,normalized,fetched_at) VALUES (?,?,?,?,?)',
                    (source,'test',cid,json.dumps({'overview':text,'_added_at':added}),1))
            merge_canonical(conn,cid)
            self.assertEqual(conn.execute('SELECT overview FROM library_items WHERE id=?',(cid,)).fetchone()[0],'Sinemalar description')
            conn.execute('UPDATE source_items SET normalized=? WHERE source=?',(json.dumps({'overview':''}),'sinemalar'))
            merge_canonical(conn,cid)
            self.assertEqual(conn.execute('SELECT overview FROM library_items WHERE id=?',(cid,)).fetchone()[0],'Original description')
            self.assertEqual(conn.execute('SELECT source FROM field_provenance WHERE canonical_id=? AND field=?',(cid,'overview')).fetchone()[0],'yabancidizi')

    def test_sinemalar_trailer_precedes_other_healthy_trailer(self):
        cid=self.add_videos()
        provider=db.query_one("SELECT * FROM video_sources WHERE kind='trailer'")
        db.execute("UPDATE video_sources SET source='aaa',status='healthy' WHERE id=?",(provider['id'],))
        db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,kind,locator,resolver,media_type,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                   ('preferred',cid,'sinemalar','test','trailer','https://example.org/trailer.mp4','direct','mp4',1))
        result=videos.streams(cid,kind='trailer')['streams']
        self.assertEqual(result[0]['source'],'sinemalar')
        self.assertEqual(result[1]['source'],'aaa')
        db.execute("UPDATE video_sources SET status='broken' WHERE id='preferred'")
        self.assertEqual(videos.streams(cid,kind='trailer')['streams'][0]['source'],'aaa')

    def test_page_resolver_collects_all_provider_choices(self):
        self.ingest([integration.film()])
        row=db.query_one("SELECT * FROM video_sources WHERE resolver='page'")
        db.execute("UPDATE video_sources SET status='suspect',last_error='old resolver error' WHERE id=?",(row['id'],))
        candidates=[
            {'url':'https://vidmolly.biz/embed-a.html','label':'VidMolly'},
            {'url':'https://ok.ru/videoembed/123','label':'OK.ru'},
        ]
        # candidates are resolved concurrently: answer by URL, not by call order
        resolved={
            'https://vidmolly.biz/embed-a.html':{'provider':'VidMolly','duration':0,'streams':[
                {'url':'https://cdn.example/video.m3u8','type':'hls','quality':'auto','label':'auto'}]},
            'https://ok.ru/videoembed/123':{'provider':'OK.ru','duration':3600,'streams':[
                {'url':'https://okcdn.example/video.mp4','type':'mp4','quality':'1080p','label':'1080p'}]},
        }
        with patch('app.scraper.fetch.page_bundle',return_value={
                'initial_html':'<html></html>','html':'<html></html>',
                'frames':[],'network_pages':[]}), \
             patch('app.scraper.site_extractors.discover',return_value=candidates), \
             patch('app.scraper.site_extractors.resolve_candidate',side_effect=lambda site,candidate,page,cookies:candidate), \
             patch('app.scraper.providers.resolve',side_effect=lambda url,**kw:resolved[url]):
            result=videos.resolve_source(row,force=True)
        self.assertEqual([s['provider'] for s in result['streams']],['VidMolly','OK.ru'])
        self.assertEqual(result['duration'],3600)
        refreshed=db.query_one('SELECT status,last_error FROM video_sources WHERE id=?',(row['id'],))
        self.assertEqual((refreshed['status'],refreshed['last_error']),('unknown',None))
        choices=videos.streams('mayday-2026',kind='video')['streams']
        self.assertEqual([s['label'] for s in choices],['VidMolly · auto','OK.ru · 1080p'])

    def test_same_imdb_adds_provider_without_duplicate_movie(self):
        self.ingest([{**integration.film(), 'imdb_id':'tt1234567'}])
        self.ingest([{**integration.film('Translated title'), 'detail_url':'film/another', 'imdb_id':'tt1234567'}])
        self.assertEqual(len(db.query('SELECT * FROM library_items')),1)
        self.assertEqual(len(db.query('SELECT * FROM source_items')),2)
        self.assertEqual(db.query_one('SELECT imdb_id FROM library_items')['imdb_id'],'tt1234567')

    def test_tmdb_movie_and_series_namespaces(self):
        self.ingest([{**integration.film(), 'tmdb_id':42}])
        self.ingest([{**integration.film(), 'detail_url':'dizi/mayday', 'tmdb_id':42}])
        self.assertEqual({r['id'] for r in db.query('SELECT id FROM library_items')},{'tmdb_42','tmdb_tv_42'})

    def test_missing_year_name_collision_is_not_merged(self):
        self.ingest([integration.film(year=None)])
        self.ingest([{**integration.film(year=None),'detail_url':'film/another'}])
        self.assertEqual(len(db.query('SELECT * FROM library_items')),2)

    def test_conflicting_identity_preserves_binding(self):
        self.ingest([{**integration.film(),'imdb_id':'tt1234567'}])
        self.ingest([{**integration.film(),'imdb_id':'tt7654321'}])
        self.assertEqual(db.query_one('SELECT imdb_id FROM library_items')['imdb_id'],'tt1234567')
        self.assertEqual(db.query_one('SELECT reason FROM identity_reviews')['reason'],'conflicting_identities')

    def test_late_identity_merge_preserves_old_ids_and_user_data(self):
        self.ingest([integration.film()])
        old='mayday-2026'
        self.ingest([{**integration.film('Different title'),'detail_url':'film/another','imdb_id':'tt1234567'}])
        target='imdb_tt1234567'
        db.execute('INSERT INTO mylist VALUES (?,?,?)',('p1',old,1))
        db.execute('INSERT INTO progress VALUES (?,?,?,?,?,?,?)',('p1',old,old,30,100,0,20))
        self.ingest([{**integration.film(),'imdb_id':'tt1234567'}])
        self.assertEqual(len(db.query('SELECT * FROM library_items')),1)
        self.assertEqual(db.query_one('SELECT item_id FROM mylist')['item_id'],target)
        self.assertEqual(db.query_one('SELECT episode_id FROM progress')['episode_id'],target)
        snap=cache.Snapshot(LibrarySource())
        self.assertEqual(snap.by_id[old]['id'],target)
        with patch.object(rows,'get_cache',return_value=snap):
            self.assertEqual(rows.detail(old,'p1')['progress']['position'],30)

    def add_videos(self):
        self.ingest([{**integration.film(),'video_sources':[
            {'url':'https://media.example/a.mp4','label':'A','kind':'movie'},
            {'url':'https://media.example/b.mp4','label':'B','kind':'movie'},
            {'url':'https://media.example/trailer.mp4','kind':'trailer'}]}])
        # These tests exercise the explicit fake sources above. Keep the
        # catalogue page source present but disabled so the suite never reaches
        # the live Yabancidizi/Obscura transport on a networked host.
        db.execute("UPDATE video_sources SET status='disabled' WHERE resolver='page'")
        return 'mayday-2026'

    def test_explicit_trailer_does_not_mix_with_full_video(self):
        cid=self.add_videos()
        self.assertEqual({s['kind'] for s in videos.streams(cid,kind='trailer')['streams']},{'trailer'})
        self.assertEqual({s['kind'] for s in videos.streams(cid,kind='video')['streams']},{'movie'})
        db.execute("DELETE FROM video_sources WHERE kind!='trailer'")
        self.assertEqual(videos.streams(cid,kind='video')['streams'],[])

    def test_streams_resume_unfinished_movie_for_selected_profile(self):
        cid=self.add_videos()
        db.execute(
            'INSERT INTO progress VALUES (?,?,?,?,?,?,?)',
            ('p1',cid,cid,37,100,0,20),
        )
        snap=cache.Snapshot(LibrarySource())
        with patch.object(cache,'get',return_value=snap):
            resumed=stream_routes.streams(cid,episode=None,profile='p1',kind='video')
            other_profile=stream_routes.streams(cid,episode=None,profile='p2',kind='video')
            trailer=stream_routes.streams(cid,episode=None,profile='p1',kind='trailer')
        self.assertEqual(resumed['resume_position'],37)
        self.assertEqual(other_profile['resume_position'],0)
        self.assertEqual(trailer['resume_position'],0)

    def test_video_failure_scoped_and_deduplicated(self):
        cid=self.add_videos()
        streams=videos.streams(cid)['streams']
        self.assertEqual(len(streams),2) # trailer is not a full-film fallback
        bad=streams[0]
        for n in range(3):
            if n: bad=next(s for s in videos.streams(cid)['streams'] if s['source_id']==bad['source_id'])
            videos.feedback(bad['attempt_token'],'failure','network','html5')
            videos.feedback(bad['attempt_token'],'failure','network','html5')
        source=db.query_one('SELECT * FROM video_sources WHERE id=?',(bad['source_id'],))
        self.assertEqual((source['status'],source['failures']),('broken',3))
        remaining=videos.streams(cid)['streams']
        self.assertEqual(len(remaining),1)
        self.assertNotEqual(remaining[0]['source_id'],bad['source_id'])
        self.assertEqual(len(db.query('SELECT * FROM library_items')),1)
        self.assertIsNone(source['resolved_payload'])

    def test_device_errors_do_not_break_source_and_success_recovers(self):
        cid=self.add_videos();stream=videos.streams(cid)['streams'][0]
        videos.feedback(stream['attempt_token'],'failure','unsupported','html5')
        source=db.query_one('SELECT * FROM video_sources WHERE id=?',(stream['source_id'],))
        self.assertEqual(source['failures'],0)
        stream=videos.streams(cid)['streams'][0]
        videos.feedback(stream['attempt_token'],'success','','html5')
        self.assertEqual(db.query_one('SELECT status FROM video_sources WHERE id=?',(stream['source_id'],))['status'],'healthy')

    def test_disabled_source_survives_ingest_and_no_trailer_fallback(self):
        cid=self.add_videos()
        db.execute("UPDATE video_sources SET status='disabled' WHERE kind='movie'")
        self.add_videos()
        self.assertEqual(videos.streams(cid)['streams'],[])
        # yabancidizi now contributes its page-backed movie provider in
        # addition to the two explicit test providers.
        self.assertEqual(len(db.query("SELECT * FROM video_sources WHERE status='disabled'")),3)

    def test_expired_attempt_rejected(self):
        cid=self.add_videos();stream=videos.streams(cid)['streams'][0]
        db.execute('UPDATE playback_attempts SET created_at=0')
        with self.assertRaises(ValueError): videos.feedback(stream['attempt_token'],'failure','network')

    def test_episode_sources_are_attached_to_the_episode(self):
        self.ingest([{**integration.film(),'detail_url':'dizi/mayday','video_sources':[
            {'url':'https://media.example/ep.mp4','kind':'episode','season':1,'episode':2}]}])
        snap=cache.Snapshot(LibrarySource());item=snap.items[0];ep=item['seasons'][0]['episodes'][0]
        self.assertEqual(videos.streams(item['id'])['streams'],[])
        self.assertEqual(len(videos.streams(item['id'],ep['id'])['streams']),1)

    def test_tmdb_matching_rejects_ambiguous_and_wrong_year(self):
        hits={'results':[{'id':1,'title':'Mayday','release_date':'2026-01-01'},{'id':2,'title':'Mayday','release_date':'2026-12-01'}]}
        with patch.object(tmdb,'enabled',return_value=True),patch.object(tmdb,'_get',return_value=hits):
            self.assertIsNone(tmdb.match('Mayday',2026))
            self.assertIsNone(tmdb.match('Mayday',2025))

    def test_id_binding_conflict_is_transactional(self):
        self.ingest([{**integration.film(),'imdb_id':'tt1234567'}])
        self.ingest([{**integration.film('Other'),'detail_url':'film/other','imdb_id':'tt9999999'}])
        with self.assertRaises(ValueError),closing(db.connect()) as conn, conn: identity.bind(conn,'imdb_tt9999999',123,'tt1234567')
        self.assertIsNone(db.query_one("SELECT * FROM external_ids WHERE provider='tmdb'"))

    def test_identity_pair_conflict_does_not_merge(self):
        self.ingest([{**integration.film(),'imdb_id':'tt1234567','tmdb_id':111}])
        self.ingest([{**integration.film('Other'),'detail_url':'film/other'}])
        with self.assertRaises(ValueError),closing(db.connect()) as conn, conn:
            identity.bind(conn,'other-2026',222,'tt1234567')
        self.assertEqual(len(db.query('SELECT id FROM library_items')),2)
        self.assertEqual(db.query_one("SELECT tmdb_id FROM library_items WHERE id='tmdb_111'")['tmdb_id'],111)
        self.ingest([{**integration.film('Other'),'detail_url':'film/other','imdb_id':'tt1234567','tmdb_id':222}])
        self.assertEqual(len(db.query('SELECT id FROM library_items')),2)

    def test_manual_shared_identity_merges_without_losing_alias(self):
        self.ingest([{**integration.film(),'imdb_id':'tt1234567'}])
        self.ingest([{**integration.film('Other'),'detail_url':'film/other'}])
        with closing(db.connect()) as conn,conn:
            identity.bind(conn,'other-2026',None,'tt1234567')
        self.assertEqual(len(db.query('SELECT id FROM library_items')),1)
        self.assertEqual(db.query_one("SELECT canonical_id FROM catalogue_aliases WHERE alias='other-2026'")['canonical_id'],'imdb_tt1234567')
