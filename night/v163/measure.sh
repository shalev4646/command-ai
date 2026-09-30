#!/bin/sh
# v163 — the free paired measurement of the wave (session A, 29.09), in the tree it lives in, under the v161 fly.toml
# [env] flags + RETRIEVE_RESERVE_CUE=1 (the v163 flag), HyDE forced off:
#   levers_measure vs the primary base (frozen ruler 82, head-100 dev listed / held counted, gate 431, router), c, the
#   locked homonym set, real24 (lead changes), lastq, fresh_v3 (check + aggregate), the v2 ruler and the six wave-11
#   phrasings (night/w11), each paired vs the primary base and vs every extra base tag.
#     sh night/v163/measure.sh <tag> <primary-base> <lastq-base-record.json> [extra-base ...]
#   v163a2/v163b2: primary v161r (lastq_v161r.json), extra w11base; v163b3: primary v163b2 — the wave's own df shift
#   (see RUN_LOG 18) cancels in that pair and only the added definition is left.
# Base records (night/head100/out/*_<base>.json, night/out/routed_<base>.json, night/out/real24_window_<base>.json)
# must be in the tree. Nothing here writes the corpus.
set -u
cd "$(dirname "$0")/../.." || exit 2
PY=/d/app_soldier/venv/Scripts/python.exe
REAL=${REAL_QUESTIONS:-/d/app_soldier/night/out/real_questions.json}
TAG=$1; B0=$2; LASTQ_BASE=$3; shift 3
H=night/head100/out
export PYTHONIOENCODING=utf-8 RETRIEVE_HYDE=0
export RETRIEVE_GLOSSARY=1 RETRIEVE_FULL_BLOCKS=1 RETRIEVE_ROUTER_SLOTS=2 RETRIEVE_DOC_BLOCKS=7
export RETRIEVE_FULL_BLOCK_MAX_WORDS=2000 RETRIEVE_HOMONYMS=1 RETRIEVE_QUOTELESS=1
export RETRIEVE_RESERVE_CUE=1
export RETRIEVE_LACK_CLAUSES=3 RETRIEVE_SECOND_PASS=4 RETRIEVE_SECOND_PASS_KEEP_RULING=1 ANSWER_V2=1
unset ANTHROPIC_API_KEY
echo "== levers_measure $TAG vs $B0"
$PY -m night.levers_measure "$TAG" RETRIEVE_DOC_BLOCKS=7 RETRIEVE_HOMONYMS=1 RETRIEVE_QUOTELESS=1 "BASE=$B0" 2>&1 \
  | grep -E "^\[sectprobe\]|^\[head100\]|^\[gate\]|^against|^\[levers\]|lost|gain|REGRESSION"
echo "== c"; $PY -m night.head100.probe_variant night/head100/targets_c.json $H/c_$TAG.json 2>&1 | grep -E "^\[sectprobe\] n="
echo "== locked homonym set"; $PY -m night.head100.probe_variant night/head100/homonym_targets.json $H/homset_$TAG.json 2>&1 | grep -E "^\[sectprobe\] n="
echo "== real24 vs $B0"; $PY -m night.real24_window "$REAL" "$TAG" "BASE=$B0" 2>&1 | tail -1
echo "== lastq"; $PY -m night.head100.probe_variant night/head100/targets_lastq.json $H/lastq_$TAG.json 2>&1 | grep -E "^\[sectprobe\] n="
$PY night/v163/pair_split.py "$LASTQ_BASE" $H/lastq_$TAG.json night/head100/targets_lastq.json "lastq vs $(basename "$LASTQ_BASE" .json)"
echo "== fresh_v3 (held: aggregate only)"; $PY night/fresh_v3/check.py 2>&1 | tail -1
$PY -m night.head100.probe_variant night/fresh_v3/questions.json $H/fresh3_$TAG.json 2>&1 | grep -E "^\[sectprobe\] n="
if [ -f $H/fresh3_$B0.json ]; then
  $PY -c "import json,sys; f=lambda t: sum(bool(p.get('sect_content')) for p in json.load(open(f'night/head100/out/fresh3_{t}.json',encoding='utf-8'))['per']); print(f'[fresh_v3] content {f(sys.argv[1])} -> {f(sys.argv[2])} (aggregate only)')" "$B0" "$TAG"
fi
echo "== v2 ruler"; $PY -m night.head100.probe_variant night/out/adjudication_realstyle_v2.json $H/rulerv2_$TAG.json 2>&1 | grep -E "^\[sectprobe\] n="
echo "== wave-11 phrasings (6)"; $PY -m night.head100.probe_variant night/w11/adjudication.json $H/w11_$TAG.json 2>&1 | grep -E "^\[sectprobe\] n="
$PY -m night.real24_window night/w11/targets.json "w11_$TAG" 2>&1 | tail -1
for BASE in "$B0" "$@"; do
  echo "== paired vs $BASE"
  for k in ruler c homset rulerv2 w11; do
    if [ -f $H/${k}_$BASE.json ]; then $PY night/pair_probe.py $H/${k}_$BASE.json $H/${k}_$TAG.json "$k vs $BASE"; fi
  done
  if [ -f night/out/routed_$BASE.json ]; then $PY night/pair_probe.py night/out/routed_$BASE.json night/out/routed_$TAG.json "routed vs $BASE"; fi
  if [ -f night/out/real24_window_w11_$BASE.json ]; then $PY -m night.real24_window night/w11/targets.json "w11_$TAG" "BASE=w11_$BASE" 2>&1 | tail -1; fi
done
echo "== tracked files the run touched"; git status --short -- night/out/gate.json | head -3
