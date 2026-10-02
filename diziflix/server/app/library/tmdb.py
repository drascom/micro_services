"""TMDB client: title/year matching, metadata and artwork selection.

Credentials come from ``TMDB_ACCESS_KEY`` (v4 bearer JWT *or* v3 api key, detected
automatically), ``TMDB_TOKEN`` (bearer) or ``TMDB_API_KEY`` (v3). The key is never
logged. Every network failure degrades to "no result" so ingest can never fail
because of TMDB.
"""
import collections
import difflib
import functools
import logging
import os
import re
import time
import unicodedata

import httpx

from .. import config

log = logging.getLogger("library.tmdb")
_BASE = 'https://api.themoviedb.org/3'
IMG_BASE = 'https://image.tmdb.org/t/p/'

# Defaults of the (env-tunable, see config.py) thresholds; the matcher reads
# ``config.TMDB_*`` at call time, these names are kept for callers/tests.
HIGH_CONFIDENCE = config.TMDB_AUTO_SCORE    # auto-match needs at least this score ...
LOW_CONFIDENCE = config.TMDB_REVIEW_SCORE   # between LOW and HIGH -> review queue; below -> unmatched
MARGIN = config.TMDB_MARGIN                 # ... and a lead over the runner-up of this much
YEAR_MISMATCH_PENALTY = 0.1                 # both years known but >1 apart


class TmdbError(Exception):
    """Network / HTTP failure talking to TMDB (not a 'no match' outcome).

    ``status`` is the HTTP status when the failure was an HTTP error response (else None)."""

    def __init__(self, message='', status=None):
        super().__init__(message)
        self.status = status


def credentials():
    """Return ``(kind, key)``; kind is 'bearer', 'api_key' or None."""
    for name in ('TMDB_ACCESS_KEY', 'TMDB_TOKEN', 'TMDB_API_KEY'):
        key = (os.environ.get(name) or '').strip()
        if key:
            return key_kind(key), key
    return None, ''


def key_kind(key):
    """v4 read-access tokens are long JWTs (``eyJ...``); v3 keys are 32 hex chars."""
    key = (key or '').strip()
    return 'bearer' if key.startswith('eyJ') or len(key) > 40 else 'api_key'


def enabled():
    return credentials()[0] is not None


def _get(path, **params):
    kind, key = credentials()
    if not kind:
        raise TmdbError('no key')
    headers = {}
    if kind == 'bearer':
        headers['Authorization'] = 'Bearer ' + key
    else:
        params['api_key'] = key
    for attempt in range(3):
        try:
            res = httpx.get(_BASE + path, params=params, headers=headers, timeout=config.TMDB_TIMEOUT)
        except httpx.HTTPError as exc:
            raise TmdbError(type(exc).__name__) from None
        if res.status_code == 429 and attempt < 2:
            try:
                wait = float(res.headers.get('Retry-After') or 1)
            except ValueError:
                wait = 1.0
            time.sleep(min(max(wait, 0.5), 10))
            continue
        if res.status_code >= 400:
            # Never include the URL (it may carry the api key).
            raise TmdbError('HTTP %d' % res.status_code, res.status_code)
        try:
            return res.json()
        except ValueError:
            raise TmdbError('bad json') from None
    raise TmdbError('rate limited')


# --- series title tidy-up (search/scoring only; the stored source title is never changed) --------
# Dizi kaynakları başlığa sezon/etiket ekler: "Dark 3. Sezon", "Dark Sezon 3", "Dark Season 3", "Dark (Dizi)".
_SERIES_SUFFIXES = tuple(re.compile(p, re.IGNORECASE) for p in (
    r'\(\s*dizi\s*\)',
    r'\bsezon\s*\d{1,2}\b',              # "Sezon 3" first, so "24 Sezon 2" -> "24"
    r'\bseason\s*\d{1,2}\b',
    r'\b\d{1,2}\s*\.\s*sezon\b',          # "3. Sezon"
    r'\b\d{1,2}(?:st|nd|rd|th)\s+season\b',
    r'\b\d{1,2}\s+sezon\b',              # "3 Sezon"
))


