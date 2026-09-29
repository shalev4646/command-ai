#!/bin/sh
# Wave-11 paired free measurement (night/w11/CRITERION.md) under the fly.toml [env] flags; runs in the tree it lives in.
#   sh night/w11/measure.sh <tag> [base-tag]
# Base records (night/head100/out/*_<base>.json, night/out/routed_<base>.json, night/out/real24_window_<base>.json,
# night/out/real24_window_w11_<base>.json) must already be in the tree.
set -u
cd "$(dirname "$0")/../.." || exit 2
PY=/d/app_soldier/venv/Scripts/python.exe
REAL=${REAL_QUESTIONS:-/d/app_soldier/night/out/real_questions.json}
TAG=$1; BASE=${2:-}
H=night/head100/out
export PYTHONIOENCODING=utf-8 RETRIEVE_HYDE=0
export RETRIEVE_GLOSSARY=1 RETRIEVE_FULL_BLOCKS=1 RETRIEVE_ROUTER_SLOTS=2 RETRIEVE_DOC_BLOCKS=7
export RETRIEVE_FULL_BLOCK_MAX_WORDS=2000 RETRIEVE_HOMONYMS=1 RETRIEVE_QUOTELESS=1
export RETRIEVE_LACK_CLAUSES=3 RETRIEVE_SECOND_PASS=4 RETRIEVE_SECOND_PASS_KEEP_RULING=1 ANSWER_V2=1
unset ANTHROPIC_API_KEY
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
echo "== wave-11 phrasings with a clause (6)"; $PY -m night.head100.probe_variant night/w11/adjudication.json $H/w11_$TAG.json
echo "== wave-11 phrasings, window listing (6)"
if [ -n "$BASE" ]; then $PY -m night.real24_window night/w11/targets.json "w11_$TAG" "BASE=w11_$BASE" | tail -2; else $PY -m night.real24_window night/w11/targets.json "w11_$TAG" | tail -1; fi
if [ -n "$BASE" ]; then
  echo "== paired"
  for k in w11 rulerv2 c homset; do $PY night/pair_probe.py $H/${k}_$BASE.json $H/${k}_$TAG.json $k; done
  $PY night/pair_probe.py night/out/routed_$BASE.json night/out/routed_$TAG.json routed
fi
echo "== tracked files the run touched"; git status --short -- storage/ night/out/gate.json | head -5
