"""Problem record (T13, README §5.7): every unsolved situation is ONE row with one state machine, LLM-free diagnosis,
measurement-only resolution and watcher notifications. T14: ``repair`` (stage repair mode, K8), ``gaps`` (capability gaps),
``system`` (problems the system finds itself).

Modules: ``store`` (rows, transitions, watchers), ``intake`` (event subscribers), ``diagnose`` (evidence -> class/action),
``actions`` (LLM-free actions + measurement), ``run`` (job kind ``problem``), ``confirm`` (K9), ``notify``, ``client``,
``repair``, ``gaps``, ``system``, ``views`` (admin Problems screen data).
"""
from __future__ import annotations

from .client import client_state, finder_status  # noqa: F401

SWEEP_EVERY_S = 3600


def _sweep_job(job, ctx):
    from .. import jobs
    from . import confirm
    return jobs.Done({"closed": confirm.sweep()})


def register() -> None:
    """Startup (``control.wiring``): job kinds ``problem`` + ``problems_sweep``, event subscriptions, the recover hook that
    re-queues a working problem whose job row is gone."""
    from .. import jobs
    from . import intake, run
    jobs.register(run.KIND, run.handle, llm=False, timeout_s=300, on_fail=run.on_fail)
    jobs.register("problems_sweep", _sweep_job)
    jobs.schedule.every("problems_sweep", "problems_sweep", SWEEP_EVERY_S)
    jobs.add_recover_hook(run.recover_missing)
    intake.subscribe()
    _register_repair()


REPAIR_TICK_S = 300


def _register_repair() -> None:
    """T14: repair results from the stage engine, the admin's ``mark_gap``, ``repair_tick`` (waiting_llm -> again),
    ``problem_scan`` (daily system problems) and the startup capability-catalogue check."""
    from .. import jobs
    from ..stages import actions as stage_actions, engine
    from . import gaps, repair, system
    if repair.on_stage_result not in engine.RESULT_HOOKS:
        engine.RESULT_HOOKS.append(repair.on_stage_result)
    stage_actions.GAP_HOOK = gaps.from_mark_gap
    jobs.register("repair_tick", repair.tick_job)
    jobs.schedule.every("repair_tick", "repair_tick", REPAIR_TICK_S)
    jobs.register("problem_scan", system.scan_job)
    jobs.schedule.every("problem_scan", "problem_scan", system.SCAN_EVERY_S)
    jobs.add_recover_hook(gaps.check_catalog)
