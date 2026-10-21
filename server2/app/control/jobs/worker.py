"""Job executor: N worker threads + 1 lease renewer + 1 scheduler loop (README §5.3).

Handlers are registered per kind (code configuration, not queue state). Every job state is read from / written to the
``jobs`` table through ``store``; the only in-process state is the stop flag and the thread handles.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ... import db
from . import schedule, store

log = logging.getLogger("diziflix.jobs")

DEFAULT_THREADS = 4
DEFAULT_TIMEOUT_S = 600
RENEW_EVERY_S = 30
TIMEOUT_GRACE_S = 60     # a handler past deadline + grace loses its lease and the job fails with ``timeout``
SCHEDULER_EVERY_S = 5
LOG_KEEP = 20


# --------------------------------------------------------------------------- handler results

@dataclass
class Done:
    result: Any = None


@dataclass
class Retry:
    after_s: float = 60
    error: str = ""
    #: a planned yield (the handler stopped at its deadline with its progress checkpointed): the run does not count
    yield_: bool = False


@dataclass
class Wait:
    reason: str = ""


@dataclass
class Fail:
    error: str = ""


class LeaseLost(Exception):
    """The job is no longer ours (cancelled or recovered by someone else): stop working on it."""


@dataclass
class _Kind:
    handler: Callable[[dict, "Context"], Any]
    llm: bool = False
    timeout_s: int = DEFAULT_TIMEOUT_S
    on_fail: Optional[Callable[[dict, str], Any]] = None


_kinds: dict[str, _Kind] = {}
#: ``fn() -> Any`` run after every ``store.recover()`` (startup + scheduler loop): owners reconcile their own rows
#: with jobs that are no longer active (crashed / cancelled).
recover_hooks: list = []


def register(kind: str, handler: Callable[[dict, "Context"], Any], *, llm: bool = False,
             timeout_s: int = DEFAULT_TIMEOUT_S, on_fail: Optional[Callable[[dict, str], Any]] = None) -> None:
    """``on_fail(job, error)``: called once when a job of this kind ends ``failed`` (Fail, attempts used up, timeout)."""
    _kinds[kind] = _Kind(handler, llm, int(timeout_s), on_fail)


def add_recover_hook(fn) -> None:
    if fn not in recover_hooks:
        recover_hooks.append(fn)


def run_recover_hooks() -> None:
    for fn in list(recover_hooks):
        try:
            fn()
        except Exception:  # one failing hook never stops recovery
            log.exception("recover hook %s failed", getattr(fn, "__name__", fn))


def recover(now: Optional[float] = None) -> int:
    """``store.recover`` + the recover hooks."""
    n = store.recover(now)
    run_recover_hooks()
    return n


def _failed(job: dict, error: str) -> None:
    spec = _kinds.get(job.get("kind") or "")
    if spec is None or spec.on_fail is None:
        return
    try:
        spec.on_fail(job, error)
    except Exception:  # pragma: no cover - a broken hook never breaks the worker
        log.exception("on_fail for job %s failed", job.get("id"))


def unregister(kind: str) -> None:
    _kinds.pop(kind, None)


def kinds() -> list[str]:
    return list(_kinds)


def llm_kinds() -> list[str]:
    return [k for k, v in _kinds.items() if v.llm]


@dataclass
class Context:
    job: dict
    owner: str
    deadline: float
    notes: list = field(default_factory=list)

    def checkpoint(self, step: int, payload: Any = None) -> None:
        if not store.checkpoint(self.job["id"], self.owner, step, payload):
            raise LeaseLost(f"job {self.job['id']} lease lost")
        self.job["step"] = int(step)
        if payload is not None:
            self.job["payload"] = payload

    def log(self, note: str) -> None:
        self.notes.append(str(note)[:500])
        del self.notes[:-LOG_KEEP]
        store.set_result(self.job["id"], self.owner, {"log": list(self.notes)})

    def expired(self) -> bool:
        return time.time() >= self.deadline


def _with_log(result: Any, notes: list) -> Any:
    if not notes:
        return result
    if isinstance(result, dict):
        return {**result, "log": list(notes)}
    return {"value": result, "log": list(notes)}


class Runner:
    def __init__(self, threads: int = DEFAULT_THREADS, poll_s: float = 1.0, owner: Optional[str] = None) -> None:
        self.threads = max(1, int(threads))
        self.poll_s = poll_s
        self.owner = owner or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._running: dict[int, float] = {}   # job id -> deadline (in-process bookkeeping for the renewer)
        self._running_lock = threading.Lock()

    # ---- one job (also used directly by tests)

    def run_one(self, now: Optional[float] = None) -> Optional[dict]:
        """Lease one due job and run it in the calling thread. Returns the leased job (before running) or None."""
        job = store.lease(self.owner, kinds(), now, llm_kinds=llm_kinds())
        if job is None:
            return None
        self._execute(job)
        return job

    def _execute(self, job: dict) -> None:
        spec = _kinds.get(job["kind"])
        jid = job["id"]
        if spec is None:  # handler unregistered between lease and run
            store.retry(jid, self.owner, 60, f"no handler for kind {job['kind']!r}")
            return
        ctx = Context(job=dict(job), owner=self.owner, deadline=time.time() + spec.timeout_s)
        with self._running_lock:
            self._running[jid] = ctx.deadline
        try:
            outcome = spec.handler(ctx.job, ctx)
        except LeaseLost:
            log.info("job %s: lease lost, dropped", jid)
            return
        except Exception as exc:  # noqa: BLE001 - an uncaught handler error is a retry
            tb = traceback.format_exc(limit=5)[-2000:]
            outcome = Retry(60, f"{type(exc).__name__}: {exc}\n{tb}")
        finally:
            with self._running_lock:
                self._running.pop(jid, None)
        # store.* writes below are guarded by state='running' AND lease_owner: a job already failed with
        # ``timeout`` by the renewer is not overwritten by a late return.
        if outcome is None or not isinstance(outcome, (Done, Retry, Wait, Fail)):
            outcome = Done(outcome)
        if isinstance(outcome, Done):
            store.complete(jid, self.owner, _with_log(outcome.result, ctx.notes))
        elif isinstance(outcome, Retry):
            if store.retry(jid, self.owner, outcome.after_s, outcome.error, refund=outcome.yield_) == "failed":
                _failed(ctx.job, (store.get(jid) or {}).get("error") or outcome.error)
        elif isinstance(outcome, Wait):
            store.wait(jid, self.owner, outcome.reason)
        elif store.fail(jid, self.owner, outcome.error):
            _failed(ctx.job, outcome.error)

    # ---- loops

    def _work_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self.run_one()
            except Exception:  # pragma: no cover - keep the thread alive
                log.exception("job worker error")
                job = None
            if job is None:
                self._stop.wait(self.poll_s)

    def renew_tick(self, now: Optional[float] = None) -> list[int]:
        """Renew the leases of our running jobs that are within deadline + grace; fail the overdue ones with
        ``timeout`` (a hung handler must not hold its lease, e.g. the single LLM slot, forever). Returns failed ids."""
        t = time.time() if now is None else now
        with self._running_lock:
            running = dict(self._running)
        overdue = [jid for jid, deadline in running.items() if t > deadline + TIMEOUT_GRACE_S]
        for jid in overdue:
            if store.fail(jid, self.owner, "timeout", now):
                log.warning("job %s: past deadline + %ss, failed (timeout)", jid, TIMEOUT_GRACE_S)
                job = store.get(jid)
                if job is not None:
                    _failed(job, "timeout")
            with self._running_lock:
                self._running.pop(jid, None)
        store.renew(self.owner, now, job_ids=[j for j in running if j not in overdue])
        return overdue

    def _renew_loop(self) -> None:
        while not self._stop.wait(RENEW_EVERY_S):
            try:
                self.renew_tick()
            except Exception:  # pragma: no cover
                log.exception("lease renew failed")

    def _schedule_loop(self) -> None:
        while not self._stop.is_set():
            try:
                recover()
                schedule.tick()
            except Exception:  # pragma: no cover
                log.exception("scheduler tick failed")
            self._stop.wait(SCHEDULER_EVERY_S)

    @staticmethod
    def _closing(fn: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                fn()
            finally:
                db.close_thread()  # the thread's shared connection dies with it
        return run

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        targets = [self._work_loop] * self.threads + [self._renew_loop, self._schedule_loop]
        for i, fn in enumerate(targets):
            t = threading.Thread(target=self._closing(fn), name=f"jobs-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, wait_s: float = 10.0) -> None:
        """Stop leasing, give running jobs up to ``wait_s`` to finish, then hand the unfinished ones back to the queue."""
        self._stop.set()
        end = time.time() + wait_s
        for t in self._threads:
            t.join(max(0.0, end - time.time()))
        self._threads = []
        try:
            store.release(self.owner)
        except Exception:  # pragma: no cover
            log.exception("lease release failed")


_runner: Optional[Runner] = None


def start(threads: int = DEFAULT_THREADS) -> Runner:
    """Startup hook: recover expired leases and start the executor."""
    global _runner
    recover()
    if _runner is None:
        _runner = Runner(threads)
        _runner.start()
    return _runner


def shutdown(wait_s: float = 10.0) -> None:
    """Shutdown hook: drain this process (not persisted) and wait at most ``wait_s``."""
    global _runner
    if _runner is not None:
        _runner.stop(wait_s)
        _runner = None
