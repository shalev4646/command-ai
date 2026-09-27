#!/bin/sh
# Wave-8 measurement under the v158 flags (K=7 + HOMONYMS + QUOTELESS). Usage: sh gates_k7.sh <tag> [BASE]
set -u
cd /d/_run_wt || exit 2
PY=/d/app_soldier/venv/Scripts/python.exe
TAG=$1; BASE=${2:-}
export PYTHONIOENCODING=utf-8 RETRIEVE_HYDE=0
export RETRIEVE_GLOSSARY=1 RETRIEVE_FULL_BLOCKS=1 RETRIEVE_ROUTER_SLOTS=2 RETRIEVE_DOC_BLOCKS=7
export RETRIEVE_FULL_BLOCK_MAX_WORDS=2000 RETRIEVE_HOMONYMS=1 RETRIEVE_QUOTELESS=1
export RETRIEVE_LACK_CLAUSES=3 RETRIEVE_SECOND_PASS=4 RETRIEVE_SECOND_PASS_KEEP_RULING=1 ANSWER_V2=1
unset ANTHROPIC_API_KEY
echo "== levers_measure $TAG"
if [ -n "$BASE" ]; then $PY -m night.levers_measure "$TAG" RETRIEVE_DOC_BLOCKS=7 RETRIEVE_HOMONYMS=1 RETRIEVE_QUOTELESS=1 "BASE=$BASE" 2>&1 | grep -E "^\[sectprobe\]|^\[head100\]|^\[gate\]|^against|^\[levers\]|lost|gain"
else $PY -m night.levers_measure "$TAG" RETRIEVE_DOC_BLOCKS=7 RETRIEVE_HOMONYMS=1 RETRIEVE_QUOTELESS=1 2>&1 | grep -E "^\[sectprobe\]|^\[head100\]|^\[gate\]|^against|^\[levers\]"; fi
echo "== c (38)"; $PY -m night.head100.probe_variant night/head100/targets_c.json night/head100/out/c_$TAG.json 2>&1 | grep -E "^\[sectprobe\]"
echo "== locked homonym set (12)"; $PY -m night.head100.probe_variant night/head100/homonym_targets.json night/head100/out/homset_$TAG.json 2>&1 | grep -E "^\[sectprobe\]"
echo "== real24"; if [ -n "$BASE" ]; then $PY -m night.real24_window night/out/real_questions.json "$TAG" "BASE=$BASE" 2>&1 | tail -30; else $PY -m night.real24_window night/out/real_questions.json "$TAG" 2>&1 | tail -5; fi
echo "== info: vs merged (K=6, same corpus minus the two)"; $PY -m night.head100.compare merged "$TAG" 2>&1 | tail -8
echo "== storage status"; git status --short -- storage/ | head -3
