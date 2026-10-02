import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import contextlib
import io
import os
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from app import cache, config, db, images, settings
from app.library import enrich, ingest as ingest_module, tmdb
from app.scraper.runner import RunResult
from app.sources.library import LibrarySource
import test_crawlee_integration as integration

_settings_patch = None


def setUpModule():
    """Never read the dev machine's saved admin settings (data/ops_settings.json): env defaults only."""
    import tempfile
    global _settings_patch, _settings_tmp
    _settings_tmp = tempfile.TemporaryDirectory()
    _settings_patch = patch.object(settings, 'SETTINGS_PATH', os.path.join(_settings_tmp.name, 'ops_settings.json'))
    _settings_patch.start()


def tearDownModule():
    _settings_patch.stop()
    _settings_tmp.cleanup()


DETAIL = {
    'id': 42, 'imdb_id': 'tt1234567', 'overview': 'TMDB overview', 'original_title': 'Mayday',
    'genres': [{'name': 'Aksiyon'}], 'vote_average': 7.5, 'vote_count': 100, 'runtime': 101,
    'poster_path': '/default_p.jpg', 'backdrop_path': '/default_b.jpg',
    'images': {
        'posters': [{'file_path': '/en_hi.jpg', 'iso_639_1': 'en', 'vote_average': 9, 'width': 2000},
                    {'file_path': '/tr_lo.jpg', 'iso_639_1': 'tr', 'vote_average': 1, 'width': 500},
                    {'file_path': '/tr_hi.jpg', 'iso_639_1': 'tr', 'vote_average': 5, 'width': 1000}],
        'backdrops': [{'file_path': '/en_b.jpg', 'iso_639_1': 'en', 'vote_average': 9, 'width': 3000},
                      {'file_path': '/null_b.jpg', 'iso_639_1': None, 'vote_average': 2, 'width': 1920}]},
}
HIT = {'id': 42, 'title': 'Mayday', 'original_title': 'Mayday', 'release_date': '2026-03-01'}


def fake_get(hits=None, calls=None):
    def _get(path, **params):
        if calls is not None:
            calls.append(path)
        if path.startswith('/search/'):
            return {'results': hits if hits is not None else [HIT]}
        if path.startswith('/movie/') or path.startswith('/tv/'):
            return DETAIL
        raise AssertionError(path)
    return _get


