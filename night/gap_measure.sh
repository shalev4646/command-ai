#!/bin/sh
# Paired free measurement for night/GLOSSARY_GAPS_CRITERION.md under the v161 production flags
# (fly.toml [env]; HyDE off, no API key). Runs in the tree this script lives in.
#   sh night/gap_measure.sh <tag>            # the base
#   sh night/gap_measure.sh <tag> <base-tag> # the treatment, paired against the base
# REAL_QUESTIONS points at the gitignored real-question file (default: the production tree's copy).
set -u
cd "$(dirname "$0")/.." || exit 2
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
echo "== v2 ruler (45)"; $PY -m night.head100.probe_variant night/out/adjudication_realstyle_v2.json $H/rulerv2_$TAG.json
echo "== c (38)"; $PY -m night.head100.probe_variant night/head100/targets_c.json $H/c_$TAG.json
echo "== locked homonym set (12)"; $PY -m night.head100.probe_variant night/head100/homonym_targets.json $H/homset_$TAG.json
echo "== gap phrasings with a clause (14)"; $PY -m night.head100.probe_variant night/glossary_gap_adjudication.json $H/gap_$TAG.json
if [ -n "$BASE" ]; then
  echo "== gap phrasings, window listing (16)"; $PY -m night.real24_window night/glossary_gap_targets.json "gap_$TAG" "BASE=gap_$BASE" | tail -3
  echo "== real24"; $PY -m night.real24_window "$REAL" "$TAG" "BASE=$BASE" | tail -2
  echo "== paired"
  for k in rulerv2 c homset gap; do $PY night/pair_probe.py $H/${k}_$BASE.json $H/${k}_$TAG.json $k; done
  $PY night/pair_probe.py night/out/routed_$BASE.json night/out/routed_$TAG.json routed
else
  echo "== gap phrasings, window listing (16)"; $PY -m night.real24_window night/glossary_gap_targets.json "gap_$TAG" | tail -1
  echo "== real24"; $PY -m night.real24_window "$REAL" "$TAG" | tail -1
fi
echo "== tracked files the run touched"; git status --short -- storage/ | head -5
