"""``core.play`` (T03): player page -> candidate -> provider -> stream -> probe, without any control-layer tie.

Entry points: ``ctx.PlayCtx`` / ``make_ctx``; ``candidates.discover`` / ``resolve_candidate``; ``follow.follow`` (the single
playback verifier); ``providers.resolve`` (the provider library, K4); ``gate`` / ``blocked`` (verdicts only); ``sources``
(``video_sources`` resolution + health); ``streamdiag`` / ``streamprobe`` / ``hostrules``; ``streamproxy`` / ``hlsproxy``.
Late binding of the site map: ``hooks.bind_ctx``.
"""
from .ctx import PlayCtx, make_ctx  # noqa: F401
