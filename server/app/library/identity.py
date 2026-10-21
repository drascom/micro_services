"""External identities win; uncertain title matches are queued for review."""
import hashlib
import json
import re
import time


def identifiers(norm):
    out = {}
    tmdb = norm.get('tmdb_id')
    if str(tmdb or '').isdigit() and int(tmdb) > 0:
        out['tmdb'] = str(int(tmdb))
    imdb = str(norm.get('imdb_id') or '')
    match = re.search(r'\b(tt\d{7,10})\b', imdb)
    if match:
        out['imdb'] = match.group(1)
    return out


def review(conn, site, key, cid, reason, candidates=()):
    conn.execute('''INSERT INTO identity_reviews(source,source_key,canonical_id,reason,candidates,updated_at)
        VALUES (?,?,?,?,?,?) ON CONFLICT(source,source_key) DO UPDATE SET
        canonical_id=excluded.canonical_id,reason=excluded.reason,candidates=excluded.candidates,
        status='pending',updated_at=excluded.updated_at''',
        (site,key,cid,reason,json.dumps(list(candidates)),int(time.time())))


def compatible(conn, cid, ids):
    known = {r['provider']:r['external_id'] for r in conn.execute('SELECT * FROM external_ids WHERE canonical_id=?',(cid,))}
    return all(provider not in known or known[provider] == value for provider,value in ids.items())


def choose(conn, site, norm, previous=None):
    from .ingest import canonical_id, slugify
    key, kind = norm['source_key'], norm.get('type','movie')
    ids = identifiers(norm)
    matches = set()
    for provider,value in ids.items():
        row = conn.execute('SELECT canonical_id FROM external_ids WHERE provider=? AND media_type=? AND external_id=?',(provider,kind,value)).fetchone()
        if row: matches.add(row['canonical_id'])
    old = previous['canonical_id'] if previous else None
    if old and len(matches)==1 and old not in matches:
        target=next(iter(matches))
        if compatible(conn,old,ids) and compatible(conn,target,ids):
            try:
                merge(conn,old,target)
                old=target
            except ValueError:
                pass
    conflict = len(matches)>1 or (old and matches and old not in matches)
    candidate = old or (next(iter(matches)) if len(matches)==1 else None)
    if candidate and not compatible(conn,candidate,ids): conflict=True
    if not candidate and not conflict:
        titles={slugify(norm.get(f) or '') for f in ('title','original_title')} - {'untitled'}
        year=norm.get('year')
        candidates=[]
        if year:
            for row in conn.execute('SELECT id,title,original_title FROM library_items WHERE type=? AND year=?',(kind,year)):
                if titles.intersection({slugify(row['title'] or ''),slugify(row['original_title'] or '')}) and compatible(conn,row['id'],ids):
                    candidates.append(row['id'])
        if len(candidates)==1: candidate=candidates[0]
        elif candidates: conflict=True;matches.update(candidates)
    if not candidate:
        candidate=canonical_id(norm, int(ids['tmdb']) if 'tmdb' in ids else None)
        # A name collision must never overwrite an unrelated/undated title.
        if conn.execute('SELECT 1 FROM library_items WHERE id=?',(candidate,)).fetchone() or conflict:
            candidate += '-' + hashlib.sha256((site+':'+key).encode()).hexdigest()[:10]
    if conflict:
        review(conn,site,key,candidate,'conflicting_identities',matches)
        # Preserve the source binding; do not attach conflicting external IDs.
        return candidate
    for provider,value in ids.items():
        conn.execute('INSERT OR IGNORE INTO external_ids VALUES (?,?,?,?)',(provider,kind,value,candidate))
    if not ids:
        review(conn,site,key,candidate,'missing_external_id')
    else:
        conn.execute("UPDATE identity_reviews SET status='resolved',updated_at=? WHERE source=? AND source_key=?",(int(time.time()),site,key))
    return candidate


def bind(conn, cid, tmdb_id=None, imdb_id=None):
    """Manual verified identity assignment. Conflicts require explicit review."""
    row=conn.execute('SELECT type FROM library_items WHERE id=?',(cid,)).fetchone()
    if not row: raise ValueError('Film bulunamadı')
    ids=identifiers({'tmdb_id':tmdb_id,'imdb_id':imdb_id})
    if not ids: raise ValueError('Geçerli bir TMDB veya IMDb kimliği gerekli')
    if not compatible(conn,cid,ids): raise ValueError('Kayıtta farklı bir kimlik var; önce eşleşmeyi inceleyin')
    owners=set()
    for provider,value in ids.items():
        owner=conn.execute('SELECT canonical_id FROM external_ids WHERE provider=? AND media_type=? AND external_id=?',(provider,row['type'],value)).fetchone()
        if owner: owners.add(owner['canonical_id'])
    if len(owners)>1: raise ValueError('TMDB ve IMDb farklı kayıtlara bağlı')
    if owners and cid not in owners:
        target=next(iter(owners))
        if not compatible(conn,target,ids): raise ValueError("Hedef kaydın kimlikleriyle çelişiyor")
        merge(conn,cid,target)
        cid=target
    for provider,value in ids.items():
        conn.execute('INSERT OR IGNORE INTO external_ids VALUES (?,?,?,?)',(provider,row['type'],value,cid))
        conn.execute('UPDATE library_items SET '+provider+'_id=? WHERE id=?',(value,cid))
    conn.execute("UPDATE identity_reviews SET status='resolved' WHERE canonical_id=?",(cid,))
    return cid