class ClientTests(unittest.TestCase):
    def test_key_kind_detection(self):
        self.assertEqual(tmdb.key_kind('eyJhbGciOiJIUzI1NiJ9.abc.def'), 'bearer')
        self.assertEqual(tmdb.key_kind('a' * 32), 'api_key')
        self.assertEqual(tmdb.key_kind('x' * 60), 'bearer')

    def test_enabled_reads_any_env_name_and_empty_disables(self):
        with patch.dict(os.environ, {'TMDB_ACCESS_KEY': '', 'TMDB_TOKEN': '', 'TMDB_API_KEY': ''}):
            self.assertFalse(tmdb.enabled())
        with patch.dict(os.environ, {'TMDB_ACCESS_KEY': '', 'TMDB_TOKEN': '', 'TMDB_API_KEY': 'k' * 32}):
            self.assertEqual(tmdb.credentials()[0], 'api_key')
        with patch.dict(os.environ, {'TMDB_ACCESS_KEY': 'eyJabc', 'TMDB_TOKEN': '', 'TMDB_API_KEY': ''}):
            self.assertEqual(tmdb.credentials()[0], 'bearer')

    def test_auth_transport_bearer_vs_query(self):
        seen = {}

        class Res:
            status_code = 200
            headers = {}
            def json(self): return {}

        def fake(url, params, headers, timeout):
            seen.update(params=params, headers=headers)
            return Res()
        with patch.dict(os.environ, {'TMDB_ACCESS_KEY': 'eyJabc'}), patch.object(tmdb.httpx, 'get', fake):
            tmdb._get('/x')
        self.assertEqual(seen['headers'], {'Authorization': 'Bearer eyJabc'})
        self.assertNotIn('api_key', seen['params'])
        with patch.dict(os.environ, {'TMDB_ACCESS_KEY': 'k' * 32}), patch.object(tmdb.httpx, 'get', fake):
            tmdb._get('/x')
        self.assertEqual(seen['params']['api_key'], 'k' * 32)
        self.assertEqual(seen['headers'], {})

    def test_429_retry_after_is_honoured(self):
        seq = []

        class Res:
            def __init__(self, code): self.status_code = code; self.headers = {'Retry-After': '2'}
            def json(self): return {'ok': 1}
        responses = [Res(429), Res(200)]
        with patch.dict(os.environ, {'TMDB_ACCESS_KEY': 'k' * 32}), \
                patch.object(tmdb.httpx, 'get', lambda *a, **k: responses.pop(0)), \
                patch.object(tmdb.time, 'sleep', lambda s: seq.append(s)):
            self.assertEqual(tmdb._get('/x'), {'ok': 1})
        self.assertEqual(seq, [2.0])

    def test_score_and_match_thresholds(self):
        self.assertGreaterEqual(tmdb.score('Mayday', 2026, None, HIT), tmdb.HIGH_CONFIDENCE)
        self.assertGreaterEqual(tmdb.score('Mayday', 2025, None, HIT), tmdb.HIGH_CONFIDENCE)  # +-1 year
        self.assertLess(tmdb.score('Mayday', 2020, None, HIT), tmdb.HIGH_CONFIDENCE)
        self.assertLess(tmdb.score('Mayday', None, None, HIT), tmdb.HIGH_CONFIDENCE)  # year unknown: 0.7 at most
        # original title helps when the localised one differs
        self.assertGreaterEqual(tmdb.score('Yerel Ad', 2026, 'Mayday', HIT), tmdb.HIGH_CONFIDENCE)
        with patch.object(tmdb, 'enabled', return_value=True), patch.object(tmdb, '_get', fake_get()):
            res = tmdb.find('Mayday', 2026)
            self.assertEqual(res['status'], 'matched')
            self.assertEqual(res['data']['imdb_id'], 'tt1234567')
            # No source year: a lone, exact title with a clear lead is now auto-matched
            # (was "review"); a merely similar one still is not (see MatchingTests).
            self.assertEqual(tmdb.find('Mayday', None)['status'], 'matched')
            self.assertEqual(tmdb.find('Tamamen Farkli', 2026)['status'], 'unmatched')

    def test_ambiguous_candidates_go_to_review(self):
        hits = [HIT, {**HIT, 'id': 43}]
        with patch.object(tmdb, 'enabled', return_value=True), patch.object(tmdb, '_get', fake_get(hits)):
            self.assertEqual(tmdb.find('Mayday', 2026)['status'], 'review')

    def test_artwork_selection_prefers_tr_then_votes_and_uses_configured_sizes(self):
        with patch.object(tmdb, 'enabled', return_value=True), patch.object(tmdb, '_get', fake_get()):
            data = tmdb.find('Mayday', 2026)['data']
        self.assertEqual(data['poster_url'], 'https://image.tmdb.org/t/p/w500/tr_hi.jpg')
        self.assertEqual(data['backdrop_url'], 'https://image.tmdb.org/t/p/w1280/null_b.jpg')
        with patch.object(config, 'TMDB_POSTER_SIZE', 'w780'), patch.object(tmdb, '_get', fake_get()), \
                patch.object(tmdb, 'enabled', return_value=True):
            self.assertTrue(tmdb.find('Mayday', 2026)['data']['poster_url'].startswith('https://image.tmdb.org/t/p/w780/'))

    def test_network_error_returns_error_status(self):
        def boom(path, **p): raise tmdb.TmdbError('timeout')
        with patch.object(tmdb, 'enabled', return_value=True), patch.object(tmdb, '_get', boom):
            self.assertEqual(tmdb.find('Mayday', 2026)['status'], 'error')
            self.assertIsNone(tmdb.match('Mayday', 2026))


def smart_get(hits=None, alt=None, calls=None, by_query=None):
    """Mock TMDB: search hits (optionally per query), per-id alt/translation payloads."""
    def _get(path, **params):
        if calls is not None:
            calls.append((path, params.get('append_to_response'), params.get('query')))
        if path.startswith('/search/'):
            return {'results': by_query(params['query']) if by_query else (hits or [])}
        if path.startswith('/movie/') or path.startswith('/tv/'):
            if 'alternative_titles' in (params.get('append_to_response') or ''):
                return (alt or {}).get(int(path.rsplit('/', 1)[-1]), {})
            return DETAIL
        raise AssertionError(path)
    return _get


