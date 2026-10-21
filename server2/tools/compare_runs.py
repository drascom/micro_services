"""A/B comparison of two onboarding + crawl runs (each run = its own DATA_DIR with ``diziflix2.db``).

usage: venv/bin/python -m tools.compare_runs data_A/diziflix2.db data_B/diziflix2.db [--site X] [--play N] [--json]

Both databases are opened READ-ONLY (``file:...?mode=ro``). A missing table / column is reported as "yok", never a crash
(the two runs may have different schemas). ``--play N`` (real network, optional): N random sources of each run are resolved
and play-tested with ``app.core.play`` in a child process whose DATA_DIR is a temporary COPY of that run.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any, Optional

STAGES = ("A1", "A2", "A3", "A4", "A5", "A6", "A7")
SOURCE_STATES = ("unknown", "healthy", "no_source", "broken", "blocked")
VISIBLE = ("unknown", "healthy")
FINAL = ("passed", "passed_warn", "needs_human")
HELP_TITLE = "Yardım gerekiyor"
ADMIN_TRIGGERS = ("admin", "user", "manual")
YOK = "yok"


class Run:
    """Read-only view of one run's db; every query degrades to ``None`` ("yok") when a table / column is missing."""

    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        self._cols: dict[str, set] = {}

    def cols(self, table: str) -> set:
        if table not in self._cols:
            try:
                self._cols[table] = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            except sqlite3.Error:
                self._cols[table] = set()
        return self._cols[table]

    def has(self, table: str, *columns: str) -> bool:
        have = self.cols(table)
        return bool(have) and all(c in have for c in columns)

    def rows(self, sql: str, params=()) -> Optional[list]:
        try:
            return list(self.conn.execute(sql, params))
        except sqlite3.Error:
            return None

    def one(self, sql: str, params=()) -> Any:
        rows = self.rows(sql, params)
        return rows[0][0] if rows else None

    def sites(self) -> list[str]:
        found: set = set()
        for table, col in (("sites", "site_id"), ("site_stages", "site_id"), ("llm_calls", "site_id"),
                           ("source_items", "site_id"), ("video_sources", "source"), ("crawl_frontier", "site_id")):
            if self.has(table, col):
                found |= {r[0] for r in self.rows(f"SELECT DISTINCT {col} FROM {table}") or [] if r[0]}
        return sorted(found)


# ---------------------------------------------------------------- collection

def _llm(run: Run, site: str, stage: str) -> dict:
    if not run.has("llm_calls", "site_id", "stage"):
        return {"llm": YOK}
    cols = run.cols("llm_calls")
    rows = run.rows("SELECT * FROM llm_calls WHERE site_id=? AND stage=?", (site, stage)) or []
    out: dict = {"calls": len(rows)}
    if "trigger" in cols:
        admin = sum(1 for r in rows if str(r["trigger"] or "").lower() in ADMIN_TRIGGERS)
        out["calls_admin"], out["calls_system"] = admin, len(rows) - admin
    elif "background" in cols:
        system = sum(1 for r in rows if r["background"])
        out["calls_admin"], out["calls_system"] = len(rows) - system, system
    else:
        out["calls_admin"] = out["calls_system"] = YOK
    for col in ("prompt_chars", "reply_chars"):
        out[col] = sum(int(r[col] or 0) for r in rows) if col in cols else YOK
    if "ok" in cols:
        out["ok"] = sum(1 for r in rows if r["ok"])
        out["errors"] = len(rows) - out["ok"]
    else:
        out["ok"] = out["errors"] = YOK
    fixes = sum(1 for r in rows if any(w in str(r["purpose"] or "").lower() for w in ("fix", "format", "biçim"))) \
        if "purpose" in cols else 0
    if not fixes and run.has("stage_messages", "text"):
        fixes = run.one("SELECT COUNT(*) FROM stage_messages WHERE site_id=? AND stage=? AND text LIKE ?",
                        (site, stage, "%biçim düzeltmesi%")) or 0
    out["format_fixes"] = fixes
    return out