def series_query_title(text):
    """``text`` without season/"(Dizi)" suffixes (falls back to ``text`` if nothing is left)."""
    if not text:
        return text
    out = text
    for rx in _SERIES_SUFFIXES:
        out = rx.sub(' ', out)
    if out == text:
        return text  # nothing to strip: keep the title byte-identical
    out = re.sub(r'\s+', ' ', out).strip(' \t-–—:,.|/')
    return out if _fold(out) else text


def _fold(text):
    return re.sub(r'[^\w]+', '', unicodedata.normalize('NFKD', text or '').casefold().replace('ı', 'i'))


def _kind(media_type):
    return 'tv' if media_type == 'series' else 'movie'


def _year(value):
    m = re.match(r'(\d{4})', str(value or ''))
    return int(m.group(1)) if m else None


def image_url(path, size):
    return IMG_BASE + size + path if path else None


# --- title normalisation -------------------------------------------------------

_PREFIXES = {'wwe', 'wwf', 'the'}                 # dropped when leading ("WWE Wrestlemania 26")
_ROMAN_RE = re.compile(r'^(x{0,3})(ix|iv|v?i{0,3})$')
_ROMAN_VAL = {'i': 1, 'v': 5, 'x': 10}
_SUBTITLE_SPLIT = re.compile(r':\s+|\s[-–—]\s')
_QUOTES = re.compile('["“”„‟]')

_Title = collections.namedtuple('_Title', 'base norm main main_norm sub sub_norm')


def _clean_query(text):
    """Plain title text: no escaped/typographic double quotes, tidy spacing (" :" -> ":")."""
    s = _QUOTES.sub('', (text or '').replace('\\', ''))
    return re.sub(r'\s+', ' ', re.sub(r'\s+:', ':', s)).strip()


def _words(text):
    """Accent/case-folded word tokens (same folding as ``_fold`` but keeps boundaries)."""
    s = unicodedata.normalize('NFKD', text or '').casefold().replace('ı', 'i')
    return re.findall(r'\w+', ''.join(c for c in s if not unicodedata.combining(c)))


