# head-100 — יומן הריצה בתשלום (סשן א', 22.09.2026)

ענף `claude/paid-run` (מ-main `fc70cc3` + הקריטריון `23e90c2` מ-`claude/head100`), עץ `D:/_run_wt`.
התוכנית והקריטריון: `PAID_RUN_PLAN.md` (נכתבו לפני שקיימת תשובה אחת; לא ישתנו).
הדלתות על העץ: `claude/doors` = `2bb4115` (ממוזגות ב-main; שכבה אחרי התשובה — אינן נמדדות בריצה).

## 0. העץ

- `git worktree add D:/_run_wt -b claude/paid-run main` — `fc70cc3`, נקי; מיזוג `claude/head100` (מסמך בלבד) ⇒ `81951b7`.
- הקורפוס = הקורפוס של הפריסה: `git diff --stat 21b1b8d main -- storage/ backend.py` ריק. 20 פקודות `digits_fixed`, 294 מסמכים.
- ליג'ר בעץ: $136.75 / $145, reserved 0 ⇒ $8.25.
- פרודקשן בזמן הריצה (22.09, נקרא בלבד): `flyctl releases` — `v155 │ complete │ Release │ shalev4793@gmail.com │ Sep 19 2026 21:14` (v154 20:45, v153 10:26); מכונה `48e4659f039578` גרסה 155, fra, started, 1/1 checks, image `deployment-01M2XPM87C1ANSFMG522MQ8Z6E`. `flyctl config env` — 13 המשתנים זהים ל-`fly.toml` של main: `CAI_SW=1 RETRIEVE_HYDE=1 RETRIEVE_GLOSSARY=1 RETRIEVE_FULL_BLOCKS=1 RETRIEVE_ROUTER_SLOTS=2 RETRIEVE_SECOND_PASS=4 RETRIEVE_DOC_BLOCKS=6 RETRIEVE_LACK_CLAUSES=3 RETRIEVE_SECOND_PASS_KEEP_RULING=1 ANSWER_V2=1 SYSTEM_CACHE_TTL=5m RETRIEVE_FULL_BLOCK_MAX_WORDS=2000 DOC_SCAN_TTL_SEC=5`. אלה גם הדגלים של p1/p2 כאן.
- נתיב-התשובה בעץ זהה לפרודקשן: `git diff 5dcbff7..HEAD` על `backend.py scope_routes.py common.py metrics.py storage/ night/probe.py night/head100/arm.py sample_A.json night/grade.py` — ריק. (הדלתות נגעו רק ב-`out_of_scope.py`, `report_goal.py`, `doorgate.py` ובבדיקות; `scope_routes.py`, שכן נכנס לפרומפט, לא זז.)
- המדגם `sample_A.json`: 50 (40 עם יעד + 10 בלי פקודה), קבוצות L5 D6 R5 A3 M5 W5 C2 S3 Res4 Cmd2 + G10, תפקידים soldier 44 / reserve 4 / commander 2 — קומיט `02af605` מ-18.09, לפני שקיימת תשובה.
- `.env` מועתק **רק אחרי** השערים החינמיים (כדי ששום מכשיר חינמי לא יוכל לשלם).

## 1. שערים חינמיים (דגלי-הפרודקשן, HyDE כבוי) — מול הרשומה `w6cap`

קו-הבסיס (19.09, `w6cap`): שער 390/431 · סרגל 30/82 סעיף, 36/82 פקודה · head-100 148/174 (dev 76, held 72) ·
מכשיר-עם-נתב 37/82, 12/20 (`sect7_routed_prodflags.json`). קריטריון: אפס אבודים, אפס נופלות חדשות.

- חבילת-הבדיקות `tests/run_all.py` בעץ (בלי `.env`, בלי רשת): **71/71 קבצים, 342 שניות** — אפס נופלות (גם ששת כשלי-huggingface לא הופיעו הפעם).

- שערי-האחזור (22.09, `RETRIEVE_HYDE=0`, שאר הדגלים כמו fly.toml כולל `FULL_BLOCK_MAX_WORDS=2000`), תג `run1`,
  `night.head100.compare w6cap run1`:

| מכשיר | w6cap (19.09) | run1 (22.09) | אבודים / נוספים |
|---|---|---|---|
| `night.gate` | 390/431 | **390/431** | 0 נופלות חדשות, 0 עוברות חדשות (אותן 4 adversarial) |
| הסרגל הקפוא — סעיף (תוכן) / פקודה | 30/82 / 36/82 | **30/82 / 36/82** | 0 / 0 |
| head-100 dev / held | 76/87 / 72/87 | **76/87 / 72/87** | 0 / 0 (held — מצרפי בלבד) |
| מכשיר-עם-נתב מול `sect7_routed_prodflags` | 50 פקודות · 37 סעיפים · 12/20 אמיתי | **50 · 37 · 12/20** | lost 0, gained 0 (קוד-יציאה 0) |
| חלון (סרגל / head-100 / עם-נתב) | — | 3,039 / 3,105 / חציון 3,134 מילים | |

