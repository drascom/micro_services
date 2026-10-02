"""Source-independent catalogue views for TV navigation."""
import time
from .cache import fold
from . import rows

from .genres import GENRES  # central genre id -> Turkish label dictionary (app/genres.py)


def trailer_dead(provider):
    """``video_sources.trailer_dead``: the trailer's YouTube video was verified gone/private/non-embeddable
    (library/trailer_check.py). Not a playback failure: status/failures/K-T are not involved."""
    try: return bool(provider['trailer_dead'])
    except (KeyError, IndexError): return False


def availability(providers):
    full=[v for v in providers if v['kind']!='trailer']
    active=[v for v in full if v['status'] in ('unknown','healthy')]
    suspect=[v for v in full if v['status']=='suspect']
    return {'state':'ready' if active else 'check_required' if suspect else 'unavailable',
            'reason':None if active or suspect else 'sources_unavailable' if full else 'no_video_source',
            'has_trailer':any(v['kind']=='trailer' and v['status'] not in ('broken','disabled') and not trailer_dead(v) for v in providers)}


def list_items(snap, profile, kind='', genre='', year=None, available='', sort='new', q='', mine=False, offset=0, limit=20):
    pool=[i for i in snap.items if not kind or i['type']==kind]
    all_pool=pool
    if mine:
        ids=set(rows.mylist_ids(profile));pool=[i for i in pool if i['id'] in ids]
    if genre:
        label=GENRES.get(genre)
        pool=[i for i in pool if label in i.get('genres',[])]
    if year is not None: pool=[i for i in pool if i.get('year')==year]
    if available: pool=[i for i in pool if i.get('availability',{}).get('state','ready')==available]
    needle=fold(q or '').strip()
    if needle: pool=[i for i in pool if needle in fold(i['title']+' '+(i.get('original_title') or ''))]
    rank=lambda i: 0 if i.get('availability',{}).get('state','ready')=='ready' else 1
    if sort in ('trending','popular'):
        # trend_score (homelayout): `trending` = popularity + recency, `popular` = the same without the time-based parts
        from . import homelayout
        sig,now=homelayout.signals(snap),time.time()
        score={i['id']:homelayout.trend_score(i,sig,now,include_fresh=sort=='trending') for i in pool}
        pool.sort(key=lambda i:(rank(i),-score[i['id']],-homelayout._num(i.get('rating')),i['id']))
    elif sort=='title': pool.sort(key=lambda i:(fold(i['title']),i['id']))
    elif sort=='year': pool.sort(key=lambda i:(-(i.get('year') or 0),fold(i['title']),i['id']))
    else: pool.sort(key=lambda i:(rank(i),-(i.get('added_at') or 0),i['id']))
    genres=[{'id':key,'name':name} for key,name in GENRES.items() if any(name in i.get('genres',[]) for i in all_pool)]
    return {'items':rows.items_json(pool[offset:offset+limit],rows.progress_map(profile)),
            'total':len(pool),'offset':offset,'limit':limit,'genres':genres,
            'years':sorted({i['year'] for i in all_pool if i.get('year')},reverse=True)}