def _stage_time(run: Run, site: str, stage: str, row: Optional[sqlite3.Row]) -> dict:
    out: dict = {"seconds_to_pass": None, "help": YOK, "user_msgs": YOK}
    if run.has("stage_messages", "site_id", "stage", "role", "text", "at"):
        msgs = run.rows("SELECT role, text, at FROM stage_messages WHERE site_id=? AND stage=? ORDER BY id",
                        (site, stage)) or []
        out["help"] = sum(1 for m in msgs if str(m["text"]).startswith(HELP_TITLE))
        out["user_msgs"] = sum(1 for m in msgs if m["role"] == "user")
        start = next((m["at"] for m in msgs if "başladı" in str(m["text"])), msgs[0]["at"] if msgs else None)
        passed = next((m["at"] for m in msgs if str(m["text"]).startswith("Geçti") and (start is None or m["at"] >= start)),
                      None)
        if start is not None and passed is not None:
            out["seconds_to_pass"] = int(passed - start)
    if out["seconds_to_pass"] is None and row is not None and str(row["state"] or "").startswith("passed"):
        keys = row.keys()
        if "started_at" in keys and "updated_at" in keys and row["started_at"] and row["updated_at"]:
            out["seconds_to_pass"] = int(row["updated_at"] - row["started_at"])
    return out


def _stages(run: Run, site: str) -> dict:
    have = run.has("site_stages", "site_id", "stage")
    out: dict = {}
    for stage in STAGES:
        row = None
        if have:
            got = run.rows("SELECT * FROM site_stages WHERE site_id=? AND stage=?", (site, stage))
            row = got[0] if got else None
        info = {"state": (row["state"] if row is not None and "state" in row.keys() else (YOK if not have else "-"))}
        info["rounds"] = (row["round"] if row is not None and "round" in row.keys() else YOK)
        info["runs"] = (row["runs"] if row is not None and "runs" in row.keys() else YOK)
        info.update(_llm(run, site, stage))
        info.update(_stage_time(run, site, stage, row))
        out[stage] = info
    return out


def _site_titles(run: Run, site: str) -> Optional[str]:
    """SQL sub-select of the canonical ids that belong to ``site`` (``None`` when nothing links titles to sites)."""
    parts = []
    if run.has("source_items", "site_id", "canonical_id"):
        parts.append("SELECT canonical_id FROM source_items WHERE site_id=:s AND canonical_id IS NOT NULL")
    if run.has("video_sources", "source", "canonical_id"):
        parts.append("SELECT canonical_id FROM video_sources WHERE source=:s")
    return " UNION ".join(parts) or None


def _catalog(run: Run, site: str) -> dict:
    sub = _site_titles(run, site)
    if not sub or not run.has("library_items", "id"):
        return {"catalog": YOK}
    p = {"s": site}
    out: dict = {}
    if run.has("library_items", "type"):
        out["titles_series"] = run.one(f"SELECT COUNT(*) FROM library_items WHERE type='series' AND id IN ({sub})", p)
        out["titles_movie"] = run.one(f"SELECT COUNT(*) FROM library_items WHERE type!='series' AND id IN ({sub})", p)
    out["titles"] = run.one(f"SELECT COUNT(*) FROM library_items WHERE id IN ({sub})", p)
    out["tmdb_matched"] = (run.one(f"SELECT COUNT(*) FROM library_items WHERE tmdb_id IS NOT NULL AND id IN ({sub})", p)
                           if run.has("library_items", "tmdb_id") else YOK)
    if run.has("video_sources", "source", "kind", "episode_id", "canonical_id"):
        out["series_with_episodes"] = run.one(
            "SELECT COUNT(DISTINCT canonical_id) FROM video_sources WHERE source=:s AND kind='episode'", p)
        out["episodes"] = run.one("SELECT COUNT(*) FROM (SELECT DISTINCT canonical_id, episode_id FROM video_sources "
                                  "WHERE source=:s AND kind='episode')", p)
    else:
        out["series_with_episodes"] = out["episodes"] = YOK
    out["library_episodes"] = (run.one(f"SELECT COUNT(*) FROM library_episodes WHERE canonical_id IN ({sub})", p)
                               if run.has("library_episodes", "canonical_id") else YOK)
    if run.has("video_sources", "source", "status"):
        by = {r[0]: r[1] for r in run.rows("SELECT status, COUNT(*) FROM video_sources WHERE source=:s GROUP BY status",
                                           p) or []}
        out["sources"] = {**{k: by.pop(k, 0) for k in SOURCE_STATES}, **by}
        out["visible_titles"] = run.one(
            "SELECT COUNT(DISTINCT canonical_id) FROM video_sources WHERE source=:s AND status IN (%s)"
            % ",".join(f"'{s}'" for s in VISIBLE), p)
    else:
        out["sources"] = out["visible_titles"] = YOK
    return out


