#!/bin/sh
# Stage-2 paired free measurement (night/stage2/CRITERION.md) under the v161 flags; runs in the tree it lives in.
#   sh night/stage2/measure.sh <tag> [base-tag]      # full: every instrument + the stage-2 phrasings
#   ONLY=phrasings sh night/stage2/measure.sh <tag> [base-tag]
# An arm's own switch comes from the caller's environment (e.g. RETRIEVE_RESERVE_CUE=1); levers_measure
# inherits it. Base records (night/head100/out/*_<base>.json, night/out/routed_<base>.json,
# night/out/real24_window_<base>.json) must already be in the tree.
set -u
cd "$(dirname "$0")/../.." || exit 2
PY=/d/app_soldier/venv/Scripts/python.exe
REAL=${REAL_QUESTIONS:-/d/app_soldier/night/out/real_questions.json}
TAG=$1; BASE=${2:-}; ONLY=${ONLY:-full}
H=night/head100/out
export PYTHONIOENCODING=utf-8 RETRIEVE_HYDE=0
export RETRIEVE_GLOSSARY=1 RETRIEVE_FULL_BLOCKS=1 RETRIEVE_ROUTER_SLOTS=2 RETRIEVE_DOC_BLOCKS=7
export RETRIEVE_FULL_BLOCK_MAX_WORDS=2000 RETRIEVE_HOMONYMS=1 RETRIEVE_QUOTELESS=1
export RETRIEVE_LACK_CLAUSES=3 RETRIEVE_SECOND_PASS=4 RETRIEVE_SECOND_PASS_KEEP_RULING=1 ANSWER_V2=1
unset ANTHROPIC_API_KEY
if [ "$ONLY" = full ]; then
  echo "== levers_measure $TAG: frozen ruler 82, head-100, gate 431, router"
  if [ -n "$BASE" ]; then
    $PY -m night.levers_measure "$TAG" RETRIEVE_DOC_BLOCKS=7 RETRIEVE_HOMONYMS=1 RETRIEVE_QUOTELESS=1 "BASE=$BASE"
  else
    $PY -m night.levers_measure "$TAG" RETRIEVE_DOC_BLOCKS=7 RETRIEVE_HOMONYMS=1 RETRIEVE_QUOTELESS=1
  fi
  echo "== v2 ruler"; $PY -m night.head100.probe_variant night/out/adjudication_realstyle_v2.json $H/rulerv2_$TAG.json
  echo "== c"; $PY -m night.head100.probe_variant night/head100/targets_c.json $H/c_$TAG.json
  echo "== locked homonym set"; $PY -m night.head100.probe_variant night/head100/homonym_targets.json $H/homset_$TAG.json
  echo "== real24"
  if [ -n "$BASE" ]; then $PY -m night.real24_window "$REAL" "$TAG" "BASE=$BASE" | tail -2; else $PY -m night.real24_window "$REAL" "$TAG" | tail -1; fi
fi
echo "== stage-2 phrasings with a clause (12)"; $PY -m night.head100.probe_variant night/stage2/adjudication.json $H/s2_$TAG.json
echo "== stage-2 phrasings, window listing (17)"
if [ -n "$BASE" ]; then $PY -m night.real24_window night/stage2/targets.json "s2_$TAG" "BASE=s2_$BASE" | tail -2; else $PY -m night.real24_window night/stage2/targets.json "s2_$TAG" | tail -1; fi
if [ -n "$BASE" ]; then
  echo "== paired"
  $PY night/pair_probe.py $H/s2_$BASE.json $H/s2_$TAG.json s2
  if [ "$ONLY" = full ]; then
    for k in rulerv2 c homset; do $PY night/pair_probe.py $H/${k}_$BASE.json $H/${k}_$TAG.json $k; done
    $PY night/pair_probe.py night/out/routed_$BASE.json night/out/routed_$TAG.json routed
  fi
fi
echo "== tracked files the run touched"; git status --short -- storage/ night/out/gate.json | head -5
