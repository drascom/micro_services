"""Request-local catalogue filters. Shared cache objects are never modified."""
from copy import copy
from .errors import bad_request
from .scraper import config


def sources(snap):
    result = []
    for sid in config.list_sites():
        cfg = config.load_site(sid)
        result.append({"id": sid, "name": cfg.data.get("display_name", sid),
                       "count": sum(sid in i.get("sources", []) for i in snap.items)})
    return result


def select(snap, source=""):
    if not source:
        return snap
    if source not in config.list_sites():
        raise bad_request("Unknown catalogue source")
    view = copy(snap)
    view.items = [i for i in snap.items if source in i.get("sources", [])]
    view.by_id = {i["id"]: i for i in view.items}
    keep = lambda items: [i for i in items if i["id"] in view.by_id]
    view.by_genre = {slug: keep(items) for slug, items in snap.by_genre.items()}
    view.newest = keep(snap.newest) or view.items
    view.top10 = sorted(view.items, key=lambda i: (-i.get("popularity", 0), i["id"]))[:10]
    view.upcoming = {**snap.upcoming, "items": keep(snap.upcoming["items"])} if snap.upcoming else None
    view.search_index = [(h, i) for h, i in snap.search_index if i["id"] in view.by_id]
    return view