def _roman(word):
    if not word or not _ROMAN_RE.match(word):
        return None
    total = prev = 0
    for ch in reversed(word):
        v = _ROMAN_VAL[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def _norm(text):
    """Comparison form: roman numerals -> arabic, leading WWE/WWF/The dropped."""
    words = _words(text)
    while len(words) > 1 and words[0] in _PREFIXES:
        words.pop(0)
    out = []
    for i, w in enumerate(words):
        n = None if (i == 0 and len(w) == 1) else _roman(w)  # "I Am Legend", "X-Men" stay
        out.append(str(n) if n else w)
    return ''.join(out)


@functools.lru_cache(maxsize=4096)
def _prep(text):
    text = _clean_query(text)
    base = _fold(text)
    if not base:
        return None
    main = sub = main_norm = sub_norm = ''
    parts = _SUBTITLE_SPLIT.split(text, maxsplit=1)
    if len(parts) == 2 and _fold(parts[0]) and _fold(parts[1]):
        main, sub, main_norm, sub_norm = _fold(parts[0]), _fold(parts[1]), _norm(parts[0]), _norm(parts[1])
    return _Title(base, _norm(text), main, main_norm, sub, sub_norm)


def _preps(*texts):
    seen, out = set(), []
    for t in texts:
        p = _prep(t) if t else None
        if p and p.base not in seen:
            seen.add(p.base)
            out.append(p)
    return tuple(out)


def _ratio(a, b):
    if not a or not b:
        return 0.0
    return 1.0 if a == b else difflib.SequenceMatcher(None, a, b).ratio()


def _title_sim(a, b):
    """0..1 similarity of two prepared titles. 1.0 = same after folding; 0.98 = same after
    numeral/prefix normalisation; a shared main title with a different or missing
    subtitle is capped well below the auto-match floor (base film vs its edition)."""
    if a.base == b.base:
        return 1.0
    best = max(_ratio(a.base, b.base), 0.98 if a.norm == b.norm else _ratio(a.norm, b.norm))
    if a.main and b.main:
        main = max(_ratio(a.main, b.main), _ratio(a.main_norm, b.main_norm))
        best = max(best, min(0.95, 0.6 * main + 0.4 * max(_ratio(a.sub, b.sub), _ratio(a.sub_norm, b.sub_norm))))
    elif a.main or b.main:  # only one side has a subtitle
        main = max(_ratio(a.main or a.base, b.main or b.base), _ratio(a.main_norm or a.norm, b.main_norm or b.norm))
        best = max(best, 0.85 * main)
    return best


def _assess(title, year, original_title, cand, kind='movie', extra=()):
    """Score one search hit. ``extra`` = alternative/translated titles of the candidate.

    Returns ``sim`` (title similarity), ``score`` (sim*0.7 + year agreement: +0.3 same
    year, +0.15 off by one, -0.1 when both years are known but further apart; no year
    on either side -> sim*0.7), ``year`` state (exact/near/far/none/cand_none) and
    ``via`` ('alt' when the best similarity came from an alternative title)."""
    src = _preps(title, original_title)
    own = _preps(*(cand.get(k) for k in ('title', 'original_title', 'name', 'original_name')))
    alt = _preps(*extra)
    if not src or not (own or alt):
        return {'sim': 0.0, 'score': 0.0, 'year': 'none', 'via': ''}
    s_own = max((_title_sim(a, b) for a in src for b in own), default=0.0)
    s_alt = max((_title_sim(a, b) for a in src for b in alt), default=0.0)
    sim = max(s_own, s_alt)
    sy = _year(year)
    cy = _year(cand.get('first_air_date' if kind == 'tv' else 'release_date'))
    if not sy:
        state = 'none'
    elif not cy:
        state = 'cand_none'
    else:
        diff = abs(sy - cy)
        state = 'exact' if diff == 0 else 'near' if diff == 1 else 'far'
    if state == 'exact':
        val = sim * 0.7 + 0.3
    elif state == 'near':
        val = sim * 0.7 + 0.15
    elif state == 'far':
        val = max(0.0, sim * 0.7 - YEAR_MISMATCH_PENALTY)
    else:
        val = sim * 0.7  # cannot confirm the year
    return {'sim': round(sim, 4), 'score': round(val, 4), 'year': state, 'via': 'alt' if s_alt > s_own else ''}


def score(title, year, original_title, cand, kind='movie', extra=()):
    """0..1 confidence: title similarity (70%) + release-year agreement (30%)."""
    return _assess(title, year, original_title, cand, kind, extra)['score']


def _auto_ok(a):
    """Could a candidate with assessment ``a`` be auto-matched (ignoring the lead)?"""
    if a['year'] in ('exact', 'near'):
        return a['score'] >= config.TMDB_AUTO_SCORE and a['sim'] >= config.TMDB_MIN_SIM
    if a['year'] == 'none':
        return a['sim'] >= config.TMDB_MIN_SIM_NOYEAR
    return False


def _search_plan(title, original_title):
    """Base searches: localised title in tr-TR, then original (else same) title in en-US."""
    t, o = _clean_query(title), _clean_query(original_title)
    return [(t, 'tr-TR'), ((o if o and _fold(o) != _fold(t) else t), 'en-US')]


def _fallback_plan(title, year, original_title):
    """Extra searches for titles the base plan could not place: dotless-ı slips
    ("Shake ıt Up") and an apostrophe year ("Road Wild '99" -> "1999"). At most 2."""
    century = (_year(year) or 0) // 100 or None
    plan, seen = [], set()
    for text in (_clean_query(title), _clean_query(original_title)):
        if not text:
            continue
        fixed = text.replace('ı', 'i').replace('İ', 'I')
        fixed = re.sub(r"'(\d\d)\b", lambda m: '%d%s' % (century or (19 if int(m.group(1)) >= 30 else 20), m.group(1)), fixed)
        if _fold(fixed) not in seen and fixed != text:
            seen.add(_fold(fixed))
            plan.append((fixed, 'en-US'))
    return plan[:2]


def _alt_names(tmdb_id, kind):
    """Alternative + translated titles of one TMDB entry (one request)."""
    d = _get('/%s/%s' % (kind, tmdb_id), append_to_response='alternative_titles,translations')
    names = []
    try:
        alts = d.get('alternative_titles') or {}
        names += [t.get('title') for t in (alts.get('titles') or alts.get('results') or [])]
        for t in (d.get('translations') or {}).get('translations') or []:
            data = t.get('data') or {}
            names.append(data.get('title') or data.get('name'))
    except (AttributeError, TypeError):
        return []
    return [n for n in dict.fromkeys(names) if n]


def _rated_candidates(title, year, original_title, kind):
    """``[(hit, assessment)]`` best first.

    1. base searches (stop early once a hit could be auto-matched);
    2. nothing auto-matchable -> alternative/translated titles of the best plausible
       candidates (<= TMDB_ALT_TITLE_LOOKUPS requests in total) and re-scoring;
    3. still nothing plausible -> fallback searches, then the same alt-title step
       with whatever request budget is left."""
    seen, extra_of = {}, {}
    left = [max(0, config.TMDB_ALT_TITLE_LOOKUPS)]

    def rate():
        rated = [(h, _assess(title, year, original_title, h, kind, extra_of.get(h['id'], ()))) for h in seen.values()]
        return sorted(rated, key=lambda r: -r[1]['score'])

    def search(plan):
        for query, lang in plan:
            if not query:
                continue
            for h in _get('/search/' + kind, query=query, language=lang, include_adult='false').get('results', []):
                if h.get('id') and h['id'] not in seen:
                    seen[h['id']] = h
            if any(_auto_ok(a) for _, a in rate()):
                return

    def alt_titles():
        rated = rate()
        if not rated or left[0] <= 0 or any(_auto_ok(a) for _, a in rated):
            return
        # Best few candidates whose year is not known to be clearly different.
        todo = [h for h, a in rated if a['year'] != 'far' and h['id'] not in extra_of][:left[0]]
        for h in todo:
            try:
                extra_of[h['id']] = _alt_names(h['id'], kind)
            except TmdbError as exc:
                log.info('tmdb alt-title lookup skipped: %s', exc)
                left[0] = 0
                return
            left[0] -= 1

    search(_search_plan(title, original_title))
    alt_titles()
    rated = rate()
    if not rated or rated[0][1]['score'] < config.TMDB_REVIEW_SCORE:
        search(_fallback_plan(title, year, original_title))
        alt_titles()
        rated = rate()
    return rated


def _pick_image(items, fallback_path):
    """Best image path: tr first, then language-less, then en; votes/width break ties."""
    rank = {'tr': 0, None: 1, 'en': 2}

    def key(i):
        return (rank.get(i.get('iso_639_1'), 3), -(i.get('vote_average') or 0), -(i.get('width') or 0))
    usable = [i for i in items or [] if i.get('file_path')]
    if usable:
        return sorted(usable, key=key)[0]['file_path']
    return fallback_path


def details(tmdb_id, media_type='movie'):
    """Fetch detail + images and normalise into the fields the library uses."""
    kind = _kind(media_type)
    d = _get('/%s/%s' % (kind, tmdb_id), append_to_response='external_ids,images',
             include_image_language='tr,en,null', language='tr-TR')
    images = d.get('images') or {}
    poster = _pick_image(images.get('posters'), d.get('poster_path'))
    backdrop = _pick_image(images.get('backdrops'), d.get('backdrop_path'))
    runtime = (d.get('runtime') or ((d.get('episode_run_time') or [None])[0])
               or (d.get('last_episode_to_air') or {}).get('runtime'))  # TMDB dropped episode_run_time for many series
    return {
        'tmdb_id': d['id'],
        'imdb_id': d.get('imdb_id') or (d.get('external_ids') or {}).get('imdb_id') or None,
        'overview': d.get('overview') or None,
        'original_title': d.get('original_title') or d.get('original_name'),
        'genres': [g['name'] for g in d.get('genres', []) if g.get('name')],
        'rating': d.get('vote_average') if d.get('vote_count') else None,
        'runtime': runtime or None,
        'poster_url': image_url(poster, config.TMDB_POSTER_SIZE),
        'backdrop_url': image_url(backdrop, config.TMDB_BACKDROP_SIZE),
    }


# --- seasons: poster + episode metadata/stills --------------------------------
# TMDB answers untranslated fields with '' or a generic label ("Bölüm 3", "Episode 3", "Sezon 2"); those
# count as blank so the en-US answer (or the source's own value) can fill them.
_GENERIC_EPISODE = re.compile(r'^\s*(?:(?:bölüm|bolum|episode|ep\.?)\s*\d+|\d+\s*\.?\s*(?:bölüm|bolum|episode))\s*$', re.IGNORECASE)
_GENERIC_SEASON = re.compile(r'^\s*(?:(?:sezon|season)\s*\d+|\d+\s*\.?\s*(?:sezon|season))\s*$', re.IGNORECASE)


def generic_episode_title(text):
    """True for empty / placeholder episode titles ("3. Bölüm", "Bölüm 3", "Episode 3")."""
    return not (text or '').strip() or bool(_GENERIC_EPISODE.match(text))


def generic_season_name(text):
    return not (text or '').strip() or bool(_GENERIC_SEASON.match(text))


def _parse_season(d):
    """TMDB ``tv/{id}/season/{n}`` body -> ``{name, overview, air_date, poster_path, episodes{n: {...}}}``
    (raw text values: '' = TMDB has none)."""
    d = d or {}
    eps = {}
    for e in d.get('episodes') or []:
        n = e.get('episode_number') if isinstance(e, dict) else None
        if isinstance(n, int) and not isinstance(n, bool) and n >= 0:
            eps[n] = {'title': (e.get('name') or '').strip(), 'overview': (e.get('overview') or '').strip(),
                      'air_date': e.get('air_date') or None, 'runtime': e.get('runtime') or None,
                      'still_path': e.get('still_path') or None}
    return {'name': (d.get('name') or '').strip(), 'overview': (d.get('overview') or '').strip(),
            'air_date': d.get('air_date') or None, 'poster_path': d.get('poster_path') or None, 'episodes': eps}


def _needs_fallback(p):
    """Any season overview / episode title / episode overview still blank (or a generic episode label) in
    the primary (tr-TR) language? (A generic season name - "Sezon 2" - is normal and no reason to ask again.)"""
    if not p['overview']:
        return True
    return any(generic_episode_title(e['title']) or not e['overview'] for e in p['episodes'].values())


def season_details(tmdb_id, season_number):
    """One season: poster, name/overview/air date and every episode (``episode``, ``title``, ``overview``,
    ``air_date``, ``runtime``, ``still_url``). ``tr-TR`` first; blank/generic titles and blank overviews are
    filled from an ``en-US`` answer (one extra request, only when something is blank). Blank -> ``None``.
    Raises ``TmdbError`` (``.status == 404`` when TMDB does not know the season)."""
    path = '/tv/%s/season/%s' % (tmdb_id, season_number)
    tr = _parse_season(_get(path, language='tr-TR'))
    en = None
    if _needs_fallback(tr):
        try:
            en = _parse_season(_get(path, language='en-US'))
        except TmdbError as exc:
            if exc.status is None:
                raise  # transport trouble: let the caller retry later instead of storing half a season
            log.info('tmdb season en-US fallback skipped: %s', exc)
    en = en or {'name': '', 'overview': '', 'air_date': None, 'poster_path': None, 'episodes': {}}

    def pick(a, b, generic):
        return a if not generic(a) else (b if not generic(b) else None)

    blank = lambda v: not (v or '').strip()  # noqa: E731
    episodes = []
    for n in sorted(set(tr['episodes']) | set(en['episodes'])):
        t = tr['episodes'].get(n) or {}
        e = en['episodes'].get(n) or {}
        episodes.append({
            'episode': n,
            'title': pick(t.get('title'), e.get('title'), generic_episode_title),
            'overview': pick(t.get('overview'), e.get('overview'), blank),
            'air_date': t.get('air_date') or e.get('air_date'),
            'runtime': t.get('runtime') or e.get('runtime'),
            'still_url': image_url(t.get('still_path') or e.get('still_path'), config.TMDB_STILL_SIZE),
        })
    return {
        'season': int(season_number),
        'name': pick(tr['name'], en['name'], generic_season_name),
        'overview': pick(tr['overview'], en['overview'], blank),
        'air_date': tr['air_date'] or en['air_date'],
        'poster_url': image_url(tr['poster_path'] or en['poster_path'], config.TMDB_POSTER_SIZE),
        'episodes': episodes,
    }


def season(tmdb_id, season_number):
    """Never raises. ``{'status': 'ok', 'data': season_details}`` | ``'empty'`` (TMDB has no such season, or
    nothing useful in it) | ``'error'`` (network / rate limit / auth: the caller must not stamp an attempt) |
    ``'disabled'`` (no key)."""
    if not enabled():
        return {'status': 'disabled'}
    try:
        data = season_details(tmdb_id, season_number)
    except TmdbError as exc:
        if exc.status == 404:
            return {'status': 'empty', 'reason': 'not found on TMDB'}
        log.warning('tmdb season lookup failed: %s', exc)
        return {'status': 'error', 'error': str(exc)[:80]}
    except (KeyError, TypeError, ValueError) as exc:
        log.warning('tmdb season parse failed: %s', exc)
        return {'status': 'error', 'error': type(exc).__name__}
    useful = (data['poster_url'] or data['name'] or data['overview'] or data['air_date']
              or any(e['title'] or e['overview'] or e['still_url'] or e['air_date'] or e['runtime'] for e in data['episodes']))
    if not useful:
        return {'status': 'empty', 'reason': 'season has no data'}
    return {'status': 'ok', 'data': data}


def _cand(hit, a, kind):
    c = {'id': hit['id'], 'title': hit.get('title') or hit.get('name'),
         'year': _year(hit.get('first_air_date' if kind == 'tv' else 'release_date')),
         'score': a['score'], 'sim': a['sim'], 'ystate': a['year']}
    if hit.get('original_title') or hit.get('original_name'):
        c['original_title'] = hit.get('original_title') or hit.get('original_name')
    if hit.get('poster_path'):
        c['poster_url'] = image_url(hit['poster_path'], 'w92')  # tiny thumbnail for the admin preview
    if a['via']:
        c['via'] = 'alt-title'
    return c


def _decide(best, runner, year):
    """``(status, reason)`` for the best candidate; ``runner`` = second best score."""
    lead = best['score'] - runner
    st, sim = best['ystate'], best['sim']
    if st in ('exact', 'near'):
        if best['score'] < config.TMDB_AUTO_SCORE:
            return 'review', 'score %.2f < %.2f' % (best['score'], config.TMDB_AUTO_SCORE)
        if sim < config.TMDB_MIN_SIM:
            return 'review', 'title sim %.2f < %.2f (year cannot rescue)' % (sim, config.TMDB_MIN_SIM)
    elif st == 'none':
        if sim < config.TMDB_MIN_SIM_NOYEAR:
            return 'review', 'no source year: title sim %.2f < %.2f' % (sim, config.TMDB_MIN_SIM_NOYEAR)
    elif st == 'cand_none':
        return 'review', 'candidate has no release year'
    else:
        return 'review', 'year mismatch (%s vs %s)' % (_year(year), best['year'])
    if lead < config.TMDB_MARGIN:
        return 'review', 'ambiguous: lead %.2f < %.2f' % (lead, config.TMDB_MARGIN)
    why = 'sim %.2f, year %s, lead %.2f' % (sim, {'none': 'unknown', 'exact': 'exact', 'near': '+-1'}[st], lead)
    return 'matched', why + (', via alt title' if best.get('via') else '')


def find(title, year=None, original_title=None, media_type='movie', tmdb_id=None, imdb_id=None):
    """Resolve one title. Returns a dict with ``status``:

    matched  -> ``data`` (details), ``score``, ``candidate``, ``reason``
    review   -> plausible but not certain; ``candidates`` for the review queue
    unmatched-> nothing plausible
    error    -> TMDB unreachable / failed (caller must not record an attempt)

    Every result carries a short ``reason``. Auto-match needs: score >= TMDB_AUTO_SCORE
    *and* title similarity >= TMDB_MIN_SIM (the year bonus never rescues a weak title),
    a lead of TMDB_MARGIN over the runner-up and a year within +-1. A source without a
    year needs similarity >= TMDB_MIN_SIM_NOYEAR and the same lead. When the hit's own
    titles are not similar enough, the best candidates' alternative/translated titles
    are fetched (<= TMDB_ALT_TITLE_LOOKUPS requests) and the candidates re-scored.
    """
    if not enabled():
        return {'status': 'disabled'}
    kind = _kind(media_type)
    if kind == 'tv':  # search/score on the bare series name; source titles carry season tags
        title, original_title = series_query_title(title), series_query_title(original_title)
    try:
        if not tmdb_id and imdb_id:
            hits = _get('/find/' + imdb_id, external_source='imdb_id').get(kind + '_results', [])
            if len(hits) == 1:
                tmdb_id = hits[0]['id']
        if tmdb_id:
            return {'status': 'matched', 'score': 1.0, 'reason': 'known id', 'data': details(tmdb_id, media_type)}
        if not title:
            return {'status': 'unmatched', 'candidates': [], 'reason': 'no title'}
        rated = _rated_candidates(title, year, original_title, kind)
        cands = [_cand(h, a, kind) for h, a in rated[:5]]
        if not cands:
            return {'status': 'unmatched', 'candidates': [], 'reason': 'no candidates'}
        best = cands[0]
        if best['score'] < config.TMDB_REVIEW_SCORE:
            return {'status': 'unmatched', 'candidates': cands,
                    'reason': 'best score %.2f < %.2f' % (best['score'], config.TMDB_REVIEW_SCORE)}
        status, reason = _decide(best, cands[1]['score'] if len(cands) > 1 else 0, year)
        if status == 'matched':
            return {'status': 'matched', 'score': best['score'], 'candidate': best, 'reason': reason,
                    'data': details(best['id'], media_type)}
        return {'status': 'review', 'score': best['score'], 'candidates': cands, 'reason': reason}
    except (TmdbError, KeyError, TypeError, ValueError) as exc:
        log.warning('tmdb lookup failed: %s', exc)
        return {'status': 'error', 'error': str(exc)[:80]}


def match(title, year=None, original_title=None, media_type='movie', tmdb_id=None, imdb_id=None):
    """Backward-compatible wrapper: details dict on a confident match, else None."""
    res = find(title, year, original_title, media_type, tmdb_id, imdb_id)
    if res.get('status') != 'matched':
        return None
    data = dict(res['data'])
    data.setdefault('poster_url', None)
    return data
