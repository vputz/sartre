#!/usr/bin/env bash
# Pinned TLA+ verification protocol for design-time vetting.
#
#   PHASE 1  SANY parse        — fail fast on syntax / semantic errors
#   PHASE 2  smoke simulation  — cheap random walk; catches gross modelling errors
#   PHASE 3  exhaustive check  — the real BFS model check, with coverage
#   GATE     coverage gate     — any action that NEVER fired = vacuous spec = FAIL
#
# Usage:
#   run_tlc.sh <module.tla> [config.cfg] [--deadlock-check] [--smoke-only]
#
#   config.cfg        defaults to <module>.cfg next to the module.
#   --deadlock-check  ENABLE deadlock checking (default: OFF — design models
#                     routinely have legitimate terminal states e.g. Canceled).
#   --smoke-only      stop after PHASE 2 (fast inner-loop while drafting a spec).
#
# Exit code: 0 = all invariants hold AND coverage gate passed.
#            2 = parse failure.  Other non-zero = invariant violation / TLC error
#            / coverage-gate failure. Read the trace + coverage block above.
#
# tlc bundles its own JRE (nixpkgs#tlaplus); no separate `java` needed.
set -uo pipefail

MODULE="${1:?usage: run_tlc.sh <module.tla> [config.cfg] [--deadlock-check] [--smoke-only]}"
shift || true

CFG=""
DEADLOCK="-deadlock"     # default: DO NOT check deadlock
SMOKE_ONLY=0
for a in "$@"; do
  case "$a" in
    --deadlock-check) DEADLOCK="" ;;
    --smoke-only)     SMOKE_ONLY=1 ;;
    *.cfg)            CFG="$a" ;;
    *) echo "unknown arg: $a" >&2; exit 64 ;;
  esac
done

MODDIR="$(cd "$(dirname "$MODULE")" && pwd)"
MODFILE="$(basename "$MODULE")"
BASE="${MODFILE%.tla}"
[ -z "$CFG" ] && CFG="$BASE.cfg"
cd "$MODDIR"

if ! command -v tlc >/dev/null 2>&1; then
  echo "RESULT: TOOLING_MISSING — 'tlc' not on PATH. Install: nix profile install nixpkgs#tlaplus" >&2
  exit 3
fi

# Per-phase TLC metadirs under one temp dir. TLC otherwise derives its states
# dir from the wall-clock SECOND, so two phases in the same second collide; an
# explicit -metadir also keeps the spec folder free of `states/` litter.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "===== PHASE 1: SANY parse ($MODFILE) ====="
if ! tlasany "$MODFILE"; then
  echo "RESULT: PARSE_FAILED"
  exit 2
fi

echo
echo "===== PHASE 2: smoke simulation (num=200, depth=25) ====="
tlc -config "$CFG" -metadir "$WORK/smoke" -simulate num=200 -depth 25 -difftrace $DEADLOCK -workers auto "$MODFILE"
SMOKE=${PIPESTATUS[0]}
echo "smoke exit: $SMOKE"
if [ "$SMOKE_ONLY" = "1" ]; then
  echo "RESULT: SMOKE_ONLY exit=$SMOKE"
  exit "$SMOKE"
fi

echo
echo "===== PHASE 3: exhaustive check + coverage ====="
COVOUT="$(mktemp)"
tlc -config "$CFG" -metadir "$WORK/check" -coverage 1 -difftrace $DEADLOCK -workers auto "$MODFILE" 2>&1 | tee "$COVOUT"
CHECK=${PIPESTATUS[0]}

echo
echo "===== COVERAGE GATE ====="
# TLC -coverage prints one line per action as "<action src loc>: <distinct>:<total>".
# An action that never fired shows a leading count of 0 (e.g. ": 0:0"). Any such
# action means the spec is (partly) vacuous: invariants may "pass" only because
# that transition never happens.
ZERO="$(grep -nE ':[[:space:]]*0:[0-9]+' "$COVOUT" || true)"
GATE=0
if [ -n "$ZERO" ]; then
  echo "COVERAGE_GATE: FAIL — action(s) never fired (vacuous spec):"
  echo "$ZERO"
  GATE=1
else
  echo "COVERAGE_GATE: PASS — every reported action fired at least once."
fi
rm -f "$COVOUT"

echo
if [ "$CHECK" -ne 0 ]; then
  echo "RESULT: CHECK_FAILED exit=$CHECK (invariant violation or TLC error — read trace above)"
  exit "$CHECK"
elif [ "$GATE" -ne 0 ]; then
  echo "RESULT: VACUOUS exit=0-but-coverage-gate-failed (invariants hold but some action never fired)"
  exit 1
fi
echo "RESULT: PASS — all invariants hold and every action fired."
exit 0
