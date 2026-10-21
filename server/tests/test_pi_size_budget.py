"""Size budget of the pi onboarding skill and extension (``server/pi``).

``SKILL.md`` is loaded into EVERY pi run (new site, repair, edit), the extension's tool / parameter descriptions are in the context of EVERY turn,
and a run ``read``s five to nine references. They grew once; these ceilings keep them from growing back (the current sizes are lower: see the
numbers in the messages). A failing test means: DO NOT raise the ceiling, shorten something first (a duplicated explanation, a "why" paragraph, a
long example) and keep the rules.

No pi, no network, no real data."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import re
import unittest
from pathlib import Path

PI = Path(__file__).resolve().parent.parent / "pi"
SKILL_DIR = PI / "skills" / "diziflix-site-onboarding"
REFS = SKILL_DIR / "references"
EXTENSION = PI / "extensions" / "diziflix-onboard.ts"

#: bytes (decimal KB, like the sizes quoted in the plan)
SKILL_MAX = 26_000
EXTENSION_DESCRIPTIONS_MAX = 9_500
REFERENCE_MAX = 15_000

HINT = "this ceiling must not be raised: shorten something first (keep the rules, drop the repetition)"
LITERAL = r'"(?:[^"\\\n]|\\.)*"'


def description_chars(source: str) -> tuple:
    """(characters, literals) of every tool / parameter description of the extension, each string literal counted once: the ``description:``
    values (adjacent literals joined with ``+``) and the first argument of ``str("...")`` (a parameter description)."""
    total = count = 0
    for match in re.finditer(r"description:\s*((?:" + LITERAL + r"\s*\+?\s*)+)", source):
        for literal in re.findall(LITERAL, match.group(1)):
            total += len(literal) - 2
            count += 1
    for match in re.finditer(r"\bstr\(\s*(" + LITERAL + ")", source):
        total += len(match.group(1)) - 2
        count += 1
    return total, count


class PiSizeBudget(unittest.TestCase):
    def test_skill_md_stays_small(self):
        size = len((SKILL_DIR / "SKILL.md").read_bytes())
        self.assertLessEqual(size, SKILL_MAX, "SKILL.md is %d bytes (ceiling %d): %s" % (size, SKILL_MAX, HINT))

    def test_extension_descriptions_stay_small(self):
        total, count = description_chars(EXTENSION.read_text(encoding="utf-8"))
        self.assertGreater(count, 50, "the description reader found only %d literals: it no longer matches the extension's style" % count)
        self.assertGreater(total, 3_000, "the description reader found only %d characters" % total)
        self.assertLessEqual(total, EXTENSION_DESCRIPTIONS_MAX,
                             "the extension's tool / parameter descriptions are %d characters (ceiling %d): %s" % (total, EXTENSION_DESCRIPTIONS_MAX, HINT))

    def test_every_reference_stays_small(self):
        files = sorted(REFS.glob("*.md"))
        self.assertGreaterEqual(len(files), 12)
        for path in files:
            size = len(path.read_bytes())
            self.assertLessEqual(size, REFERENCE_MAX, "%s is %d bytes (ceiling %d): %s" % (path.name, size, REFERENCE_MAX, HINT))


if __name__ == "__main__":
    unittest.main()
