"""Site brief: the server's persistent memory of a site that heads every stage prompt (K6). Rebuilt from the DB on every
call (never stale): address, fetch mode, one line per finished stage, skipped fields, every admin hint given on this site
(with its stage), open engine gaps. At most ``MAX_BYTES``."""
from __future__ import annotations

from ..sitemap import store as map_store
from . import state

MAX_BYTES = 2048
HINT_CLIP = 200


def _fit(lines: list[str], limit: int) -> str:
    text = "\n".join(lines)
    while len(text.encode("utf-8")) > limit and len(lines) > 1:
        lines = lines[:-1]
        text = "\n".join(lines + ["…"])
    return text.encode("utf-8")[:limit].decode("utf-8", "ignore")


def brief(site_id: str) -> str:
    from .engine import STAGES   # the registry (engine imports the stage modules)
    site = map_store.get(site_id)
    if site is None:
        return ""
    try:
        draft = map_store.draft(site_id)
    except Exception:
        draft = {}
    mode = (draft.get("fetch") or {}).get("mode", "auto")
    head = [f"Site: {site['base_url']} (fetch: {mode}, status: {site['status']})"]
    rows = state.all_for(site_id)
    stage_lines, gaps = [], []
    for sid, stage in STAGES.items():
        row = rows.get(sid)
        if not row or row["state"] in ("pending", "running"):
            continue
        if row["state"] in state.PASSED:
            line = getattr(stage, "result_line", lambda p: "")(row.get("patch"))
            stage_lines.append(f"- {sid} {stage.title}: {row['state']} - {line}")
        elif row["state"] == "engine_gap":
            gaps.append(f"- {sid}: {((row.get('evidence') or {}).get('gap') or 'motor boşluğu')}")
        else:
            stage_lines.append(f"- {sid} {stage.title}: {row['state']}")
    skipped = []
    for page_type, entry in (draft.get("meta") or {}).items():
        for field in (entry or {}).get("skipped") or []:
            skipped.append(f"{page_type}.{field}")
    hints = [f"- [{m['stage']}] {m['text'][:HINT_CLIP]}" for m in state.messages(site_id, role="user", limit=20)]
    lines = head + ["Stages:"] + (stage_lines or ["- (none finished)"])
    from ..sitemap import labels
    confirmed = labels.summary_lines(draft)
    if confirmed:   # T17: what the admin said the home sections / menu links are
        lines += ["Home labels (confirmed by the admin):"] + confirmed
    blocks = labels.block_lines(draft)
    if blocks:   # A2: which blocks of a listing page are read, as what
        lines += ["Listing blocks (confirmed by the admin):"] + blocks
    if skipped:
        lines.append("Skipped fields (not on this site): " + ", ".join(skipped))
    if gaps:
        lines += ["Open engine gaps:"] + gaps
    # hints last: the oldest go first when the brief is too long
    budget = MAX_BYTES - len("\n".join(lines).encode("utf-8")) - 40
    kept: list[str] = []
    for line in reversed(hints):
        size = len(line.encode("utf-8")) + 1
        if size > budget:
            break
        kept.insert(0, line)
        budget -= size
    if kept:
        lines += ["Admin hints on this site:"] + kept
    return _fit(lines, MAX_BYTES)
