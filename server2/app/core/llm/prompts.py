"""Shared prompt skeleton: TASK, RULES, EVIDENCE, ANSWER FORMAT. Stage-specific text lives in its own task (T08/T09/T12/T14)."""
from __future__ import annotations

EVIDENCE_LIMIT = 15_000


def clip(text: str, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... truncated, {len(text) - limit} more chars]"


def build(task: str, rules: list[str], evidence: dict[str, str], answer_format: str, *,
          evidence_limit: int = EVIDENCE_LIMIT) -> str:
    """Every piece of evidence is clipped on its own (one huge page cannot crowd out the others)."""
    parts = ["## TASK", str(task).strip(), "", "## RULES"]
    parts += [f"- {r}" for r in rules] or ["- (none)"]
    parts += ["", "## EVIDENCE"]
    if not evidence:
        parts.append("(none)")
    for name, body in evidence.items():
        parts += [f"### {name}", "'''", clip(body, evidence_limit), "'''"]
    parts += ["", "## ANSWER FORMAT", str(answer_format).strip()]
    return "\n".join(parts) + "\n"