def _crawl(run: Run, site: str) -> dict:
    if not run.has("crawl_frontier", "site_id", "state", "page_type"):
        return {"crawl": YOK}
    out = {"state": {r[0]: r[1] for r in run.rows(
               "SELECT state, COUNT(*) FROM crawl_frontier WHERE site_id=? GROUP BY state", (site,)) or []},
           "page_type": {r[0]: r[1] for r in run.rows(
               "SELECT page_type, COUNT(*) FROM crawl_frontier WHERE site_id=? GROUP BY page_type", (site,)) or []}}
    cond = "state IN ('failed','error','dead')" + (" OR fail_count>0" if run.has("crawl_frontier", "fail_count") else "")
    out["failed"] = run.one(f"SELECT COUNT(*) FROM crawl_frontier WHERE site_id=? AND ({cond})", (site,))
    if run.has("jobs", "site_id", "kind", "state"):
        out["jobs"] = {f"{r[0]}/{r[1]}": r[2] for r in run.rows(
            "SELECT kind, state, COUNT(*) FROM jobs WHERE site_id=? GROUP BY kind, state", (site,)) or []}
    return out


def collect(path: str, site: Optional[str] = None) -> dict:
    run = Run(path)
    try:
        sites = [site] if site else run.sites()
        return {"db": path, "sites": {s: {"stages": _stages(run, s), "catalog": _catalog(run, s), "crawl": _crawl(run, s)}
                                      for s in sites}}
    finally:
        run.conn.close()


# ---------------------------------------------------------------- --play (child process, real network)

def _play_worker(path: str, n: int, seed: Optional[int]) -> dict:
    """Runs in a fresh process: DATA_DIR = a temporary COPY of the run, then ``app.core.play`` resolves + plays."""
    tmp = tempfile.mkdtemp(prefix="compare_play_")
    try:
        src = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
        dst = sqlite3.connect(os.path.join(tmp, "diziflix2.db"))
        src.backup(dst)
        src.close(), dst.close()
        providers = os.path.join(os.path.dirname(os.path.abspath(path)), "providers")
        if os.path.isdir(providers):
            shutil.copytree(providers, os.path.join(tmp, "providers"))
        os.environ["DATA_DIR"] = tmp
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from app import db  # noqa: E402  (after DATA_DIR)
        try:
            db.init()
        except Exception:
            pass
        from app.control import wiring
        from app.core.play import hooks, playtest, sources
        hooks.bind_ctx(wiring._play_ctx)
        rows = db.query("SELECT * FROM video_sources WHERE kind!='trailer'")
        rng = random.Random(seed)
        picked = rng.sample(list(rows), min(n, len(rows)))
        results = []
        for row in picked:
            item = {"id": row["id"], "site": row["source"], "played": False}
            try:
                res = sources.resolve_source(row, force=True)
                stream = next((s for s in res.get("streams") or [] if s.get("type") != "embed"), None)
                if not stream:
                    item["error"] = "akış yok"
                else:
                    kind = "hls" if str(stream.get("type") or "").lower() in ("hls", "m3u8") else None
                    out = playtest.play_test(stream["url"], stream.get("request_headers") or stream.get("headers"), kind)
                    item["played"], item["error"] = bool(out.get("played")), out.get("error")
            except Exception as exc:
                item["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            results.append(item)
        played = sum(1 for r in results if r["played"])
        return {"tried": len(results), "played": played, "ratio": (played / len(results)) if results else None,
                "items": results}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def play(path: str, n: int, seed: Optional[int]) -> dict:
    cmd = [sys.executable, "-m", "tools.compare_runs", "--play-worker", path, "--play", str(n)]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (proc.stderr or "play worker failed").strip()[-300:]}


