"""Per-title/per-episode video providers and playback health, independent of UI."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import closing
import hashlib
import json
import threading
import time
import uuid
from urllib.parse import urljoin, urlparse
from .. import config as app_config, db
from . import streamdiag, streamlife, tracks

TTL=6*3600  # legacy trailer cache on source_items; resolved payloads follow config.RESOLVE_CACHE_TTL
RESOLVER_VERSION=7  # 7: streams may carry request_headers (OK.ru mp4: the User-Agent its signed URL is bound to; /api/streams serves them through the stream proxy), cached payloads without them are re-resolved (6: candidates carry resolver_type, json_api streams skip the provider step, per-site provider allow-list (5: re-signed duplicates merged, spare copies marked (mirror_of), sub_known/site_lang_hint, no unmeasured language in labels; 4: soft subtitle tracks + per-stream variant_id/audio_lang/sub_mode/hard_lang; 3: labels carry the subtitle language))
BLOCKED_PREFIX='engelli: '


class Blocked(ValueError):
    """The playback page carries a ``blocked:`` placeholder (a copyright / access block, scraper/blocked.py): content that is not
    public. Not a failure of the source: its health counters (suspect / broken), the playheal window and the source finder's
    repair step are not involved (``video_sources.status='blocked'``)."""


def is_blocked(exc):
    """``exc`` is (or carries the message of) a :class:`Blocked`: a negative-cache hit comes back as a plain ``ValueError``."""
    return isinstance(exc,Blocked) or str(exc).startswith(BLOCKED_PREFIX)


_neg_lock=threading.Lock()
_neg_sources={}     # source id -> (monotonic expiry, message): every candidate of the source failed
_neg_candidates={}  # candidate key -> (monotonic expiry, message)
_prefetching=set()
_refreshing=set()   # source ids whose background refresh (refresh-ahead) is running: one per source
_refresh_backoff={} # source id -> (monotonic expiry, message): its last background refresh failed, do not hammer the provider
_last_prune=0.0


def _url(value):
    p=urlparse(str(value or ''))
    return str(value) if p.scheme in ('http','https') and p.hostname else None


def sync_source(conn, site, key, cid, norm, cfg):
    entries=list(norm.get('video_sources') or [])
    if cfg.data.get('playback') != 'trailer' and norm.get('trailer_url'):
        entries.append({'url':norm['trailer_url'],'kind':'trailer','resolver':'embed',
                        'type':'embed','label':'Fragman'})
    if cfg.data.get('playback')=='trailer' and norm.get('source_url') and (not norm.get('trailer_checked') or norm.get('trailer_url')):
        entries.append({'url':norm['source_url'],'kind':'trailer','resolver':'page','label':'Fragman'})
    elif cfg.data.get('playback')=='video' and norm.get('type')!='series' and norm.get('source_url'):
        entries.append({'url':norm['source_url'],'kind':'movie','resolver':'page'})
    if getattr(cfg,'blocked',None) or getattr(cfg,'availability_gate',None):
        # content that is not public (library/gate.py): an episode / film page with a fresh blocked verdict is not written
        from . import gate
        blocked=gate.fresh_blocked_urls(site,key,conn)
        if blocked: entries=[e for e in entries if e.get('url') not in blocked]
    for entry in entries:
        locator=_url(entry.get('url'))
        kind=entry.get('kind','movie')
        if not locator or kind not in ('movie','episode','trailer'): continue
        season,episode=entry.get('season'),entry.get('episode')
        if kind=='episode':
            if norm.get('type')!='series' or not isinstance(season,int) or not isinstance(episode,int) or season<0 or episode<1: continue
            episode_id=f'{cid}:s{season}:e{episode}'
        else:
            if kind=='movie' and norm.get('type')=='series': continue
            episode_id=''
        identity='|'.join([site,key,kind,str(season),str(episode),entry.get('key') or locator])
        sid='vs_'+hashlib.sha256(identity.encode()).hexdigest()[:24]
        resolver=entry.get('resolver','direct')
        if resolver not in ('page','direct','embed'): continue
        media_type=entry.get('type','hls' if '.m3u8' in locator else 'mp4')
        if media_type not in ('hls','mp4','embed'): continue
        conn.execute('''INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,season,episode,
            kind,locator,resolver,media_type,label,language,episode_title,episode_overview,
            episode_still_url,episode_runtime,episode_air_date,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET canonical_id=excluded.canonical_id,label=excluded.label,
            language=COALESCE(excluded.language,language),
            episode_title=COALESCE(excluded.episode_title,episode_title),
            episode_overview=COALESCE(excluded.episode_overview,episode_overview),
            episode_still_url=COALESCE(excluded.episode_still_url,episode_still_url),
            episode_runtime=COALESCE(excluded.episode_runtime,episode_runtime),
            episode_air_date=COALESCE(excluded.episode_air_date,episode_air_date),
            updated_at=excluded.updated_at,
            resolved_payload=CASE WHEN locator!=excluded.locator THEN NULL ELSE resolved_payload END,
            trailer_dead=CASE WHEN locator!=excluded.locator THEN 0 ELSE trailer_dead END,
            locator=excluded.locator''',
            (sid,cid,site,key,episode_id,season,episode,kind,locator,resolver,media_type,
             entry.get('label') or cfg.data.get('display_name',site),entry.get('language'),
             entry.get('title') or None,entry.get('overview') or None,entry.get('still_url') or None,
             entry.get('runtime') or None,entry.get('air_date') or None,int(time.time())))


def backfill():
    """Idempotently register existing scraper records without running a crawl."""
    from ..scraper import config
    from . import identity
    configs={sid:config.load_site(sid) for sid in config.list_sites()}
    with closing(db.connect()) as conn, conn:
        for row in conn.execute('SELECT * FROM source_items WHERE canonical_id IS NOT NULL').fetchall():
            cfg=configs.get(row['source'])
            if cfg:
                norm=json.loads(row['normalized'] or '{}')
                norm.setdefault('source_url',row['source_url'])
                norm['source_key']=row['source_key']
                known=conn.execute('SELECT 1 FROM external_ids WHERE canonical_id=?',(row['canonical_id'],)).fetchone()
                if not known: identity.review(conn,row['source'],row['source_key'],row['canonical_id'],'missing_external_id')
                sync_source(conn,row['source'],row['source_key'],row['canonical_id'],norm,cfg)


def _candidate_key(raw):
    handoff=raw.get('handoff') or {}
    # generic ajax hand-offs share the page URL and carry no ``link``: their form values tell them apart
    form=json.dumps(handoff['form'],sort_keys=True,default=str) if handoff.get('form') else ''
    return '|'.join([str(raw.get('url') or ''),str(handoff.get('link') or ''),str(raw.get('label') or '')]+([form] if form else []))


def _neg_get(cache,key):
    with _neg_lock:
        hit=cache.get(key)
        if hit and hit[0]>time.monotonic(): return hit[1]
        cache.pop(key,None)
    return None


def _neg_set(cache,key,message):
    ttl=app_config.RESOLVE_NEG_TTL
    if ttl<=0: return
    with _neg_lock:
        if len(cache)>2000:
            now=time.monotonic()
            for stale in [k for k,v in cache.items() if v[0]<=now]: cache.pop(stale,None)
        cache[key]=(time.monotonic()+ttl,str(message)[:250])


def reset_caches():
    """Forget the short-lived negative results and the background-refresh bookkeeping (tests, admin)."""
    with _neg_lock:
        _neg_sources.clear();_neg_candidates.clear();_refreshing.clear();_refresh_backoff.clear()


_LANG_RANK={'tr':0,'en':2}  # candidates of the site's default (Turkish subtitle) tab first, then English, unknown in between


def _rank(candidate,outcome):
    """Menu order bucket of a candidate: Turkish-subtitle files with a MEASURED subtitle state (VidMolly) first, files whose
    subtitle state is unknown (OK.ru: only the site's tab claims a language) next, English files last."""
    streams=outcome['streams']
    if streams and not streams[0].get('sub_known'): return 1
    return _LANG_RANK.get(candidate.get('lang') or '',1)


def _with_language(stream,language):
    """Menu label "<language> · <quality>": several files of one provider (TR/EN subtitle) must be tellable apart.
    ``provider · `` is put in front by :func:`streams`; the API field names stay the same. ``language`` is what
    :func:`tracks.label_language` lets through: none for a provider whose subtitle state is unknown."""
    if not language: return stream
    return {**stream,'label':language+' · '+str(stream.get('label') or stream.get('quality') or 'Otomatik')}


def _timed_out(candidate,why,ms):
    return {'label':candidate.get('label') or '','provider':'','streams':[],'duration':0,'error':'zaman aşımı: '+why,
            'events':[],'ms':ms,'timed_out':True,'lang':candidate.get('lang') or '',
            'resolver_type':candidate.get('resolver_type') or ''}


def _cfg_kw(cfg):
    """``cfg=`` for the site_extractors dispatcher only when the site has a yaml ``resolvers:`` list (it otherwise looks
    the same config up itself), so sites without one go through the unchanged 4-argument call."""
    return {'cfg':cfg} if cfg.resolvers else {}


def _json_api_stamp(cfg):
    """Route a candidate that did not come from a resolver (detail-field fallback, cached trailer) to the site's first
    ``json_api`` resolver, which turns an embed URL into streams."""
    for index,item in enumerate(cfg.resolvers):
        if item.get('type')=='json_api': return {'resolver':index,'resolver_type':'json_api'}
    return {}


def _resolve_candidate(row,cfg,raw,locator,frame_pages,load_cookies,use_neg):
    """One provider candidate, start to finish. Never raises: failures come back as ``error``."""
    from ..scraper import fetch,resolve,site_extractors
    from ..scraper.providers import resolve as resolve_provider,trace
    out={'label':raw.get('label') or '','provider':'','streams':[],'subtitles':[],'duration':0,'error':'','events':[],'ms':0,
         'lang':raw.get('lang') or '','resolver_type':raw.get('resolver_type') or ''}
    started=time.monotonic()
    trace.begin()
    key=_candidate_key(raw)
    try:
        cached_error=_neg_get(_neg_candidates,key) if use_neg else None
        if cached_error: raise ValueError('kısa süre önce başarısız oldu: '+cached_error)
        candidate=site_extractors.resolve_candidate(row['source'],raw,locator,load_cookies,**_cfg_kw(cfg))
        if not candidate: raise ValueError('sağlayıcı yönlendirmesi çözülemedi')
        candidate_url=urljoin(locator,candidate.get('url') or '')
        if not _url(candidate_url): raise ValueError('geçersiz sağlayıcı adresi')
        out['resolver_type']=out['resolver_type'] or candidate.get('resolver_type') or ''
        if isinstance(candidate.get('stream'),dict) and candidate['stream']:
            resolved=candidate['stream']   # json_api resolver: the type already produced the media, no provider step
        else:
            resolved=resolve_provider(
                candidate_url,referer=locator,
                load_handoff=lambda url: frame_pages.get(url) or fetch.page(cfg,url),allowed=cfg.providers)
            if not resolved and cfg.stream_resolver:
                resolved=resolve.resolve_stream(cfg,candidate_url)
        if not resolved or not resolved.get('streams'): raise ValueError('sağlayıcı akış vermedi')
        provider=resolved.get('provider') or candidate.get('label') or raw.get('label')
        out['provider']=provider or ''
        out['duration']=resolved.get('duration',0)
        out['cache_ttl']=streamlife.provider_ttl(resolved.get('cache_ttl'))   # the provider's own "reuse for N seconds" (recipe/resolver ``cache_ttl``)
        found=[{**s,'url':_url(s.get('url')),'provider':provider} for s in resolved['streams'] if _url(s.get('url'))]
        if not found: raise ValueError('sağlayıcı geçerli akış vermedi')
        # One provider file = one variant: its soft subtitle tracks and what the page says about its languages.
        soft=[t for t in (resolved.get('subtitles') or []) if isinstance(t,dict) and _url(t.get('url'))]
        variant=tracks.variant_id(row['id'],str(resolved.get('variant') or ''),found[0]['url'])
        fields={**tracks.describe(provider or '',raw.get('lang'),raw.get('language'),soft),'variant_id':variant}
        language=tracks.label_language(fields,raw.get('language'))
        out['streams']=[{**_with_language(s,language),**fields} for s in found]
        out['subtitles']=[{**t,'variant_id':variant} for t in soft]
    except Exception as exc:
        out['error']=trace.short(exc)
        out['streams']=[];out['subtitles']=[]
    out['events']=trace.take()
    out['ms']=int((time.monotonic()-started)*1000)
    if out['error'] and not out['error'].startswith('ValueError: kısa süre önce'):
        _neg_set(_neg_candidates,key,out['error'])
    return out


def _candidate_timeout(candidate):
    """The candidate's own time budget in seconds (0 = none): a positive number under ``timeout``, anything else ignored."""
    value=candidate.get('timeout') if isinstance(candidate,dict) else None
    return float(value) if isinstance(value,(int,float)) and not isinstance(value,bool) and value>0 else 0.0


def live_limits(candidates):
    """``(per-candidate limits, whole-source limit)`` in seconds of live playback. A candidate may carry its own time
    budget (``timeout``: player_page with ``fetch: browser``, a browser session takes 10+ s): its limit is the larger of
    that and ``RESOLVE_CANDIDATE_TIMEOUT``, and the whole-source limit grows to the longest such limit + 2 s. Candidates
    without one (or with one not above the live limit) keep exactly ``RESOLVE_CANDIDATE_TIMEOUT`` / ``RESOLVE_TOTAL_TIMEOUT``."""
    per=app_config.RESOLVE_CANDIDATE_TIMEOUT;total=app_config.RESOLVE_TOTAL_TIMEOUT
    pers=[max(per,_candidate_timeout(c)) for c in candidates]
    longer=[p for p in pers if p>per]
    if longer: total=max(total,max(longer)+2)
    return pers,total


def _run_candidates(candidates,run_one):
    """Resolve candidates with bounded parallelism, a per-candidate and a total time limit, and a short
    grace period for the others once one produced streams. Returns outcomes in candidate order."""
    n=len(candidates)
    outcomes=[None]*n
    grace=app_config.RESOLVE_GRACE
    pers,total=live_limits(candidates)
    starts={}
    def job(i):
        starts[i]=time.monotonic()
        return run_one(candidates[i])
    t0=time.monotonic()
    pool=ThreadPoolExecutor(max_workers=max(1,min(app_config.RESOLVE_PARALLEL,n)),thread_name_prefix='resolve')
    futures={pool.submit(job,i):i for i in range(n)}
    pending=set(futures);first_ok=None
    try:
        while pending:
            now=time.monotonic()
            hard=t0+total
            if first_ok is not None: hard=min(hard,first_ok+grace)
            soft=min([starts[futures[f]]+pers[futures[f]] for f in pending if futures[f] in starts] or [hard])
            if any(futures[f] not in starts for f in pending): soft=min(soft,now+0.05)  # queued/just-submitted: look again soon
            done,pending=wait(pending,timeout=max(0.0,min(hard,soft)-now),return_when=FIRST_COMPLETED)
            for f in done:
                try: outcomes[futures[f]]=f.result()
                except Exception as exc:
                    outcomes[futures[f]]={'label':'','provider':'','streams':[],'duration':0,'error':str(exc)[:160],'events':[],'ms':0}
                if outcomes[futures[f]]['streams'] and first_ok is None: first_ok=time.monotonic()
            now=time.monotonic()
            if now>=min(hard,t0+total): break
            expired={f for f in pending if futures[f] in starts and starts[futures[f]]+pers[futures[f]]<=now}
            for f in expired:
                outcomes[futures[f]]=_timed_out(candidates[futures[f]],f'aday süresi ({pers[futures[f]]:g} sn) doldu',int((now-starts[futures[f]])*1000))
            pending-=expired
    finally:
        now=time.monotonic()
        # Say WHY it was left out: a slow hand-off cut by the grace period looks like a broken provider otherwise.
        why=(f'ilk akıştan sonra {grace:g} sn bekleme süresi doldu' if first_ok is not None and now>=first_ok+grace-0.05
             else f'toplam süre ({total:g} sn) doldu')
        for f in pending:  # still running when we stopped waiting: abandon (its own HTTP timeouts end it)
            i=futures[f]
            outcomes[i]=_timed_out(candidates[i],why,int((now-starts.get(i,t0))*1000))
        pool.shutdown(wait=False,cancel_futures=True)
    return outcomes


def _record(row,outcomes,started,page_ms,error='',valid_in=None,blocked=False):
    """Explain this resolution to the admin dashboard (``state.last_resolver``); never raises. ``valid_in`` = seconds the
    result is going to be reused (``from_cache`` false: this was a real resolution; admin only, /api/streams is unchanged)."""
    try:
        from ..scraper import state
        cands=[]
        for o in outcomes:
            if not o: continue
            has=bool(o['streams'])
            ev=[e for e in o['events'] if e['ok']==has] or o['events']
            last=ev[-1] if ev else {}
            err=''
            if not has:
                err=last.get('error') or o['error'] or 'akış yok'
                if last.get('ok') and not last.get('error'):  # a stage said ok but no playable stream came out: never "ok"
                    err=f"{last.get('stage') or 'aşama'} başarılı görünüyor ama akış yok"+(f" ({o['error']})" if o['error'] else '')
            cands.append({'label':o['label'],'provider':o['provider'],'ok':has,'ms':o['ms'],
                          'stage':last.get('stage',''),'host':last.get('host',''),'error':err[:160],
                          **({'lang':o['lang']} if o.get('lang') else {}),
                          **({'resolver_type':o['resolver_type']} if o.get('resolver_type') else {})})
        streams=sum(len(o['streams']) for o in outcomes if o)
        if not error and not streams:
            error=next((c['error'] for c in cands if c['error']),'') or 'Video sağlayıcısı çözülemedi'
        event={'ok':bool(streams) and not error,'source_id':row['id'],'kind':row['kind'],
            'episode_id':row['episode_id'] or '','ms':int((time.monotonic()-started)*1000),'page_ms':page_ms,
            'streams':streams,'error':error[:200],'candidates':cands[:8],'from_cache':False}
        if valid_in is not None: event['valid_in']=valid_in
        if blocked: event['blocked']=True   # content that is not public: not a failure (playheal ignores it)
        state.record_resolver(row['source'],event)
    except Exception:
        return
    _playheal(row,event)


def _playheal(row,event):
    """Playback-triggered heal (scraper/playheal.py): the result goes into the site's window and a failure checks the
    trigger. A few dict walks, the heal itself runs in its own thread; never raises, never delays the resolution."""
    try:
        if event.get('blocked'): return   # a blocked placeholder is no resolution failure: neither the window nor the trigger
        from ..scraper import playheal
        get=lambda k:row[k] if k in row.keys() else ''
        playheal.record(row['source'],{**event,'locator':get('locator'),'resolver':get('resolver') or 'page','status':get('status')})
        if not event['ok']: playheal.maybe_trigger(row['source'])
    except Exception:
        pass


def _mark_blocked(row,reason):
    """Remember that the page of this source is a "not public" placeholder: ``status='blocked'`` (not suspect / broken, so it is
    outside the K/T counters, the playheal window and the repair step of the source finder, and no stream is offered for it) and
    a ``blocked_pages`` verdict (the next scan does not write the episode again before it is judged again). Never raises."""
    try:
        from . import gate
        now=int(time.time())
        db.execute("UPDATE video_sources SET status='blocked',failures=0,last_error=?,last_checked_at=?,resolved_payload=NULL,resolved_at=NULL "
                   "WHERE id=? AND status!='disabled'",(BLOCKED_PREFIX+reason,now,row['id']))
        gate.record(row['source'],row['locator'],gate.BLOCKED,reason,'rule',kind='movie' if row['kind']=='movie' else 'episode',
                    key=row['source_key'],now=now)
    except Exception: pass


def _check_blocked(row,cfg,html,rendered,started,page_ms):
    """A ``blocked:`` rule (``on: episode_page``) that matches the playback page ends the resolution with :class:`Blocked`."""
    if row['kind']=='trailer': return
    from . import gate
    from ..scraper import blocked as sblocked
    rules=gate.episode_rules(cfg) if getattr(cfg,'blocked',None) else []
    if not rules: return
    hit=sblocked.match(html,rules,'episode_page') or (sblocked.match(rendered,rules,'episode_page') if rendered and rendered!=html else None)
    if not hit: return
    _mark_blocked(row,hit['reason'])
    _record(row,[],started,page_ms,BLOCKED_PREFIX+hit['reason'],blocked=True)
    raise Blocked(BLOCKED_PREFIX+hit['reason'])


def _resolve_page(row,force):
    from ..scraper import config,fetch,parse,site_extractors
    locator=row['locator']
    cfg=config.load_site(row['source'])
    source=db.query_one('SELECT * FROM source_items WHERE source=? AND source_key=?',(row['source'],row['source_key']))
    # Reuse legacy trailer caches during migration, except after an error.
    legacy = source and source['resolved_at'] and time.time()-source['resolved_at']<TTL and source['resolved_streams']
    if not force and row['failures']==0 and row['kind']=='trailer' and legacy:
        return {'streams':json.loads(source['resolved_streams']),'duration':source['resolved_duration'] or 0}
    started=time.monotonic();page_ms=0
    media=(source['trailer_url'] if source and row['kind']=='trailer' and not force else None)
    candidates=[]
    frame_pages={}
    if not media:
        try:
            bundle=fetch.page_bundle(cfg,locator)
        except Exception as exc:
            _record(row,[],started,int((time.monotonic()-started)*1000),'sayfa: '+str(exc)[:150])
            raise
        page_ms=int((time.monotonic()-started)*1000)
        html=bundle.get('initial_html') or bundle['html']
        _check_blocked(row,cfg,html,bundle.get('html'),started,page_ms)
        candidates=site_extractors.discover(row['source'],html,locator,**_cfg_kw(cfg))
        frame_pages={frame.get('url'):frame.get('html','') for frame in bundle.get('frames',[])
                     if _url(frame.get('url'))}
        frame_pages.update({page.get('url'):page.get('html','')
                            for page in bundle.get('network_pages',[])
                            if _url(page.get('url')) and page.get('html')})
        known={urljoin(locator,c.get('url') or '') for c in candidates}
        for frame_url in frame_pages:
            if frame_url not in known:
                candidates.insert(0,{'url':frame_url,'label':'Tarayıcı oynatıcısı'})
        if not candidates and cfg.detail_fields:
            parsed=parse.parse_detail(html,cfg.detail_fields)
            candidate=parsed.get('trailer_url') if row['kind']=='trailer' else parsed.get('video_url')
            candidates=[{'url':candidate,**_json_api_stamp(cfg)}] if candidate else []
    else:
        candidates=[{'url':media,**_json_api_stamp(cfg)}]
    if not candidates:
        _record(row,[],started,page_ms,'Sayfada video sağlayıcısı bulunamadı')
        raise ValueError('Sayfada video sağlayıcısı bulunamadı')
    cookie_lock=threading.Lock();cookie_state={}

    def load_cookies():
        with cookie_lock:  # one browser session for all candidates
            if 'jar' not in cookie_state and 'error' not in cookie_state:
                try: cookie_state['jar']=fetch.session_cookies(cfg,locator)
                except Exception as exc: cookie_state['error']=exc
            if 'error' in cookie_state: raise cookie_state['error']
            return cookie_state['jar']

    outcomes=_run_candidates(candidates,lambda raw:_resolve_candidate(row,cfg,raw,locator,frame_pages,load_cookies,not force))
    order=list(range(len(outcomes)))
    fast=app_config.RESOLVE_FAST_FIRST  # candidates that answered clearly faster go first (0.5 s buckets keep page order otherwise)
    order.sort(key=lambda i:(_rank(candidates[i],outcomes[i]),   # measured Turkish-subtitle files, then unknown (OK.ru), then English
                             (0 if outcomes[i]['streams'] else 1,outcomes[i]['ms']//500 if outcomes[i]['streams'] else 0) if fast else (0,0),i))
    result={'streams':[],'subtitles':[],'duration':0,'resolver_version':RESOLVER_VERSION}
    seen_streams=set();seen_subs=set()
    for i in order:
        outcome=outcomes[i]
        for stream in outcome['streams']:
            identity=tracks.file_identity(stream)   # the same file re-signed by a second candidate is one stream
            if identity in seen_streams: continue
            seen_streams.add(identity)
            result['streams'].append(stream)
        for sub in outcome.get('subtitles') or []:
            identity=(sub['url'],sub.get('variant_id'))
            if identity in seen_subs: continue
            seen_subs.add(identity)
            result['subtitles'].append(sub)
        result['duration']=result['duration'] or outcome['duration']
    tracks.share_audio(result['streams'])
    result['streams']=tracks.link_mirrors(result['streams'])   # a spare copy of a stream (other CDN host/path) follows it
    ttls=[o['cache_ttl'] for o in outcomes if o and o.get('streams') and o.get('cache_ttl')]
    if ttls: result['cache_ttl']=min(ttls)   # the shortest reuse time any contributing provider asked for
    until=_stamp_validity(result)
    _record(row,outcomes,started,page_ms,valid_in=max(0,int(until-time.time())) if result['streams'] else None)
    if not result['streams']:
        raise ValueError('Video sağlayıcısı çözülemedi')
    return result


def _stamp_validity(result):
    """``result['valid_until']`` (epoch seconds): until when this payload may be reused (``streamlife.valid_until``: the
    earliest expiry the stream URLs announce minus ``RESOLVE_CACHE_MARGIN``, a provider ``cache_ttl``, else
    ``RESOLVE_CACHE_TTL``). Returns it. Only a field is added to the stored payload, the answer to the client is unchanged."""
    until=int(streamlife.valid_until(result.get('streams') or [],time.time(),result.get('cache_ttl')))
    result['valid_until']=until
    return until


def _cached(row):
    """``(payload, valid_until, legacy)`` of the stored resolution when it may still be reused, else None. A payload
    stamped with ``valid_until`` is reused until then (never longer than ``RESOLVE_CACHE_MAX_TTL`` after it was resolved);
    an older one without the stamp keeps the old rule (``resolved_at + RESOLVE_CACHE_TTL``, ``legacy``)."""
    ttl=app_config.RESOLVE_CACHE_TTL
    if ttl<=0 or not row['resolved_payload'] or not row['resolved_at']: return None
    try: cached=json.loads(row['resolved_payload'])
    except ValueError: return None
    if not isinstance(cached,dict): return None
    if row['resolver']=='page' and cached.get('resolver_version')!=RESOLVER_VERSION: return None
    until=cached.get('valid_until')
    legacy=isinstance(until,bool) or not isinstance(until,(int,float))
    if legacy: until=row['resolved_at']+ttl
    else: until=min(until,row['resolved_at']+max(ttl,app_config.RESOLVE_CACHE_MAX_TTL))
    if time.time()>=until: return None
    return cached,float(until),legacy


def _start_background(name,fn):
    """Run ``fn`` in a daemon thread (tests replace this to run it inline or to collect it)."""
    threading.Thread(target=fn,daemon=True,name=name).start()


def _refresh_async(row):
    """Resolve ``row`` again in the background and store the fresh payload (refresh-ahead: the answer that is being given
    right now still comes from the cache). One refresh per source at a time; a source whose resolution (or earlier
    refresh) failed a moment ago (``RESOLVE_NEG_TTL``) is left alone; errors are swallowed. Returns whether a refresh started."""
    sid=row['id']
    if _neg_get(_neg_sources,sid) or _neg_get(_refresh_backoff,sid): return False
    with _neg_lock:
        if sid in _refreshing: return False
        _refreshing.add(sid)
    def run():
        try: _resolve_and_store(row,False,background=True)
        except Exception as exc: _neg_set(_refresh_backoff,sid,exc)
        finally:
            with _neg_lock: _refreshing.discard(sid)
    try: _start_background('resolve-refresh',run)
    except Exception:
        with _neg_lock: _refreshing.discard(sid)
        return False
    return True


def _record_cache_hit(row,cached,valid_in):
    """Admin side of a cache hit (``from_cache`` true, ``valid_in`` seconds left). A failure of the site stays visible: only
    an absent or successful ``last_resolver`` is replaced. Never raises."""
    try:
        from ..scraper import state
        last=(state.get_site_state(row['source']) or {}).get('last_resolver')
        if last and not last.get('ok'): return
        state.record_resolver(row['source'],{'ok':True,'source_id':row['id'],'kind':row['kind'],'episode_id':row['episode_id'] or '',
            'ms':0,'page_ms':0,'streams':len(cached.get('streams') or []),'error':'','candidates':[],
            'from_cache':True,'valid_in':valid_in})
    except Exception:
        pass


def resolve_source(row, force=False):
    """Resolve only this provider, never the first arbitrary source of a title.

    A stored resolution that is still valid (``_cached``) is returned as it is, without any request; one that runs out
    within ``RESOLVE_REFRESH_AHEAD`` seconds is returned too and re-resolved in the background. Anything else (expired,
    dropped after a playback error / retry, ``force``) is resolved now."""
    if not force:
        hit=_cached(row)
        if hit:
            cached,until,legacy=hit
            left=until-time.time()
            if row['resolver']=='page': _record_cache_hit(row,cached,max(0,int(left)))
            if not legacy and left<app_config.RESOLVE_REFRESH_AHEAD: _refresh_async(row)
            return cached
    return _resolve_and_store(row,force)


def _resolve_and_store(row, force, background=False):
    """A real resolution of ``row`` and its storage in ``resolved_payload``. ``background`` (refresh-ahead): the source's
    health columns stay as they are (a playback report in flight compares ``last_checked_at``) and a failure is not
    remembered in the negative cache; the payload is only written while the locator is still the one that was resolved."""
    locator=row['locator']
    if row['resolver'] in ('direct','embed'):
        result={'streams':[{'url':locator,'type':'embed' if row['resolver']=='embed' else row['media_type'],
                            'quality':'auto','label':row['label']}], 'duration':0}
    else:
        cached_error=None if force else _neg_get(_neg_sources,row['id'])
        if cached_error: raise (Blocked if cached_error.startswith(BLOCKED_PREFIX) else ValueError)(cached_error)
        try:
            result=_resolve_page(row,force)
        except Exception as exc:
            if not background: _neg_set(_neg_sources,row['id'],exc)
            raise
    result['streams']=[s for s in result.get('streams',[]) if _url(s.get('url'))]
    if not result['streams']: raise ValueError('Oynatılabilir adres bulunamadı')
    with _neg_lock: _neg_sources.pop(row['id'],None)
    if 'valid_until' not in result: _stamp_validity(result)   # direct / embed / legacy trailer cache (page resolutions are stamped already)
    now=int(time.time())
    if background:
        db.execute('UPDATE video_sources SET resolved_payload=?,resolved_at=? WHERE id=? AND locator=?',
                   (json.dumps(result),now,row['id'],locator))
        return result
    db.execute('''UPDATE video_sources SET resolved_payload=?,resolved_at=?,last_checked_at=?,
        status=CASE WHEN failures=0 AND status='suspect' THEN 'unknown' ELSE status END,
        last_error=CASE WHEN failures=0 THEN NULL ELSE last_error END WHERE id=?''',
        (json.dumps(result),now,now,row['id']))
    return result


def _invalidate(sid):
    """Drop the stored resolution of ONE source: the next ``resolve_source`` resolves it fresh (same columns the failure
    paths clear; health columns and the other sources of the episode are untouched)."""
    db.execute('UPDATE video_sources SET resolved_payload=NULL,resolved_at=NULL WHERE id=?',(sid,))


def _prune_attempts():
    """Expired attempt tokens are dropped at most every 10 minutes, not on every stream request."""
    global _last_prune
    now=time.time()
    if now-_last_prune<600: return
    _last_prune=now
    db.execute('DELETE FROM playback_attempts WHERE created_at<?',(int(now)-86400,))


def _resolve_rows(chosen):
    """[(row, result, error)] in ``chosen`` order; the sources of one episode/film resolve concurrently."""
    def one(row):
        try: return row,resolve_source(row),None
        except Exception as exc: return row,None,exc
    workers=min(app_config.RESOLVE_SOURCES_PARALLEL,len(chosen))
    if workers<=1: return [one(row) for row in chosen]
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='resolve-src') as pool:
        return list(pool.map(one,chosen))


def _finder_state(cid, episode_id, profile_id):
    """Source-finder hook of ``streams``: a full-video request that produced no stream asks for a background search
    (library/sourcefinder.py, never delays the answer, never raises). ``{'state': 'searching'|'not_found'}`` for the optional
    ``finder`` field of the answer, else None (finder off / not started / a job of this episode found something recently)."""
    try:
        from . import sourcefinder
        res=sourcefinder.request(cid,episode_id or '',profile_id or '',trigger='play')
        return {'state':res['state']} if res.get('state') in ('searching','not_found') else None
    except Exception:
        return None


def streams(cid, episode_id=None, kind=None, profile_id=''):
    # Full videos and trailers are deliberately never mixed in one playlist.
    rows=db.query("SELECT * FROM video_sources WHERE canonical_id=? AND episode_id=? AND status NOT IN ('disabled','broken','blocked') ORDER BY CASE status WHEN 'healthy' THEN 0 WHEN 'unknown' THEN 1 ELSE 2 END,source,id",(cid,episode_id or ''))
    has_full=db.query_one("SELECT 1 FROM video_sources WHERE canonical_id=? AND episode_id=? AND kind!='trailer'",(cid,episode_id or ''))
    chosen=[r for r in rows if r['kind']!='trailer'] if has_full else [r for r in rows if r['kind']=='trailer']
    if kind == "trailer": chosen=[r for r in rows if r["kind"]=="trailer"]
    elif kind == "video": chosen=[r for r in rows if r["kind"]!="trailer"]
    # Prefer Sinemalar trailers, keeping other providers as fallbacks.
    # This never changes full-film / episode source ordering.
    if chosen and all(r['kind']=='trailer' for r in chosen):
        chosen.sort(key=lambda r: r['source'] != 'sinemalar')
    from . import trailer_check
    chosen=trailer_check.filter_alive(chosen)  # a trailer whose YouTube video is gone/private/non-embeddable is not offered
    output=[];duration=0;labels={};soft=[];spares={}
    for row,result,exc in _resolve_rows(chosen):
        if exc is not None:
            if is_blocked(exc):   # a not-public placeholder is not a failing source: it is marked ``blocked``, never suspect / broken
                db.execute("UPDATE video_sources SET status='blocked',failures=0,last_error=?,last_checked_at=?,resolved_payload=NULL,resolved_at=NULL WHERE id=? AND status!='disabled'",(str(exc)[:250],int(time.time()),row['id']))
                continue
            db.execute("UPDATE video_sources SET status='suspect',last_error=?,last_checked_at=?,resolved_payload=NULL,resolved_at=NULL WHERE id=? AND status!='disabled'",(str(exc)[:250],int(time.time()),row['id']))
            continue
        token=uuid.uuid4().hex
        db.execute('INSERT INTO playback_attempts(token,source_id,created_at) VALUES (?,?,?)',(token,row['id'],int(time.time())))
        for stream in result['streams']:
            provider=stream.get('provider') or row['source']
            label=provider+' · '+str(stream.get('label') or stream.get('quality') or 'Otomatik')
            if stream.get('mirror_of'):  # spare copy of the stream above it: "… · yedek", "… · yedek 2"
                spares[stream['mirror_of']]=spares.get(stream['mirror_of'],0)+1
                label+=' · yedek'+(f" {spares[stream['mirror_of']]}" if spares[stream['mirror_of']]>1 else '')
            labels[label]=labels.get(label,0)+1
            if labels[label]>1: label+=f' ({labels[label]})'  # two rows must never read the same in the player menu
            output.append({**tracks.with_defaults(stream,row['id']),'source_id':row['id'],'source':row['source'],
                           'kind':row['kind'],'attempt_token':token,'label':label})
        soft.extend(t for t in (result.get('subtitles') or []) if isinstance(t,dict) and t.get('url'))
        duration=duration or result.get('duration',0)
    _prune_attempts()
    payload={'streams':output,'subtitles':tracks.public_subtitles(soft),'audio':tracks.public_audio(output),'duration':duration}
    if kind!='trailer' and not output:   # nothing playable (no source row, every source failed or is broken): look for one in the background
        finder=_finder_state(cid,episode_id,profile_id)
        if finder: payload['finder']=finder
    return payload


def _stamped(row):
    """Whether the stored payload carries ``valid_until`` (the link-lifetime rule) instead of the old fixed TTL."""
    try: return 'valid_until' in json.loads(row['resolved_payload'])
    except (TypeError,ValueError): return False


def prefetch(cid, episode_id=None):
    """RESOLVE_PREFETCH: warm the cache of the most likely page-backed source in the background (detail page opened).
    Fire and forget; the result lands in ``resolved_payload`` like any resolution. A payload that is still valid is not
    resolved again, unless it runs out within ``RESOLVE_REFRESH_AHEAD`` seconds (then only the refresh starts). Returns
    whether a background job started."""
    if not app_config.RESOLVE_PREFETCH: return False
    row=db.query_one("SELECT * FROM video_sources WHERE canonical_id=? AND episode_id=? AND kind!='trailer' AND resolver='page' AND status NOT IN ('disabled','broken','blocked') ORDER BY CASE status WHEN 'healthy' THEN 0 WHEN 'unknown' THEN 1 ELSE 2 END,source,id LIMIT 1",(cid,episode_id or ''))
    if not row: return False
    hit=_cached(row)
    if hit:
        _cached_payload,until,legacy=hit
        return not legacy and until-time.time()<app_config.RESOLVE_REFRESH_AHEAD and _refresh_async(row)
    if row['resolved_payload'] and row['resolved_at'] and not _stamped(row) \
            and time.time()-row['resolved_at']<app_config.RESOLVE_CACHE_TTL: return False   # an old-style payload, fresh enough
    with _neg_lock:
        if row['id'] in _prefetching: return False
        _prefetching.add(row['id'])
    def run():
        try: resolve_source(row)
        except Exception: pass
        finally:
            with _neg_lock: _prefetching.discard(row['id'])
    _start_background('resolve-prefetch',run)
    return True


DEVICE_CODES=('unsupported','decode','autoplay','aborted','offline')  # device-side: never counted
KEEP_CACHE_CODES=('autoplay','aborted','offline')  # device-side AND unrelated to the link: the stored resolution stays; any other failure drops it


def feedback(token, event, code='', engine='', detail='', user_agent=''):
    """One health update per provider/attempt; tokens expire and bind the source.

    Every stream of a source shares one token and the client falls through to the next stream after a
    failure, so a success arriving after a counted failure of the SAME attempt takes that failure back
    (the source does play). Only this attempt's own contribution is undone, and only while no newer
    attempt has reported a failure; an old attempt never erases a newer attempt's failure.

    A failure of a source whose streams are all HLS, reported by the browser's own player (``engine`` html5, ``user_agent`` a
    desktop Chrome / Firefox / Edge) is a client capability, not a fault of the source: it is not counted and the stored link
    stays (library/streamdiag.py). Any failure that is about the stream (everything but aborted / offline / autoplay) also
    queues a background probe of the source's streams after the transaction (``detail`` = the client's own error text,
    <= 120 characters, ends up in the source's ``last_diag`` note); the answer is never delayed by it."""
    result,job=_feedback(token,event,code,engine,detail,user_agent)
    if job:
        try: streamdiag.schedule(job)
        except Exception: pass
    return result


def _feedback(token, event, code, engine, detail, user_agent):
    """``(answer, diagnosis job or None)`` of :func:`feedback`."""
    now=int(time.time())
    job=None
    with closing(db.connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        attempt=conn.execute('SELECT * FROM playback_attempts WHERE token=?',(token,)).fetchone()
        if not attempt or now-attempt['created_at']>86400: raise ValueError('Oynatma denemesi bulunamadı veya süresi doldu')
        success=event=='success'
        if attempt['success_at' if success else 'failure_at']: return {'ok':True,'duplicate':True},None
        if success:  # keep the failure's error_code; only note the engine that finally played
            conn.execute("UPDATE playback_attempts SET success_at=?,engine=COALESCE(NULLIF(?,''),engine) WHERE token=?",(now,engine[:30],token))
        else:
            conn.execute('UPDATE playback_attempts SET failure_at=?,error_code=?,engine=? WHERE token=?',(now,code[:80],engine[:30],token))
        source=conn.execute('SELECT * FROM video_sources WHERE id=?',(attempt['source_id'],)).fetchone()
        if not source or source['status']=='disabled': return {'ok':True},None
        if success:
            own=bool(attempt['failure_counted'])
            # Our own counted failure moved last_checked_at to failure_at; anything later is someone else's.
            baseline=max(attempt['created_at'],attempt['failure_at'] or 0) if own else attempt['created_at']
            newer=(source['last_checked_at'] or 0)>baseline or conn.execute(
                'SELECT 1 FROM playback_attempts WHERE source_id=? AND token!=? AND failure_counted=1 AND created_at>?',
                (source['id'],token,attempt['created_at'])).fetchone()
            if own: conn.execute('UPDATE playback_attempts SET failure_counted=0 WHERE token=?',(token,))
            if not newer:
                conn.execute("UPDATE video_sources SET status='healthy',failures=0,last_error=NULL,last_success_at=?,last_checked_at=? WHERE id=?",(now,now,source['id']))
            elif own:  # a newer failure stands: only take back what this attempt added
                failures=max(0,source['failures']-1)
                status='broken' if failures>=3 else 'suspect' if failures>=1 else source['status']
                conn.execute('UPDATE video_sources SET failures=?,status=? WHERE id=?',(failures,status,source['id']))
            return {'ok':True},None
        # the stored streams are read BEFORE a failure drops the resolution: the diagnosis probes them
        streams=streamdiag.payload_streams(source['resolved_payload'])
        browser_hls=streamdiag.hls_in_browser(streams,source['media_type'],engine,user_agent)
        if browser_hls:  # the browser cannot play HLS: not this source's fault, the stored link is fine
            streamdiag.write_browser_diag(conn,source,detail)
        elif code not in DEVICE_CODES:
            failures=source['failures']+1
            conn.execute('''UPDATE video_sources SET status=?,failures=?,last_error=?,last_checked_at=?,
                resolved_payload=NULL,resolved_at=NULL WHERE id=?''',
                ('broken' if failures>=3 else 'suspect',failures,code or 'playback_failed',now,source['id']))
            conn.execute('UPDATE playback_attempts SET failure_counted=1 WHERE token=?',(token,))
        elif code not in KEEP_CACHE_CODES:  # not counted against the source's health, but the stored link may be what broke: resolve it fresh next time
            conn.execute('UPDATE video_sources SET resolved_payload=NULL,resolved_at=NULL WHERE id=?',(source['id'],))
        job=streamdiag.build_job(source,streams,code,engine,detail,browser_hls)
        return {'ok':True},job


def retry(sid):
    row=db.query_one('SELECT * FROM video_sources WHERE id=?',(sid,))
    if not row: raise ValueError('Kaynak bulunamadı')
    if row['kind']=='trailer':  # a manual retry also asks YouTube again on the next detail view
        from . import trailer_check
        trailer_check.forget(trailer_check.youtube_id(row['locator']))
    _invalidate(sid)   # whatever happens next, the stored link is not trusted any more
    result=resolve_source(row,force=True)
    # A successful resolve is not proof of video playback.
    db.execute("UPDATE video_sources SET status='unknown',failures=0,last_error=NULL,last_checked_at=? WHERE id=?",(int(time.time()),sid))
    return {'ok':True,'streams':len(result['streams']),'status':'unknown'}