⇒ העץ משחזר את הרשומה במדויק; תנאי-הקדם 2 של `PAID_RUN_PLAN.md` מתקיים. קבצים: `out/gate_run1.json`,
`out/ruler_run1.json`, `out/run1.json`, `night/out/sect8_run1.json`.

## 2. `arm dry` — המחיר (22.09, חינם, על העץ הזה, דגלי-הפרודקשן)

```
[arm] dry: 50 questions, window mean 3014 words (max 4689)
[arm]   second pass on 60%: pass1 $2.23 + pass2 $1.34 + compose $0.80 + grading ~$0.15  =  ~$4.52
[arm]   second pass on 87%: pass1 $2.23 + pass2 $1.95 + compose $0.94 + grading ~$0.15  =  ~$5.27
```

זהה למדידת 19.09 על עץ wave-6 עם התקרה (3,009 / 4,689; $4.5–5.3) — החלונות לא זזו מאז. נותרו בליג'ר $8.25 ⇒
מרווח $3 מעל ההערכה הגבוהה; התקרה ($145) עוצרת לפני חריגה. **ההזמנה יוצאת רק על „לך" מפורש של המשתמש בצ'אט
שלי, אחרי שהמספר הוצג לו, ואחרי שאישר שתקרת-החודש בקונסול מספיקה לריצה ולפרודקשן יחד.**

## 2ב. השאלה החיה לאימות המחיר אחרי `SYSTEM_CACHE_TTL=5m` (22.09 09:17Z, באישור המשתמש בצ'אט)

הזרוע: פרודקשן v155, ממשק האפליקציה (שם „בודק", תיבת-התנאים סומנה בידי הסשן באישור המשתמש, כניסת חיילים).
השאלה המזווגת מ-18.09: „מותר למפקד לטרטר אותנו כעונש?" — מעבר אחד, מטמון קר (`cache_read` 0).
השורה מ-`/app/storage/metrics_log.jsonl` (SSH, מיד אחרי התשובה):

```
ts 2026-09-22T09:17:28 · session b06f09d08db6 · role soldier · doc_ids 33.0351, PM-33.0302, CHOK-SHIPUT-1955, 33.0350, PM-33.0307, PM-33.0333, 33.0145
input_tokens 14077 · cache_read 0 · cache_write 5316 · output_tokens 861 · cost_usd 0.12513 · latency_s 28.0 · refused false
```

חישוב: 14,077×$5 + 861×$25 + 5,316×$6.25 (כתיבת-5m, 1.25×) = $0.1251 — הנוסחה החדשה ביומן = המחיר בפועל.
**מול קו-הבסיס 18.09 (מטמון-שעה: $0.124 רשום / $0.144 בפועל): $0.125 בפועל ⇒ −13% על שאלה קרה במעבר אחד; התחזית „~$0.12" אומתה.**
התשובה: „✗ אסור" מפ״מ 33.0351 „תרגול נוסף" סעיף 7 (+ סעיפים 2, 5, 6, 8), רצועת „למי פונים": מפקד ישיר ← מש"קית ת"ש ← נציב קבילות החיילים.
היומן לא התאפס מאז v155 — שורה קודמת מ-20.09 11:31 (חופשת שחרור, $0.163, 22,413 טוקני-קלט).

## 3. p1 → p2 → grade → report

- **תקרת הליג'ר 145 → 155** (22.09, המשתמש בצ'אט: „תעלה את התקרה אני מאשר"; `night/ledger.py`, קומיט `8e34bf2`).
- **p1 הוזמן 22.09 09:26Z** מ-`D:/_run_wt` עם 13 דגלי-הפרודקשן (HyDE דלוק): הרכבה 50/50 בלי `RetrievalDegraded`;
  batch `msgbatch_017WNhTj8hMuPD3epChGH8Cn`, 50 בקשות, reserve $1.40 ($16.85 נותרים אחריו); כרטיס-התביעה
  `night/out/batch_probe-head100A_p1.json` (מקומט בכוח). ההרכבה (HyDE+נתב ≈ $0.8–0.9) אינה נרשמת בליג'ר.
  התאוששות אם הסשן נופל: `python -m night.collect probe-head100A_p1`.

_(p2 / grade / report — ממלא)_

## 4. עיון שלב 6

_(ממלא)_