def merge(conn, source_id, target_id):
    """Merge confirmed identities atomically, preserving user data and old URLs."""
    if source_id == target_id: return
    old=conn.execute('SELECT * FROM library_items WHERE id=?',(source_id,)).fetchone()
    target=conn.execute('SELECT * FROM library_items WHERE id=?',(target_id,)).fetchone()
    if not old or not target or old['type']!=target['type']: raise ValueError('Aynı türde iki katalog kaydı gerekli')
    old_ids={r['provider']:r['external_id'] for r in conn.execute('SELECT * FROM external_ids WHERE canonical_id=?',(source_id,))}
    if not compatible(conn,target_id,old_ids): raise ValueError('Çelişen kimlikler birleştirilemez')
    ep_map={source_id:target_id}
    for row in conn.execute("SELECT episode_id,season,episode FROM video_sources WHERE canonical_id=? AND kind='episode'",(source_id,)):
        ep_map[row['episode_id']]=f"{target_id}:s{row['season']}:e{row['episode']}"
        ep_map[f"{source_id}:s{row['season']}"]=f"{target_id}:s{row['season']}"  # season artwork ids
    # Watch history also covers episodes without a video source (unscraped/withdrawn): map every progress
    # episode id by prefix, not only the ids the old identity's video_sources knew about.
    prefix=source_id+':'
    progress=conn.execute('SELECT * FROM progress WHERE item_id=?',(source_id,)).fetchall()
    for row in progress:
        old_ep=row['episode_id']
        if old_ep not in ep_map and old_ep.startswith(prefix):
            ep_map[old_ep]=target_id+':'+old_ep[len(prefix):]
            season=re.match(r's\d+(?=:|$)',old_ep[len(prefix):])
            if season: ep_map.setdefault(prefix+season.group(),target_id+':'+season.group())
    for alias,destination in ep_map.items():
        conn.execute('INSERT OR REPLACE INTO catalogue_aliases VALUES (?,?)',(alias,destination))
        conn.execute('UPDATE catalogue_aliases SET canonical_id=? WHERE canonical_id=?',(destination,alias))
    for row in progress:
        episode=ep_map.get(row['episode_id'],row['episode_id'])
        clash=conn.execute('SELECT updated_at FROM progress WHERE profile_id=? AND episode_id=?',(row['profile_id'],episode)).fetchone() \
            if episode!=row['episode_id'] else None
        if clash is None:  # free key: the row simply moves (nothing is deleted)
            conn.execute('UPDATE progress SET item_id=?,episode_id=? WHERE profile_id=? AND episode_id=?',
                         (target_id,episode,row['profile_id'],row['episode_id']))
            continue
        if row['updated_at']>clash['updated_at']:  # same (profile,episode) on both identities: newest wins
            conn.execute('UPDATE progress SET item_id=?,position=?,duration=?,watched=?,updated_at=? WHERE profile_id=? AND episode_id=?',
                         (target_id,row['position'],row['duration'],row['watched'],row['updated_at'],row['profile_id'],episode))
        conn.execute('DELETE FROM progress WHERE profile_id=? AND episode_id=?',(row['profile_id'],row['episode_id']))
    conn.execute('INSERT OR IGNORE INTO mylist SELECT profile_id,?,added_at FROM mylist WHERE item_id=?',(target_id,source_id))
    conn.execute('DELETE FROM mylist WHERE item_id=?',(source_id,))
    # "removed from Devam Et" follows the identity too; on a clash the later removal wins
    conn.execute('INSERT INTO continue_hidden SELECT profile_id,?,hidden_at FROM continue_hidden WHERE item_id=? '
                 'ON CONFLICT(profile_id,item_id) DO UPDATE SET hidden_at=MAX(hidden_at,excluded.hidden_at)',(target_id,source_id))
    conn.execute('DELETE FROM continue_hidden WHERE item_id=?',(source_id,))
    conn.execute('INSERT OR IGNORE INTO library_lists SELECT list_id,?,position FROM library_lists WHERE canonical_id=?',(target_id,source_id))
    conn.execute('DELETE FROM library_lists WHERE canonical_id=?',(source_id,))
    conn.execute('UPDATE source_items SET canonical_id=? WHERE canonical_id=?',(target_id,source_id))
    # TMDB season/episode metadata follows the identity (the target's own rows win)
    conn.execute('INSERT OR IGNORE INTO library_seasons SELECT ?,season,name,overview,air_date,tmdb_poster_url,status,checked_at '
                 'FROM library_seasons WHERE canonical_id=?',(target_id,source_id))
    conn.execute('DELETE FROM library_seasons WHERE canonical_id=?',(source_id,))
    conn.execute('INSERT OR IGNORE INTO library_episodes SELECT ?,season,episode,title,overview,air_date,runtime_minutes,'
                 'tmdb_still_url,checked_at FROM library_episodes WHERE canonical_id=?',(target_id,source_id))
    conn.execute('DELETE FROM library_episodes WHERE canonical_id=?',(source_id,))
    for before,after in ep_map.items():
        conn.execute('UPDATE video_sources SET episode_id=? WHERE episode_id=?',(after,before))
    conn.execute('UPDATE video_sources SET canonical_id=? WHERE canonical_id=?',(target_id,source_id))
    for provider,value in old_ids.items():
        conn.execute('DELETE FROM external_ids WHERE canonical_id=? AND provider=?',(source_id,provider))
        conn.execute('INSERT OR IGNORE INTO external_ids VALUES (?,?,?,?)',(provider,old['type'],value,target_id))
    conn.execute('UPDATE identity_reviews SET canonical_id=? WHERE canonical_id=?',(target_id,source_id))
    conn.execute('DELETE FROM field_provenance WHERE canonical_id=?',(source_id,))
    conn.execute('DELETE FROM library_items WHERE id=?',(source_id,))
    from .ingest import merge_canonical
    merge_canonical(conn,target_id)
    conn.execute('UPDATE library_items SET added_at=? WHERE id=?',(min(old['added_at'],target['added_at']),target_id))