# ---------------------------------------------------------------- comparison + output

def _num(v) -> Optional[float]:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _sum(data: dict, getter) -> int:
    total = 0
    for s in data["sites"].values():
        for v in getter(s):
            total += _num(v) or 0
    return total


def totals(data: dict) -> dict:
    st = lambda key: (lambda s: [x.get(key) for x in s["stages"].values()])  # noqa: E731
    return {"llm_calls": _sum(data, st("calls")), "user_msgs": _sum(data, st("user_msgs")), "help": _sum(data, st("help")),
            "episodes": _sum(data, lambda s: [s["catalog"].get("episodes")]),
            "titles": _sum(data, lambda s: [s["catalog"].get("titles")]),
            "visible_titles": _sum(data, lambda s: [s["catalog"].get("visible_titles")])}


def summary(a: dict, b: dict) -> dict:
    ta, tb = totals(a), totals(b)
    out = {"A": ta, "B": tb}
    out["llm_calls_pct"] = round((tb["llm_calls"] - ta["llm_calls"]) * 100.0 / ta["llm_calls"], 1) if ta["llm_calls"] else None
    out["interventions_diff"] = tb["user_msgs"] - ta["user_msgs"]
    out["episodes_diff"] = tb["episodes"] - ta["episodes"]
    out["play"] = {k: (d.get("play") or {}).get("ratio") for k, d in (("A", a), ("B", b))}
    return out


def _signed(v, pct: bool = False) -> str:
    if v is None:
        return YOK
    sign = "+" if v > 0 else ("−" if v < 0 else "±")
    return f"{sign}%{abs(v):g}" if pct else f"{sign}{abs(v):g}"


def summary_line(s: dict) -> str:
    pa, pb = s["play"]["A"], s["play"]["B"]
    play_txt = "ölçülmedi" if pa is None and pb is None else \
        f"A %{(pa or 0) * 100:.0f} -> B %{(pb or 0) * 100:.0f}"
    return (f"LLM çağrısı {_signed(s['llm_calls_pct'], pct=True)}, müdahale {_signed(s['interventions_diff'])}, "
            f"bölüm {_signed(s['episodes_diff'])}, oynayan oranı {play_txt}")


def _cell(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, dict):
        return " ".join(f"{k}:{n}" for k, n in v.items()) or "-"
    return str(v)


def _diff(a, b) -> str:
    na, nb = _num(a), _num(b)
    return _signed(nb - na) if na is not None and nb is not None else ""


STAGE_ROWS = (("durum", "state"), ("LLM çağrı", "calls"), ("  admin", "calls_admin"), ("  sistem", "calls_system"),
              ("prompt karakter", "prompt_chars"), ("yanıt karakter", "reply_chars"), ("ok", "ok"), ("hata", "errors"),
              ("biçim düzeltme", "format_fixes"), ("tur", "rounds"), ("koşu", "runs"), ("geçme süresi (sn)", "seconds_to_pass"),
              ("Yardım gerekiyor", "help"), ("kullanıcı mesajı", "user_msgs"))
