"""Persistent job queue (README §5.3): every background job of server2 goes through here and survives a restart."""
from __future__ import annotations

import logging

from . import schedule, store, worker
from .store import active, cancel, enqueue, history, wake
from .worker import Done, Fail, Retry, Wait, add_recover_hook, register

__all__ = ["enqueue", "register", "wake", "cancel", "active", "history", "drain", "Done", "Retry", "Wait", "Fail",
           "start", "shutdown", "add_gc_hook", "add_recover_hook", "gc_hooks", "store", "worker", "schedule"]


def drain(on: bool) -> None:
    store.drain(on)


# Extra cleanups run by the daily ``jobs_gc`` job (bound by ``control.wiring``); each returns a count or None.
gc_hooks: list = []


def add_gc_hook(fn) -> None:
    if fn not in gc_hooks:
        gc_hooks.append(fn)


def _jobs_gc(job, ctx):
    result = {"deleted": store.gc()}
    for fn in list(gc_hooks):
        name = getattr(fn, "__name__", "hook")
        try:
            result[name] = fn()
        except Exception as exc:  # one failing cleanup never fails the gc job
            ctx.log(f"{name} failed: {type(exc).__name__}")
    return Done(result)


log = logging.getLogger(__name__)
DRAIN_NOTE = "İş kuyruğu boşaltma (drain) açık; yeni iş başlamıyor"


def warn_if_draining() -> bool:
    """A persisted drain left on (e.g. an aborted deploy) silently stops every job: log + one unread warn note."""
    if not store.draining():
        return False
    log.warning("jobs: drain is ON (settings %s); no new job will be leased until it is turned off", store.DRAIN_KEY)
    from .. import notes
    if not any(n["text"] == DRAIN_NOTE for n in notes.unread()):
        notes.add("warn", DRAIN_NOTE)
    return True


def start() -> None:
    """main.py startup hook: built-in kinds + periodic definitions, recover expired leases, start the executor."""
    warn_if_draining()
    register("jobs_gc", _jobs_gc)
    schedule.install_defaults()
    worker.start()


def shutdown() -> None:
    """main.py shutdown hook: stop leasing, wait at most 10 s, hand unfinished jobs back to the queue."""
    worker.shutdown(10.0)