def hit(title, year, id=7, original=None):
    return {'id': id, 'title': title, 'original_title': original or title, 'release_date': '%s-01-01' % year if year else ''}


def alt_calls(calls):
    return [c for c in calls if c[1] and 'alternative_titles' in c[1]]


class MatchingTests(unittest.TestCase):
    def find(self, title, year, hits=None, original=None, get=None, calls=None, alt=None):
        get = get or smart_get(hits, alt=alt, calls=calls)
        with patch.object(tmdb, 'enabled', return_value=True), patch.object(tmdb, '_get', get):
            return tmdb.find(title, year, original)

    def test_year_bonus_cannot_rescue_a_weak_title(self):
        # "Gunah Kizlari" 1964 vs "Gunah Kadinlari" 1964: score used to be 0.89 -> auto.
        wrong = hit('Günah Kadınları', 1964)
        cand = {'title': 'Günah Kadınları', 'release_date': '1964-07-01'}
        self.assertGreaterEqual(tmdb.score('Günah Kızları', 1964, None, cand), tmdb.HIGH_CONFIDENCE)
        res = self.find('Günah Kızları', 1964, [wrong])
        self.assertEqual(res['status'], 'review')
        self.assertIn('sim', res['reason'])
        self.assertEqual(res['candidates'][0]['id'], 7)

    def test_alt_title_rescues_a_match_and_is_flagged(self):
        calls = []
        alt = {7: {'alternative_titles': {'titles': [{'iso_3166_1': 'TR', 'title': 'Günah Kızları'}]},
                   'translations': {'translations': []}}}
        res = self.find('Günah Kızları', 1964, [hit('Günah Kadınları', 1964)], calls=calls, alt=alt)
        self.assertEqual(res['status'], 'matched')
        self.assertEqual(res['candidate']['via'], 'alt-title')
        self.assertIn('alt title', res['reason'])
        self.assertEqual(len(alt_calls(calls)), 1)

    def test_translation_titles_and_tv_key_are_used(self):
        alt = {7: {'alternative_titles': {'results': []},
                   'translations': {'translations': [{'iso_639_1': 'en', 'data': {'title': '', 'name': 'Bokura ga Ita: Part 2'}}]}}}
        res = self.find('Bokura ga Ita: Part 2', 2012, [hit('僕等がいた 後篇', 2012)], alt=alt)
        self.assertEqual(res['status'], 'matched')

    def test_alt_lookups_are_capped_per_title_and_can_be_disabled(self):
        hits = [hit('Zzz Film %d' % i, 2020, id=100 + i) for i in range(6)]
        calls = []
        res = self.find('Mayday', 2020, hits, calls=calls)
        self.assertEqual(len(alt_calls(calls)), 3)
        self.assertNotEqual(res['status'], 'matched')
        calls = []
        with patch.object(config, 'TMDB_ALT_TITLE_LOOKUPS', 1):
            self.find('Mayday', 2020, hits, calls=calls)
        self.assertEqual(len(alt_calls(calls)), 1)
        calls = []
        with patch.object(config, 'TMDB_ALT_TITLE_LOOKUPS', 0):
            self.find('Mayday', 2020, hits, calls=calls)
        self.assertEqual(alt_calls(calls), [])

    def test_alt_lookup_skips_clearly_different_years_and_confident_hits(self):
        calls = []
        self.find('Mayday', 2026, [hit('Mayday', 2026)], calls=calls)  # already auto-matchable
        self.assertEqual(alt_calls(calls), [])
        calls = []
        self.find('Mayday Parade', 2026, [hit('Something else', 1980)], calls=calls)  # year far off
        self.assertEqual(alt_calls(calls), [])

    def test_alt_lookup_failure_degrades_to_review_not_error(self):
        def get(path, **params):
            if path.startswith('/search/'):
                return {'results': [hit('Mayday Parade', 2026)]}
            raise tmdb.TmdbError('HTTP 500')
        res = self.find('Mayday', 2026, get=get)
        self.assertEqual(res['status'], 'review')

    def test_yearless_source_needs_exact_title_and_clear_lead(self):
        winx = hit('Winx Club: Kayıp Krallığın Sırrı', 2007, original='Winx Club - Il segreto del regno perduto')
        res = self.find('Winx Club : Kayıp Krallığın Sırrı', None, [winx])
        self.assertEqual(res['status'], 'matched')
        self.assertIn('year unknown', res['reason'])
        # same exact title on two different films -> ambiguous
        self.assertEqual(self.find('The Dance', None, [hit('The Dance', 2007, 1), hit('The Dance', 2017, 2)])['status'], 'review')
        # similar but not (near-)exact -> review
        self.assertEqual(self.find('Mayday', None, [hit('Mayday 2', 2026)])['status'], 'review')
        # yearless source, candidate year irrelevant; but a source WITH a year and a
        # candidate without one is never auto-matched
        self.assertEqual(self.find('Mezuniyet', 2009, [hit('Mezuniyet', None)])['status'], 'review')

    def test_year_outside_plus_minus_one_is_never_auto(self):
        res = self.find('Yasak Sokaklar', 1993, [hit('Yasak Sokaklar', 1965)])
        self.assertEqual(res['status'], 'review')
        self.assertIn('year mismatch', res['reason'])
        self.assertLess(res['score'], tmdb.HIGH_CONFIDENCE)
        # +-1 is fine, and a candidate in the right year outranks a wrong-year exact title
        self.assertEqual(self.find('Suçlu Gençlik', 1985, [hit('Suçlu Gençlik', 1986)])['status'], 'matched')
        res = self.find('Lambada', 1989, [hit('Lambada', 1970, 1), hit('Lambada', 1989, 2)])
        self.assertEqual((res['status'], res['candidate']['id']), ('matched', 2))
        # weak title and wrong year -> unmatched instead of a noisy review entry
        self.assertEqual(self.find('Mayday', 2000, [hit('Mayday Parade', 2010)])['status'], 'unmatched')

    def test_roman_numerals_and_common_prefixes(self):
        cand = {'title': 'WWE Wrestlemania XXVI', 'original_title': 'WWE Wrestlemania XXVI', 'release_date': '2010-03-28'}
        self.assertGreaterEqual(tmdb.score('Wrestlemania 26', 2010, 'Wrestlemania 26', cand), tmdb.HIGH_CONFIDENCE)
        res = self.find('Wrestlemania 26', 2010, [hit('WWE Wrestlemania XXVI', 2010, id=54728)])
        self.assertEqual((res['status'], res['candidate']['id']), ('matched', 54728))
        self.assertEqual(tmdb._norm('Rocky V'), tmdb._norm('Rocky 5'))
        self.assertEqual(tmdb._norm('The Dance'), tmdb._norm('Dance'))
        self.assertEqual(tmdb._norm('Rocky IV Part II'), 'rocky4part2')
        # leading single letters and ordinary words are not numerals
        self.assertEqual(tmdb._norm('I Am Legend'), 'iamlegend')
        self.assertEqual(tmdb._norm('X-Men'), 'xmen')
        self.assertEqual(tmdb._norm('Mix Did Vivid Civil'), 'mixdidvividcivil')
        # a different number is still different
        self.assertLess(tmdb._title_sim(tmdb._prep('Wrestlemania 26'), tmdb._prep('WWE Wrestlemania XXV')), 0.97)

    def test_subtitle_scoring(self):
        sim = lambda a, b: tmdb._title_sim(tmdb._prep(a), tmdb._prep(b))
        self.assertEqual(sim('Winx Club : Kayıp Krallığın Sırrı', 'Winx Club - Kayıp Krallığın Sırrı'), 1.0)
        # base title vs. its "edition": shared main title, capped below the auto floor
        self.assertAlmostEqual(sim('Cory in the House', 'Cory in the House: All-Star Edition'), 0.85)
        self.assertAlmostEqual(sim('Takım Böyle Tutulur: Vazgeçersen Kaybedersin', 'Takım Böyle Tutulur'), 0.85)
        # same main title, unrelated subtitles
        self.assertLess(sim('Spider-Man: Homecoming', 'Spider-Man: Far From Home'), 0.8)
        # near-identical subtitle still scores high but never as an exact match
        self.assertLess(sim('Captain Tsubasa: Sekai Daikessen! Jr World Cup', 'Captain Tsubasa: Sekai Daikessen Jr World Cups'), 1.0)
        res = self.find('Cory in the House', 2007, [hit('Cory in the House: All-Star Edition', 2007)])
        self.assertEqual(res['status'], 'review')

    def test_dotless_i_and_escaped_quotes(self):
        self.assertEqual(tmdb._fold('Shake ıt Up!'), tmdb._fold('Shake It Up!'))
        self.assertEqual(tmdb._fold('İstanbul Kızları'), tmdb._fold('istanbul kizlari'))
        self.assertEqual(tmdb._clean_query('Captain Tsubasa: \\"world Youth\\"'), 'Captain Tsubasa: world Youth')
        self.assertEqual(tmdb._clean_query('Winx Club : Kayıp'), 'Winx Club: Kayıp')
        self.assertEqual(tmdb._prep('Captain Tsubasa: \\"World Youth\\"').base, tmdb._prep('Captain Tsubasa: World Youth').base)
        queries = []

        def by_query(q):
            queries.append(q)
            return [] if 'ı' in q or q == 'Shake ıt Up!' else [hit('Shake It Up!', 2011)]
        # base queries fail (dotless i), the fallback search with a plain i finds it
        res = self.find('Shake ıt Up!', 2011, get=smart_get(by_query=by_query))
        self.assertEqual(res['status'], 'matched')
        self.assertIn('Shake it Up!', queries)
        self.assertEqual(len(queries), 3)

    def test_apostrophe_year_fallback_query(self):
        self.assertEqual(tmdb._fallback_plan("Wcw Road Wild '99", 1999, None), [('Wcw Road Wild 1999', 'en-US')])
        self.assertEqual(tmdb._fallback_plan("Show '05", None, "Show '05"), [('Show 2005', 'en-US')])
        self.assertEqual(tmdb._fallback_plan('Plain title', 2000, None), [])
        res = self.find("Wcw Road Wild '99", 1999,
                        get=smart_get(by_query=lambda q: [hit('WCW Road Wild 1999', 1999)] if '1999' in q else []))
        self.assertEqual(res['status'], 'matched')

    def test_search_stops_early_and_fallback_is_bounded(self):
        calls = []
        self.find('Mayday', 2026, [hit('Mayday', 2026)], calls=calls)
        self.assertEqual(len([c for c in calls if c[0].startswith('/search/')]), 1)  # exact hit: no 2nd query
        calls = []
        self.find("Ihtiyar ıt '99", 1999, [], calls=calls)
        self.assertLessEqual(len([c for c in calls if c[0].startswith('/search/')]), 4)

    def test_thresholds_are_tunable_at_runtime(self):
        weak = [hit('Mayday Parade', 2026)]  # sim 0.67 -> score 0.77
        self.assertEqual(self.find('Mayday', 2026, weak)['status'], 'review')
        with patch.object(config, 'TMDB_AUTO_SCORE', 0.7), patch.object(config, 'TMDB_MIN_SIM', 0.6):
            self.assertEqual(self.find('Mayday', 2026, weak)['status'], 'matched')
        near = [hit('Mayday 2', 2026)]  # sim 0.92
        self.assertEqual(self.find('Mayday', None, near)['status'], 'review')
        with patch.object(config, 'TMDB_MIN_SIM_NOYEAR', 0.9):
            self.assertEqual(self.find('Mayday', None, near)['status'], 'matched')
        with patch.object(config, 'TMDB_MARGIN', 0.5):
            self.assertEqual(self.find('Mayday', 2026, [hit('Mayday', 2026, 1), hit('Mayday', 2025, 2)])['status'], 'review')
        with patch.object(config, 'TMDB_REVIEW_SCORE', 0.9):
            self.assertEqual(self.find('Mayday', 2026, weak)['status'], 'unmatched')

    def test_thresholds_come_from_environment(self):
        code = ('from app import config as c; print(c.TMDB_AUTO_SCORE, c.TMDB_REVIEW_SCORE, c.TMDB_MARGIN, '
                'c.TMDB_MIN_SIM, c.TMDB_MIN_SIM_NOYEAR, c.TMDB_ALT_TITLE_LOOKUPS)')
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')

        def run(env):
            out = subprocess.run([sys.executable, '-c', code], cwd=root, capture_output=True, text=True, timeout=60,
                                 env={**os.environ, **env})
            self.assertEqual(out.returncode, 0, out.stderr)
            return out.stdout.split()
        self.assertEqual(run({'TMDB_AUTO_SCORE': '0.95', 'TMDB_REVIEW_SCORE': '0.4', 'TMDB_MARGIN': '0.2',
                              'TMDB_MIN_SIM': '0.99', 'TMDB_MIN_SIM_NOYEAR': '0.5', 'TMDB_ALT_TITLE_LOOKUPS': '1'}),
                         ['0.95', '0.4', '0.2', '0.99', '0.5', '1'])
        self.assertEqual(run({'TMDB_AUTO_SCORE': 'abc', 'TMDB_ALT_TITLE_LOOKUPS': '0', 'TMDB_MIN_SIM': '',
                              'TMDB_REVIEW_SCORE': '', 'TMDB_MARGIN': '', 'TMDB_MIN_SIM_NOYEAR': ''}),
                         ['0.85', '0.55', '0.08', '0.9', '0.97', '0'])