CATALOG_ROWS = (("başlık", "titles"), ("  dizi", "titles_series"), ("  film", "titles_movie"),
                ("bölümlü dizi", "series_with_episodes"), ("bölüm (kaynak)", "episodes"),
                ("bölüm (library_episodes)", "library_episodes"), ("TMDB eşleşen", "tmdb_matched"),
                ("istemcide görünen", "visible_titles"), ("kaynaklar", "sources"))


def _table(title: str, lines: list) -> list[str]:
    w0 = max([len(l[0]) for l in lines] + [10])
    w1 = max([len(l[1]) for l in lines] + [6])
    w2 = max([len(l[2]) for l in lines] + [6])
    out = [f"  {title}", f"    {'':<{w0}}  {'A':<{w1}}  {'B':<{w2}}  fark"]
    out += [f"    {l[0]:<{w0}}  {l[1]:<{w1}}  {l[2]:<{w2}}  {l[3]}" for l in lines]
    return out


def render(a: dict, b: dict, s: dict) -> str:
    out = [f"A: {a['db']}", f"B: {b['db']}", ""]
    for site in sorted(set(a["sites"]) | set(b["sites"])):
        sa, sb = a["sites"].get(site), b["sites"].get(site)
        out.append(f"=== {site}" + ("" if sa and sb else f"  (yalnız {'A' if sa else 'B'} içinde)"))
        for stage in STAGES:
            xa = (sa or {}).get("stages", {}).get(stage, {})
            xb = (sb or {}).get("stages", {}).get(stage, {})
            if not any(_num(x.get("calls")) or x.get("state") not in (None, "-", YOK) for x in (xa, xb)):
                continue
            out += _table(stage, [(lbl, _cell(xa.get(k, YOK)), _cell(xb.get(k, YOK)), _diff(xa.get(k), xb.get(k)))
                                  for lbl, k in STAGE_ROWS])
        ca, cb = (sa or {}).get("catalog", {}), (sb or {}).get("catalog", {})
        out += _table("Katalog", [(lbl, _cell(ca.get(k, YOK)), _cell(cb.get(k, YOK)), _diff(ca.get(k), cb.get(k)))
                                  for lbl, k in CATALOG_ROWS])
        ka, kb = (sa or {}).get("crawl", {}), (sb or {}).get("crawl", {})
        out += _table("Tarama", [(lbl, _cell(ka.get(k, ka.get("crawl"))), _cell(kb.get(k, kb.get("crawl"))),
                                  _diff(ka.get(k), kb.get(k)))
                                 for lbl, k in (("durum", "state"), ("sayfa türü", "page_type"), ("başarısız", "failed"),
                                                ("işler", "jobs"))])
        out.append("")
    for key in ("A", "B"):
        p = (a if key == "A" else b).get("play")
        if p:
            out.append(f"Oynatma {key}: " + (p["error"] if p.get("error") else f"{p['played']}/{p['tried']} oynadı"))
    out.append("ÖZET: " + summary_line(s))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="İki onboarding + tarama koşusunun A/B karşılaştırması")
    ap.add_argument("db_a")
    ap.add_argument("db_b", nargs="?")
    ap.add_argument("--site")
    ap.add_argument("--play", type=int, default=0, help="her koşudan N rastgele kaynağı gerçekten çöz + oynat (ağ)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--play-worker", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.play_worker:
        print(json.dumps(_play_worker(args.db_a, args.play, args.seed), ensure_ascii=False))
        return 0
    if not args.db_b:
        ap.error("iki db yolu gerekli")
    for path in (args.db_a, args.db_b):
        if not os.path.isfile(path):
            ap.error(f"db yok: {path}")
    a, b = collect(args.db_a, args.site), collect(args.db_b, args.site)
    if args.play > 0:
        a["play"], b["play"] = play(args.db_a, args.play, args.seed), play(args.db_b, args.play, args.seed)
    s = summary(a, b)
    if args.json:
        print(json.dumps({"A": a, "B": b, "summary": s, "summary_line": summary_line(s)}, ensure_ascii=False, indent=1))
    else:
        print(render(a, b, s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
