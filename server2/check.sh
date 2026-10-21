#!/usr/bin/env bash
# One-command health check for server2 (README §7.1). Stops at the first failing step.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PY="python3"
[ -x venv/bin/python ] && PY="venv/bin/python"
TEST_LIMIT_S=20
# Names of real sites: none of them may appear under app/ or seed/ (K3: no site-specific code).
SITE_NAMES="yabancidizi|sinemalar|ddizi|diziasya|dizibox|dizipal|hdfilmcehennemi|fullhdfilmizlesene|jetfilmizle|dizilla|dizimom|diziwatch"

STEP=0
fail() { echo "FAIL [$STEP] $1"; echo "check.sh: KIRMIZI"; exit 1; }
step() { STEP=$((STEP + 1)); echo "== [$STEP] $1"; }

step "compileall app tools"
"$PY" -m compileall -q app tools >/dev/null || fail "compileall"

step "import app.main"
DATA_DIR="$(mktemp -d)" "$PY" -c "import app.main" || fail "import app.main"

step "node --check admin js"
shopt -s nullglob
JS=(app/admin/static/js/*.js)
if [ ${#JS[@]} -eq 0 ]; then
  echo "   (no admin js yet)"
else
  command -v node >/dev/null || fail "node not found"
  for f in "${JS[@]}"; do node --check "$f" || fail "node --check $f"; done
fi

step "layer rule (tools/check_imports.py)"
"$PY" tools/check_imports.py || fail "layer rule"

step "site name scan (app/, seed/)"
HITS="$(grep -rniEw --exclude-dir=__pycache__ "$SITE_NAMES" app seed 2>/dev/null || true)"
if [ -n "$HITS" ]; then echo "$HITS"; fail "site names found under app/ or seed/"; fi
echo "   ok"

step "unit tests (< ${TEST_LIMIT_S}s)"
T0="$("$PY" -c 'import time; print(time.time())')"
TEST_OUT="$(mktemp)"
"$PY" -m unittest discover -s tests >"$TEST_OUT" 2>&1
RC=$?
tail -n 25 "$TEST_OUT"
SANDBOX_SKIPPED=0
grep -q "^SANDBOX: yerel sunucu" "$TEST_OUT" && SANDBOX_SKIPPED=1
rm -f "$TEST_OUT"
T1="$("$PY" -c 'import time; print(time.time())')"
ELAPSED="$("$PY" -c "print(f'{$T1 - $T0:.1f}')")"
echo "   tests took ${ELAPSED}s"
[ "$RC" -eq 0 ] || fail "unit tests (exit $RC)"
"$PY" -c "import sys; sys.exit(0 if $T1 - $T0 < $TEST_LIMIT_S else 1)" || fail "tests too slow: ${ELAPSED}s >= ${TEST_LIMIT_S}s"

[ "$SANDBOX_SKIPPED" -eq 1 ] && echo "UYARI: yerel sunucu açık; sandbox kontrolü atlandı"
echo "check.sh: YESIL ($STEP adim)"