class PolicyTests(unittest.TestCase):
    def test_series_disabled_by_default_and_flag_enables(self):
        with patch.object(tmdb, 'enabled', return_value=True):
            self.assertTrue(enrich.should_run('movie', {}))
            self.assertFalse(enrich.should_run('series', {}))
            with patch.object(config, 'TMDB_ENRICH_TYPES', {'movie', 'series'}):
                self.assertTrue(enrich.should_run('series', {}))
        with patch.object(tmdb, 'enabled', return_value=False):
            self.assertFalse(enrich.should_run('movie', {}))

    def test_retry_window_and_matched_never_requeried(self):
        now = time.time()
        with patch.object(tmdb, 'enabled', return_value=True):
            self.assertFalse(enrich.should_run('movie', {'status': 'matched', 'checked_at': 1}))
            self.assertTrue(enrich.should_run('movie', {'status': 'matched', 'checked_at': 1}, force=True))
            self.assertFalse(enrich.should_run('movie', {'status': 'unmatched', 'checked_at': now - 3600}))
            self.assertTrue(enrich.should_run('movie', {'status': 'unmatched', 'checked_at': now - 8 * 86400}))

    def test_fill_norm_never_overwrites_source_fields_or_artwork(self):
        norm = {'overview': 'kaynak', 'poster_url': 'https://cdn.sinemalar.com/p.jpg', 'rating': None}
        enrich.fill_norm(norm, {'tmdb_id': 42, 'imdb_id': 'tt1234567', 'overview': 'tmdb', 'rating': 7.5,
                                'poster_url': 'https://image.tmdb.org/t/p/w500/x.jpg'})
        self.assertEqual(norm['overview'], 'kaynak')
        self.assertEqual(norm['rating'], 7.5)
        self.assertEqual(norm['poster_url'], 'https://cdn.sinemalar.com/p.jpg')
        self.assertEqual(norm['tmdb_id'], 42)

    def test_allow_list_contains_tmdb_images(self):
        self.assertTrue(images.remote_host_allowed('https://image.tmdb.org/t/p/w500/a.jpg'))
        self.assertFalse(images.remote_host_allowed('https://evil.example/a.jpg'))
        with patch.object(config, 'REMOTE_IMG_HOSTS', ['sinemalar.com']):
            self.assertFalse(images.remote_host_allowed('https://image.tmdb.org/a.jpg'))

    def test_batch_budget_defers_and_error_breaker(self):
        jobs = {str(i): {'title': 't%d' % i, 'year': 2020} for i in range(6)}
        with patch.object(enrich.tmdb, 'find', return_value={'status': 'unmatched'}):
            res, c = enrich.run_batch(jobs, budget=0)
        self.assertEqual((len(res), c['deferred']), (0, 6))
        with patch.object(enrich.tmdb, 'find', return_value={'status': 'error'}):
            res, c = enrich.run_batch(jobs, budget=30, workers=1)
        self.assertEqual(c['errors'], enrich.ERROR_BREAKER)
        self.assertEqual(c['deferred'], 6 - enrich.ERROR_BREAKER)


