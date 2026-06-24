#!/usr/bin/env bash
# AEON verification sweep: byte-compile Python, run the test suite, and syntax-check JS.
# Usage:  bash scripts/check.sh
set -uo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python"
rc=0

echo "==> py_compile (aeon/)"
"$PY" -m py_compile $(find aeon -name '*.py') && echo "    ok" || { echo "    FAILED"; rc=1; }

echo "==> pytest"
"$PY" -m pytest tests/ -q || { echo "    FAILED"; rc=1; }

if command -v node >/dev/null 2>&1; then
  echo "==> node --check (web JS)"
  jsfail=0
  for f in web/js/*.js web/js/omega/*.js; do
    node --check "$f" || { echo "    FAILED: $f"; jsfail=1; }
  done
  [ "$jsfail" -eq 0 ] && echo "    ok" || rc=1
else
  echo "==> node not found — skipping JS syntax check"
fi

echo
[ "$rc" -eq 0 ] && echo "ALL CHECKS PASSED" || echo "CHECKS FAILED (rc=$rc)"
exit "$rc"