class IngestEnrichTests(unittest.TestCase):
    setUp = integration.IngestTests.setUp

    def run_ingest(self, items, get, enabled=True, **patches):
        result = RunResult('yabancidizi', items=items, drift={'drift': False})
        with patch.object(ingest_module, 'run_site', return_value=result), \
                patch.object(tmdb, 'enabled', return_value=enabled), \
                patch.object(tmdb, '_get', get), \
                patch.object(ingest_module, '_enrich_series_catalogs'), \
                patch.object(ingest_module, '_fetch_collection', return_value=[]):
            return ingest_module.ingest_source('yabancidizi')

    def catalogue_item(self):
        return cache.Snapshot(LibrarySource()).items[0]

    def test_match_serves_tmdb_artwork_and_keeps_source_image_as_fallback(self):
        calls = []
        result = self.run_ingest([integration.film()], fake_get(calls=calls))
        self.assertIsNone(result['error'])
        self.assertEqual(result['tmdb_enrich']['matched'], 1)
        row = db.query_one('SELECT * FROM library_items')
        self.assertEqual(row['tmdb_id'], 42)
        self.assertEqual(row['imdb_id'], 'tt1234567')
        self.assertEqual(row['tmdb_enrich_status'], 'matched')
        self.assertIn('/uploads/series/mayday.jpg', row['poster_url'])  # source kept
        item = self.catalogue_item()
        self.assertEqual(item['poster_url'], 'https://image.tmdb.org/t/p/w500/tr_hi.jpg')
        self.assertEqual(item['backdrop_url'], 'https://image.tmdb.org/t/p/w1280/null_b.jpg')
        # matched titles are not queried again
        calls.clear()
        self.run_ingest([integration.film()], fake_get(calls=calls))
        self.assertEqual(calls, [])
        self.assertEqual(self.catalogue_item()['poster_url'], 'https://image.tmdb.org/t/p/w500/tr_hi.jpg')

    def test_source_overview_not_overwritten(self):
        self.run_ingest([{**integration.film(), 'synopsis': 'Kaynak ozeti'}], fake_get())
        self.assertEqual(db.query_one('SELECT overview FROM library_items')['overview'], 'Kaynak ozeti')

    def test_no_key_uses_source_artwork(self):
        result = self.run_ingest([integration.film()], fake_get(), enabled=False)
        self.assertIsNone(result['error'])
        item = self.catalogue_item()
        self.assertIn('mayday.jpg', item['poster_url'])
        self.assertIsNone(db.query_one('SELECT tmdb_poster_url FROM library_items')['tmdb_poster_url'])

    def test_tmdb_error_never_fails_ingest_and_is_retried_next_time(self):
        def boom(path, **p): raise tmdb.TmdbError('timeout')
        result = self.run_ingest([integration.film()], boom)
        self.assertIsNone(result['error'])
        self.assertEqual(result['ingested'], 1)
        self.assertEqual(result['tmdb_enrich']['errors'], 1)
        self.assertIn('mayday.jpg', self.catalogue_item()['poster_url'])
        self.assertIsNone(db.query_one('SELECT tmdb_checked_at FROM library_items')['tmdb_checked_at'])
        calls = []
        self.run_ingest([integration.film()], fake_get(calls=calls))
        self.assertTrue(calls)  # error was not stamped, so it retried and now matches

    def test_unhandled_exception_in_lookup_is_swallowed(self):
        def bad(path, **p): raise RuntimeError('boom')
        result = self.run_ingest([integration.film()], bad)
        self.assertIsNone(result['error'])
        self.assertEqual(result['ingested'], 1)

    def test_unmatched_is_stamped_and_not_retried_inside_window(self):
        calls = []
        self.run_ingest([integration.film()], fake_get(hits=[], calls=calls))
        self.assertEqual(db.query_one('SELECT tmdb_enrich_status s FROM library_items')['s'], 'unmatched')
        first = len(calls)
        self.run_ingest([integration.film()], fake_get(hits=[], calls=calls))
        self.assertEqual(len(calls), first)
        self.assertIn('mayday.jpg', self.catalogue_item()['poster_url'])
        db.execute('UPDATE library_items SET tmdb_checked_at=?', (int(time.time()) - 8 * 86400,))
        self.run_ingest([integration.film()], fake_get(hits=[], calls=calls))
        self.assertGreater(len(calls), first)

    def test_low_confidence_goes_to_review_and_keeps_source_artwork(self):
        wrong = [{**HIT, 'title': 'Mayday Parade', 'original_title': 'Mayday Parade'}]
        self.run_ingest([integration.film()], fake_get(hits=wrong))
        row = db.query_one('SELECT * FROM library_items')
        self.assertEqual(row['tmdb_enrich_status'], 'review')
        self.assertIsNone(row['tmdb_poster_url'])
        review = db.query_one('SELECT reason FROM identity_reviews')
        self.assertEqual(review['reason'], 'tmdb_low_confidence')
        self.assertIn('mayday.jpg', self.catalogue_item()['poster_url'])

    def test_series_skipped_while_flag_off(self):
        calls = []
        result = self.run_ingest([{**integration.film(), 'detail_url': 'dizi/mayday'}], fake_get(calls=calls))
        item_type = db.query_one('SELECT type FROM library_items')['type']
        self.assertEqual(item_type, 'series')
        self.assertEqual(calls, [])
        self.assertEqual(result['tmdb_enrich']['skipped'], 1)

    def test_backfill_tool_dry_run_and_apply(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from tools import tmdb_enrich
        self.run_ingest([integration.film()], fake_get(), enabled=False)
        with patch.object(tmdb, 'enabled', return_value=True), patch.object(tmdb, '_get', fake_get()):
            self.assertEqual(tmdb_enrich.main(['x', '--dry-run']), 0)
            self.assertIsNone(db.query_one('SELECT tmdb_poster_url p FROM library_items')['p'])
            self.assertEqual(tmdb_enrich.main(['x']), 0)
        row = db.query_one('SELECT * FROM library_items')
        self.assertEqual(row['tmdb_id'], 42)
        self.assertEqual(row['tmdb_enrich_status'], 'matched')
        self.assertEqual(self.catalogue_item()['poster_url'], 'https://image.tmdb.org/t/p/w500/tr_hi.jpg')


    def run_tool(self, argv, get):
        from tools import tmdb_enrich
        out = io.StringIO()
        with patch.object(tmdb, 'enabled', return_value=True), patch.object(tmdb, '_get', get), \
                contextlib.redirect_stdout(out):
            self.assertEqual(tmdb_enrich.main(['x'] + argv), 0)
        return out.getvalue()

    def test_backfill_verbose_prints_one_decision_line_per_title(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        self.run_ingest([integration.film()], fake_get(), enabled=False)
        with patch.dict(os.environ, {'TMDB_ACCESS_KEY': 'eyJsecret-token-value'}):
            quiet = self.run_tool(['--dry-run'], fake_get())
            loud = self.run_tool(['--dry-run', '--verbose'], fake_get())
            weak = self.run_tool(['--dry-run', '--verbose'],
                                 fake_get(hits=[{**HIT, 'title': 'Mayday Parade', 'original_title': 'Mayday Parade'}]))
            none = self.run_tool(['--dry-run', '--verbose'], fake_get(hits=[]))
        self.assertNotIn('[', quiet)
        self.assertEqual(len(quiet.strip().splitlines()), 3)  # header, progress, counters
        lines = [l for l in loud.splitlines() if l.strip().startswith('[')]
        self.assertEqual(len(lines), 1)
        self.assertIn('[auto] Mayday (2026) -> Mayday (2026) tmdb=42 score=1.000', lines[0])
        self.assertIn('sim 1.00', lines[0])
        self.assertIn('[review] Mayday (2026) -> Mayday Parade (2026) tmdb=42', weak)
        self.assertIn('[unmatched] Mayday (2026) -> - score=- | no candidates', none)
        for text in (quiet, loud, weak, none):
            self.assertNotIn('secret', text)

    def test_verbose_line_formats(self):
        from tools import tmdb_enrich
        row = {'title': 'Cadde', 'year': None}
        self.assertEqual(tmdb_enrich.verbose_line(row, None), '  [deferred] Cadde (?) | budget exhausted')
        self.assertEqual(tmdb_enrich.verbose_line(row, {'status': 'error', 'error': 'HTTP 429'}),
                         '  [error] Cadde (?) | HTTP 429')
        line = tmdb_enrich.verbose_line(row, {'status': 'matched', 'score': 1.0, 'reason': 'known id', 'data': {}})
        self.assertEqual(line, '  [auto] Cadde (?) -> tmdb id (known) score=1.000 | known id')


if __name__ == '__main__':
    unittest.main()
