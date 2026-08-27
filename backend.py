import json
import os
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

import scope_routes
from common import ROLES, safe_print
from metadata_overrides import apply_overrides
from storage.vector_store import retrieve
from storage import glossary as _glossary

load_dotenv(Path(__file__).parent / ".env")

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", "").strip())

MODEL = "claude-opus-4-8"
# Ceiling for thinking + answer combined. Adaptive thinking spends a few
# thousand tokens on table/legal reasoning before the ~1K-token structured
# answer; streaming means the large cap carries no HTTP-timeout risk.
MAX_OUTPUT_TOKENS = 8000

# Follow-up query rewriting runs on Haiku: it fires before every retrieval
# in an ongoing conversation, so it must be fast and cheap (~0.2s, well
# under a tenth of a cent), and turning chat context into a standalone
# search query is well within its reach.
REWRITE_MODEL = "claude-haiku-4-5-20251001"

# Hard cap on how many retrieved chunks are stitched into the prompt. Kept
# deliberately small: the top few clauses carry the answer, and every extra
# chunk inflates prompt tokens (cost + latency) and erodes the per-request
# rate-limit budget when many soldiers query at once. 8 leaves room for the
# leading order's guaranteed depth (top_doc_depth=4) plus 4 other orders —
# raised from 6 when the key-facts clauses added per order started crowding
# the basic raw-text content out of the leading order's slots.
MAX_CONTEXT_CHUNKS = 8

# Serve only orders that carry a curated key-facts block. Measured on
# 2026-08-16 (night/remeasure, paired, 30 questions / 63 frozen parts): wave 2
# grew the corpus 124 -> 289 and answered parts FELL 20 -> 13. In all seven
# questions that regressed, newly-indexed orders pushed the ones that had been
# answering out of the 8-chunk context, and 6 of the 17 intruders had no
# key-facts at all — a raw chunk displacing a curated block. Uncurated orders
# are a cost, not coverage: they crowd the context and, when they win a slot,
# hand the model raw text that answers nothing.
#
# Measured the same day with the flag on, same 30 questions, same corpus:
# answered parts 13 -> 17 and the gate 387 -> 391 (+6 fixed, -2 broken, both
# breaks 3.0502 — the one order pulled by design for unverifiable digits). Not
# a full recovery to the 124-order baseline of 20 — the rest is curated wave-2
# orders outranking the veterans that answered — but better than off on both
# instruments, so on is the default. RETRIEVE_CURATED_ONLY=0 turns it off.
RETRIEVE_CURATED_ONLY = os.environ.get("RETRIEVE_CURATED_ONLY", "1") == "1"


class RetrievalDegraded(RuntimeError):
    """A retrieval helper failed while RETRIEVE_STRICT was on."""


# Production degrades quietly when a helper call dies — `_route_docs` says so in
# its own docstring, and that is right: a soldier gets a slightly worse answer
# instead of none. A MEASUREMENT must do the opposite. On 2026-08-27 an arm was
# composed 100/100 while the credit balance was exhausted, with the hypothetical
# and the router failing on every single question; had the batch submitted, the
# whole difference against its paired arm would have been attributed to the flag
# under test. Only the submit failing on credit stopped it.
#
# So: off by default, and the night harness turns it on. A run on a broken
# pipeline then dies at the first question instead of composing a plausible lie.
RETRIEVE_STRICT = os.environ.get("RETRIEVE_STRICT", "0") == "1"

# Add chunks retrieved with a HYPOTHETICAL ANSWER — Haiku writing what a
# פקודת מטכ"ל would say — on top of the question's own eight.
#
# Measured 2026-08-17 on night/evidence, 16 questions whose answering sentence
# an adjudicator located verbatim in raw_text:
#
#   query for retrieval          answering order in window   sentence served
#   the question (production)          4/16                   3/91 spans
#   the question, typo-free            4/16                   —
#   the answering sentence            16/16                   —   (oracle)
#   a hypothetical order               7/16                   7/91 spans
#
# The oracle row is why this exists and the typo-free row is why the query
# cannot be repaired: a soldier's question and a clause of military prose are
# far apart in this embedding space no matter how the question is phrased, so
# the fix has to change what we search WITH, not how the question is written.
# This is not `night.rewrite` (question -> better question, 345/415, rejected):
# that transformation keeps both sides of the asymmetry in place.
#
# ADDITIVE, never a tenant. Three slot-taking variants were measured on the 415
# gate cases and all cost the same 12-13 top-3 placements to rescue 5, whatever
# they took (1 chunk or 3). The control settles why: the question's own
# retrieval truncated to seven costs NOTHING (390/415, 0 broken), so the damage
# was never the lost slot — it was inserting a foreign order above the expected
# one. Appending instead leaves the first eight untouched: 390/415 top-3 with
# zero broken, and the order reaches the model in 408 cases against 407.
#
# Costs a Haiku call (~$0.0012) and ~1.5s of latency per question, plus ~180
# words of context. Default OFF until the paired re-measure says the ANSWERS
# move — reaching the model is necessary, not sufficient.
RETRIEVE_HYDE = os.environ.get("RETRIEVE_HYDE", "0") == "1"
HYDE_EXTRA_CHUNKS = int(os.environ.get("HYDE_EXTRA_CHUNKS", "1"))

# Hand the model the WHOLE curated block of the leading orders, not the one or
# two clauses that happened to rank.
#
# Measured 2026-08-17/18 on the same 16 evidence targets. Once HyDE and the
# router bring the answering order into the window (8 of 16), the sentence
# that answers is still served in only 7 of 91 spans — and the reason is not
# raw text: the CURATED block of the answering order contains the answer in
# 12 of 16 (7 verbatim, 5 paraphrased). PM-33.0302 has 25 curated clauses in
# 8 chunks and the window takes one or two of them, and the one that answers
# is not among them. Ranking the clauses against the question does not fix it
# (3-4 ranked clauses: 7-8 of 16) — the answering clause is not the one that
# resembles the question, which is the same question/answer asymmetry that
# motivated HyDE, at clause resolution. `vector_store.retrieve` already calls
# this a coin flip. Serving the whole block does: 5 -> 14 of 16 questions
# covered, at 1,160 -> 2,970 words per question (~+$0.01 at Opus prices).
#
# ⚠ MEASURED AND NOT RECOMMENDED. The 14/16 came from a coverage metric that
# scored word-recall against the WHOLE context, and at 33 chunks per question
# (3,000 words) six of every ten words in any sentence are somewhere in there —
# the metric was measuring context size. Re-scored per chunk, the honest
# reading of the same run is 6/16 against 5/16 for HyDE alone, at 4x the
# context. Kept behind the flag because the mechanism is real (PM-33.0302's
# answering clause IS in its block and IS the one the window drops), but the
# lever has to be selective — a per-order full block where the router and HyDE
# agree, not every leading order — and that is not built or measured yet.
#
# Appended after everything else, so the first eight (+HyDE +router) and their
# order are untouched: gate 387/415 identical with the extension on or off.
# The block is served per ORDER, deduplicated against chunks already present.
#
# The router slot came out of the same measurement: on the 16 the router names
# the answering order in 7 and HyDE reaches it in 8, union 10 — four of the
# router's hits are ones HyDE misses, and today its verdict is only a +0.05
# bonus that cannot lift an order across a 0.15 gap. Two slots for its top
# picks, appended: 6 -> 8 of 16 orders in the window.
#
# Both default OFF until the paired re-measure says the ANSWERS move.
RETRIEVE_ROUTER_SLOTS = int(os.environ.get("RETRIEVE_ROUTER_SLOTS", "0"))
RETRIEVE_FULL_BLOCKS = int(os.environ.get("RETRIEVE_FULL_BLOCKS", "0"))

# The ceiling clause, for questions that ask for a ceiling.
#
# Measured free on 2026-08-23 against the three zeros the arbitration called
# "the block answers and retrieval did not bring it". In two of the three the
# ANSWERING ORDER WAS ALREADY IN THE WINDOW and the answering clause was not:
#   q00020 "מה המקסימום שאני יכול להוציא על חייל"  — 35.0115 sits at rank 4
#     with one chunk; its ceilings clause (15/30/50 אחוזים משכר טוראי, ten
#     private's salaries) never entered the window at all.
#   q00081 "מה הענישה המקסימלית על אי ציות לפקודה" — PM-33.0302 holds seats
#     2, 3 and 5; the מחבוש ceiling is in none of them.
# RETRIEVE_FULL_BLOCKS does not rescue either, and the same run showed why:
# it serves the block of the order owning the FIRST chunk, and in both cases
# that order is irrelevant (31.0117 "שחרור שלא בפניו" holds 8 of q00020's 11
# seats; 8.0101 "פניות גורמים אזרחיים" leads q00081). Embedding similarity
# ranks a clause by how much it sounds like the question, and a table of
# numbers sounds like nothing.
#
# So: when the question asks HOW MUCH, append the clauses that carry amounts
# from the orders already in the window. Deterministic, no model call, ~100-200
# words, and it cannot move a ranking — it only appends, so the retrieval gate
# is untouched by construction.
RETRIEVE_QUANTITY_CLAUSES = int(os.environ.get("RETRIEVE_QUANTITY_CLAUSES", "0"))

# "How much / how many / what is the maximum" — the demand, not the topic.
# Deliberately narrow: "כמה" alone would fire on "כמה שיותר מהר" and on any
# question that merely contains the word.
_QUANTITY_DEMAND = re.compile(
    r"מקסימו[םמ]|מקסימלי|מרבי|תקרה|תקרת|לכל\s+היותר|עד\s+כמה|"
    r"\bכמה\s+(?:ימים|זמן|כסף|שעות|חודשים|אחוז|עולה|מגיע|יכול|מותר|צריך|אפשר)|"
    r"מה\s+ה(?:סכום|גובה|שיעור|מקסימום|תקרה)")

# A clause "carries an amount" when a number sits next to a unit. A bare digit
# is not enough — clause numbers, order numbers and dates are digits too, and
# the curated blocks of scrambled-digit orders are written without numerals at
# all (they are skipped here, correctly: they have no ceiling to serve).
_AMOUNT = re.compile(
    r"\d[\d,.]*\s*(?:ימים|יום|שעות|שעה|חודשים|חודש|שנים|שנה|אחוז|%|"
    r"ש\"?ח|שקלים|משכורות|משכורת|מ\"?ר)|"
    r"(?:עד|לכל\s+היותר|לא\s+יעלה\s+על|לא\s+יותר\s+מ)\s*\S{0,12}\d")

# Header that marks the retrieved-context section inside a user turn. The
# context rides in the user message (not the system prompt) so the system
# prompt and past turns stay byte-identical across a conversation — the
# stable prefix the API's prompt cache needs.
_CONTEXT_HEADER = "קטעים רלוונטיים מהפקודות:"

# History trimming happens in whole-exchange jumps, not as a rolling cap:
# a window that slides every turn changes the request prefix every turn and
# never hits the prompt cache. Dropping 3 exchanges at a time once 6 have
# accumulated costs one cache miss every 3 turns instead of every turn.
_HISTORY_MAX = 12   # messages (6 exchanges) before a trim
_HISTORY_DROP = 6   # messages (3 exchanges) dropped per trim

_COMMON_RULES = """חוקים מוחלטים:
1. ענה אך ורק על בסיס הקטעים שסופקו לך בהקשר.
2. אם אין בקטעים כלל שחל ישירות על המצב שנשאל — פתח באמירה המדויקת: "המידע לא קיים בפקודות שסופקו." מותר להוסיף אחריה מה כן קיים בקטעים (כלל שחל רק על הקשר אחר או צר יותר), תוך ציון מפורש שההקשר שונה.
   אם יש כלל שחל ישירות על המצב אך אינו נוקב בערך המדויק שנשאל (שעה, סכום, מספר ימים) — אל תסתפק בסירוב: הצג את הכלל כלשונו, הסבר מה נובע ממנו לשאלה, וציין במפורש מה הפקודות לא קובעות.
2ב. **לפני שאתה מסיים — פרק את השאלה לחלקים שלה וּודא שכל חלק קיבל מענה או הוכרז כחסר.**
   שאלה של חייל מכילה לרוב יותר מדבר אחד ("תוך כמה זמן, ומי מאשר"), והכשל השכיח כאן אינו
   סירוב אלא **שתיקה**: תשובה שעונה על החלק שיש עליו חומר, ולא אומרת מילה על החלק השני.
   החייל קורא תשובה בטוחה ואינו יודע ששאל שני דברים וקיבל אחד.
   לכן: כל חלק שלא הוכרע מהקטעים חייב להיאמר **במפורש ובמילים שלו** — "הפקודות אינן קובעות
   מי מאשר את זה" — ולא להישמט. זה חל גם כשהחלק שכן נענה נענה היטב.

2א. **"אין תשובה" לעולם אינו מבוי סתום.** כל תשובה שלא הצליחה להכריע מהפקודות את השאלה **או חלק ממנה** — בין שהיא נפתחת במשפט הסירוב ובין שהיא עונה על חלק ומודה שחלק אחר אינו בקטעים — חייבת להסתיים באחת משתי השורות האלה, אחת בדיוק, כשורה האחרונה בתשובה, ומתייחסת לחלק שלא נענה:
   • כשהנושא מעצם טבעו אינו מוסדר בפקודות מטכ"ל אלא במסגרת אחרת — כתוב `{MARK_OUT}` ואחריו **שם המסגרת בעברית, מועתק מילה במילה מהרשימה שלהלן**, ואחריו משפט אחד לאן פונים. בחר אך ורק מהרשימה; אל תמציא גוף, חוק, אתר או מספר טלפון שאינם כתובים בה.
   • כשהנושא כן מסוג העניינים שפקודת מטכ"ל מסדירה, אך הכלל אינו בקטעים שקיבלת — כתוב `{MARK_MISS}` ואחריו **תיאור הנושא במילים בלבד**. בשורה הזאת אסור בהחלט לציין מספר, שם, סימוכין או הפניה של פקודה, הוראת קבע או כל מסמך אחר — גם לא בסוגריים ולא כניחוש: אינך יודע איזו פקודה מסדירה את מה שאין בידך, וציון סימוכין שאינו לפניך הוא המצאה.
   המסגרות המותרות:
{ROUTE_BLOCK}
   הכרעה בין השתיים: שאל "האם פקודת מטכ\"ל היא בכלל הכלי שמסדיר את זה?" — תגמול כספי מביטוח לאומי, תביעה אזרחית, מה שקבוע בחוק ראשי, נוהג יחידתי לא-כתוב ושאלות על אכיפה בפועל אינם פקודתיים; זכאות, סמכות מפקד, נוהל צבאי ומשמעת כן.
   בתשובה שהכריעה את **כל** מה שנשאל אין לשורות האלה מקום — אל תוסיף אותן בשביל סייג תיאורטי שאיש לא שאל עליו. המבחן הוא אם השואל נשאר בלי מענה על משהו ששאל בפועל.
   ⛔ רשימת המסגרות משמשת **אך ורק** לשורה של כלל 2א. אל תשאב ממנה כתובות להמלצת הסיום של כלל 5 ואל תזכיר גורם מתוכה בתשובה שהכריעה את השאלה — המלצת הסיום נגזרת מהפקודה שציטטת ומשרשרת הפיקוד, לא מהרשימה הזאת.
3. אל תשתמש בידע כללי על הצבא.
4. כל תשובה חייבת לכלול ציטוט מדויק + מספר סעיף + שם הפקודה.
5. לכל שאלה שיש לה שורה תחתונה נורמטיבית — פתח את התשובה בשורת **פסיקה:** שמתחילה במונח הפסיקה עצמו (מותר / אסור / זכאי / פטור / חייב / מוסמך / רשאי), גם אם השאלה לא נוסחה כ"האם מותר לי". מונח הפסיקה יכול לשאת סייג קצר (למשל "**פסיקה:** אסור בתנועה רגלית" או "**פסיקה:** מותר בתנאים"). כשמונח פסיקה חיובי (מותר / זכאי / רשאי / מוסמך / ניתן) חל רק בהתקיים תנאים, חריגים או אישור — כתוב תמיד את המונח בצורת "בתנאים" ("מותר בתנאים", "זכאי בתנאים"), ולעולם לא מונח חיובי חשוף שאחריו סייג מצמצם ("מותר — אך ורק...", "מותר רק אם..."). אל תפתח ב"כן"/"לא" ואל תשתמש ב"**תשובה:**" אלא בשאלות עובדתיות (מה הנוהל, מה עושים, איך, כמה, מתי — גם כשהתשובה נוגעת בסמכויות). כשמונח הפסיקה הוא סמכות (מוסמך / רשאי) — לעולם אל תשאיר אותו חשוף: צרף אליו את מושא הסמכות ("**פסיקה:** מוסמך להטיל עד 7 ימי ריתוק"). הפסיקה מכריעה את השאלה שהשואל שאל — לא עניין-משנה שעלה אגב המענה: כשהשואל מתאר מעשה של אדם אחר ושואל עליו ("האם הוא יכול לאיים עליי?") — פסוק על המעשה ההוא ("**פסיקה:** אסור — איום על מפקד הוא עבירה"). עניין-משנה דיוני — למי מגישים תלונה, מי מוסמך לדון, לאן פונים — לעולם אינו סעיף פסיקה ויפורט בגוף התשובה בלבד.
   אבל התנהלות הצד שכנגד בעימות אינה עניין-משנה. כששני תנאים מתקיימים — (א) השאלה מתארת עימות בין שני צדדים (השואל עצמו או דמויות שתיאר); (ב) השאלה מתארת גם מעשה או אמירה של הצד שכנגד, הצד שהשאלה המפורשת אינה עליו — חובה ששורת הפסיקה תכיל שני סעיפים מופרדים בנקודה-פסיק: הראשון מכריע את השאלה שנשאלה; השני מכריע על התנהלות הצד שכנגד, גם כשלא נשאלה עליה שאלה מפורשת — מי שתיאר עימות רוצה לדעת היכן שני הצדדים עומדים, ואסור להשאיר את ההכרעה על הצד שכנגד בגוף בלבד. כל סעיף קצר: מונח פסיקה + סייג של עד ארבע מילים; ההסברים והציטוטים בגוף. אם אין בקטעים כלל החל על התנהלות הצד שכנגד — הסעיף השני נכתב בתבנית קבועה: "לא נמצאה בפקודות הפרה בהתנהלות <הצד שכנגד>" (למשל: "לא נמצאה בפקודות הפרה בהתנהלות האחראית"; עד שמונה מילים). משפט שלם וקריא — לא צירוף סמיכויות מעורפל ("עילה באיחור האחראית") ולא ניסוח ביורוקרטי ("לא נמצאה מגבלה על התנהלות X"). בשאלה דו-צדדית סדר הגוף קבוע: מיד אחרי **מקור:** יבואו שתי שורות הכרעה בלשון פשוטה — שורה נפרדת לכל צד, בשתי פסקאות נפרדות, כל אחת נפתחת בתווית מודגשת של הצד ומסתיימת במסקנה המעשית עבורו:
"**התנהלות החייל:** דרש מחליף ואיים במשפט כדי להשיג את מבוקשו — אינה לגיטימית; איום אינו דרך פעולה מותרת."
"**התנהלות האחראית:** הודיעה כי היא מטפלת וכי הדבר ייקח זמן — אינה מקימה לחייל כל עילה לאיים; משמע האחראית מוגנת מפני האיום." רק אחריה — ציטוטים, סייגים ומה שהפקודות לא קובעות. את התשובה חותם משפט אחד לכל היותר של המלצה מעשית **לשואל עצמו, לפי תפקידו** (למפקד: "מומלץ לך לתעד/להעביר ל..."; לחייל: "עומד לרשותך..."), בלי ציטוט — לא הנחיה לצד שכנגד: מפקד ששאל אינו זקוק להסבר מה החייל צריך לעשות. למשל: "סירבתי לפקודת ניקיון והמ"כ קילל אותי מול כולם — מותר לו?" → "**פסיקה:** אסור — למפקד להעליב חייל; חייב — לציית לפקודה" (הסעיף השני מכריע על הסירוב שהשואל תיאר, אף שלא נשאל עליו). כששני התנאים אינם מתקיימים — סעיף פסיקה אחד בלבד; אל תוסיף סעיף שני על עניין-משנה דיוני, על גורם שאינו צד לעימות, או כשהתנהלות הצד שכנגד לא תוארה.

כלל תמציתיות (חל על כל תשובה): ציטוט מהפקודה מביא רק את המשפט האופרטיבי הנחוץ להכרעה — לא פסקאות שלמות. כל תנאי ועובדה מופיעים בתשובה פעם אחת בלבד: מה שפורט ברשימת התנאים לא חוזר בגוף ולא בסיכום. "מה הפקודות לא קובעות" — משפט אחד לכל היותר, ורק כשהוא משנה משהו לשואל. המלצת הסיום — משפט אחד. בלי פתיחים ("חשוב לציין", "שים לב") ובלי משפטי מעבר; עדיף שורת רשימה קצרה על פסקה. היעד: תשובה שלמה בעובדות וחסכונית במילים — כל משפט שאינו מוסיף עובדה, תנאי או מקור נמחק."""

# Rule 2א's placeholders are substituted here, not by an f-string: _COMMON_RULES
# is interpolated INTO the persona f-strings, and f-string interpolation does not
# recurse into the inserted value — `{MARK_OUT}` would reach the model verbatim.
# .replace() also sidesteps str.format() choking on any brace elsewhere in the text.
_COMMON_RULES = (
    _COMMON_RULES
    .replace("{MARK_OUT}", scope_routes.MARK_OUT_OF_SCOPE)
    .replace("{MARK_MISS}", scope_routes.MARK_MISSING)
    .replace("{ROUTE_BLOCK}", scope_routes.prompt_block())
)
assert "{MARK_OUT}" not in _COMMON_RULES and "{ROUTE_BLOCK}" not in _COMMON_RULES

# The two-sided ruling template rides inside every persona's structure
# block: the model obeys the מבנה-תשובה templates more reliably than prose
# rules, and rule 5's two-sided mandate needs that reinforcement (the
# 2026-07-27 live smokes showed the prose rule alone loses to the older
# "יפורט בגוף" instruction and the ruling stays one-sided).
_TWO_SIDED_TEMPLATE = """מבנה תשובה לשאלת עימות דו-צדדית (שני התנאים שבכלל 5 מתקיימים):
**פסיקה:** מונח פסיקה על השאלה שנשאלה; מונח פסיקה על התנהלות הצד שכנגד
**מקור:** [שם הפקודה] סעיף X"""

SYSTEM_PROMPT_SOLDIER = f"""אתה עוזר צבאי המסייע לחיילים להבין את זכויותיהם האישיות לפי פקודות מטכ"ל.
אתה פונה אל החייל בגוף שני, ומתמקד במה שמותר/אסור/מגיע לו כפרט — לא בשיקולי פיקוד.

{_COMMON_RULES}
6. התמקד בזכויות החייל, בתנאים למימושן, ובמה עומד לרשותו אם הזכות הופרה.

מבנה תשובה לשאלות "האם מגיע לי / מותר לי X?":
**פסיקה:** מותר / אסור / מגיע לי בתנאים
**מקור:** [שם הפקודה] סעיף X
**תנאים:** רשימה מפורטת
**מי מאשר:** דרגה נדרשת

{_TWO_SIDED_TEMPLATE}

מבנה תשובה לשאלות עובדתיות:
**תשובה:** תשובה ישירה
**מקור:** [שם הפקודה] סעיף X
"""

SYSTEM_PROMPT_COMMANDER = f"""אתה עוזר צבאי המסייע למפקדים להפעיל את סמכויותיהם הפיקודיות לפי פקודות מטכ"ל.
אתה פונה אל המפקד בגוף שני, ומתמקד בסמכויות אישור, בנהלי ענישה ובאחריות פיקודית — לא בזכויות אישיות של הפרט.

{_COMMON_RULES}
6. התמקד בסמכויות המפקד: מה הוא רשאי לאשר או לשלול, אילו עונשים מותר לו להטיל ובאילו תנאים, ומה חובות הדיווח/התיעוד שלו. כשמפקד שואל על זכות אישית שלו (לא על סמכות) — ענה לגופה באותם כללים.

מבנה תשובה לשאלות "האם אני רשאי לאשר/לשלול X?":
**פסיקה:** מוסמך / לא מוסמך / מוסמך בתנאים
**מקור:** [שם הפקודה] סעיף X
**דרגה נדרשת לאישור:** (אם שונה מדרגת המפקד השואל)
**תנאים / הגבלות:** רשימה מפורטת

{_TWO_SIDED_TEMPLATE}

מבנה תשובה לשאלות עובדתיות (נהלים, ענישה, דיווח):
**תשובה:** תשובה ישירה
**מקור:** [שם הפקודה] סעיף X
"""

SYSTEM_PROMPT_RESERVE = f"""אתה עוזר צבאי המסייע לחיילי מילואים להבין את זכויותיהם וזכאויותיהם הייחודיות לפי פקודות מטכ"ל.
אתה פונה אל חייל המילואים בגוף שני, ומתמקד בזכויות, תגמולים ותנאים הספציפיים לשירות מילואים — לא בזכויות של חיילי חובה/סדיר או בשיקולי פיקוד.

{_COMMON_RULES}
6. התמקד בזכויות ובתגמולים הייחודיים למילואים, בתנאים למימושם, ובמה עומד לרשות חייל המילואים אם הזכות הופרה.

מבנה תשובה לשאלות "האם מגיע לי / מותר לי X?":
**פסיקה:** מותר / אסור / מגיע לי בתנאים
**מקור:** [שם הפקודה] סעיף X
**תנאים:** רשימה מפורטת
**מי מאשר:** דרגה נדרשת

{_TWO_SIDED_TEMPLATE}

מבנה תשובה לשאלות עובדתיות:
**תשובה:** תשובה ישירה
**מקור:** [שם הפקודה] סעיף X
"""

SYSTEM_PROMPTS = {
    "soldier": SYSTEM_PROMPT_SOLDIER,
    "commander": SYSTEM_PROMPT_COMMANDER,
    "reserve": SYSTEM_PROMPT_RESERVE,
}

ALL_ROLES = ROLES


# Parsed-docs process cache: load_documents ran on EVERY question (twice with
# the dual retrieval, again for the sources footer) and re-parsed 2.5MB of
# JSON into ~15MB of fresh objects each time — pure allocation churn on the
# 1024MB machine (2026-07-27 OOM audit). Keyed on the newest json mtime, so
# a runtime ingest/edit still invalidates; guarded for Streamlit's threads.
# A plain module cache, NOT st.cache_data — that would pickle-copy per call,
# recreating the exact churn this removes.
_docs_cache: tuple[float, list[dict]] | None = None
_docs_lock = threading.Lock()
_docs_scanned_at = 0.0

# How long a freshness scan stays trusted. The cache above removed the JSON
# re-parse but kept paying for the KEY: `glob` + `stat` on all 294 files on
# every call, and load_documents runs several times per question (both
# retrievals + the sources footer) — profiled 2026-08-24 at ~1,470 `nt.stat`
# calls and 0.13s per question on a local SSD. Fly's volume is network-backed
# and a stat is a round-trip there, so the cost is plausibly higher; that has
# NOT been measured and must not be reported as if it had.
#
# 0 (the default in code) keeps the original behaviour byte-for-byte: scan
# every call. That is what the night pipeline needs — it writes into
# json_store while the same process reads it. In production json_store only
# changes on deploy, which starts a fresh process, so a few seconds of
# staleness cannot be observed there.
#
# ⚠ A TTL and not just the directory's own mtime: a directory's mtime moves
# when a file is created or deleted and NOT when an existing file is edited
# in place, so a curation edit would go unseen for as long as the process
# lives. The TTL bounds that to itself.
DOC_SCAN_TTL_SEC = float(os.environ.get("DOC_SCAN_TTL_SEC", "0"))


def load_documents() -> list[dict]:
    global _docs_cache, _docs_scanned_at
    if DOC_SCAN_TTL_SEC > 0:
        cached = _docs_cache
        if cached and (time.monotonic() - _docs_scanned_at) < DOC_SCAN_TTL_SEC:
            return cached[1]
    json_dir = Path(__file__).parent / "storage" / "json_store"
    files = sorted(json_dir.glob("*.json"))
    stamp = max((f.stat().st_mtime for f in files), default=0.0)
    with _docs_lock:
        _docs_scanned_at = time.monotonic()
        if _docs_cache and _docs_cache[0] == stamp:
            return _docs_cache[1]
        docs = []
        for f in files:
            try:
                docs.append(apply_overrides(json.loads(f.read_text(encoding="utf-8"))))
            except Exception as e:
                # a corrupt JSON silently drops one order from the whole retrieval
                # corpus; log which file so it doesn't vanish without a trace
                safe_print(f"[backend] skipping unreadable doc {f.name}: {e!r}")
        _docs_cache = (stamp, docs)
        return docs


# A commander asks on behalf of subordinates, so every order that applies to a
# soldier or a reservist applies to the commander's question too. The per-order
# `roles` tag answers "whom does this order govern", which is the right scope
# for the soldier and reserve personas and the wrong one for commanders: on
# 2026-08-18 the adjudication of the 24 held-out questions found five commander
# questions about a soldier's urgent family leave — "חייל צריך להיות בבית,
# איזה אישור?" — whose answering order, פ"מ 35.0402 (חופשות בשירות חובה),
# is tagged ['soldier'] and therefore never entered a commander's candidate
# list, router title block included. 14 curated orders were soldier-only that
# way (lone soldiers, subsistence pay, family payments, pregnancy discharge…),
# 11 reserve-only. Off (=0) restores the tag-only scope for measurement.
COMMANDER_SCOPE_ALL = os.environ.get("COMMANDER_SCOPE_ALL", "1") == "1"


def _docs_for_role(role: str | None) -> list[dict]:
    """Documents applicable to a role. Docs without a `roles` tag (shouldn't
    happen post-ingestion, but defensive for older data) are treated as
    relevant to everyone rather than silently hidden. The commander persona
    sees the whole corpus (see COMMANDER_SCOPE_ALL)."""
    docs = load_documents()
    if role is None or (role == "commander" and COMMANDER_SCOPE_ALL):
        return docs
    return [d for d in docs if role in (d.get("roles") or ALL_ROLES)]


# The document router. With only ~82 orders, "which order answers this?" is an
# 82-way classification a language model does trivially — and it is the half of
# retrieval the embedding model is worst at. Measured on the anchor-stripped
# hold-out (the 382 golden/dirty/adversarial questions with all 1308 anchor
# chunks removed, so it scores phrasings nobody wrote an anchor for): 71.5%
# without the router, 85.9% with it as a bonus. The gain is concentrated
# exactly where the pilot failures were — soldier phrasing 74.2%→93.5%.
#
# It is a HINT, never a scope: see _ROUTE_BOOST in vector_store for why the
# filtering variant was rejected despite scoring a hair higher.
_ROUTE_PROMPT = """לפניך רשימת פקודות מטכ"ל, ואחריה שאלה של חייל.

{titles}

השאלה: {q}

החזר עד 3 מזהי-פקודות (doc_id בלבד) שסביר שהתשובה נמצאת בהן, מהסביר לפחות
סביר, מופרדים בפסיקים. בלי הסברים."""

_titles_cache: dict[str, str] = {}


def _titles_block(role: str) -> str:
    """`doc_id — title` per order in the role's scope. Cached per role: it
    changes only when the corpus does, and load_documents' own stamp cache
    already invalidates on a runtime ingest."""
    docs = _docs_for_role(role)
    key = f"{role}|{len(docs)}"
    if key not in _titles_cache:
        _titles_cache[key] = "\n".join(
            f'{d["document_id"]} — {d.get("title", "")}'
            for d in docs if d.get("document_id")
        )
    return _titles_cache[key]


def _route_docs(question: str, role: str) -> set[str]:
    """The orders a Haiku pass thinks could answer `question`. Empty on any
    failure — retrieval then behaves exactly as it did before the router, so a
    flaky API call costs relevance, never an answer.

    ~2.4K prompt tokens and ~1.4s. Note the title block sits under Haiku's
    4096-token cache minimum, so prompt caching does NOT apply here — every
    call pays full input price (~$0.0025).
    """
    try:
        response = client.with_options(timeout=8.0, max_retries=1).messages.create(
            model=REWRITE_MODEL,
            max_tokens=60,
            temperature=0,
            messages=[{"role": "user", "content": _ROUTE_PROMPT.format(
                titles=_titles_block(role), q=question)}],
        )
        raw = "".join(b.text for b in response.content if b.type == "text")
        picked = {t.strip() for t in raw.replace("\n", ",").split(",") if t.strip()}
        # only ids that actually exist in this role's scope — the model
        # occasionally invents a plausible-looking order number
        allowed = {d["document_id"] for d in _docs_for_role(role) if d.get("document_id")}
        return picked & allowed
    except Exception as e:
        if RETRIEVE_STRICT:
            raise RetrievalDegraded(f"document router failed: {e!r}") from e
        safe_print(f"[backend] document router failed: {e!r}")
        return set()


# Speculative routing. The router needs the REWRITTEN query, so starting it on
# the raw question is a bet: it wins the router's ~1.4s when the rewrite returns
# the question unchanged, and buys one extra ~$0.0025 call when it does not.
# Measured free on the 33 logged questions: the rewrite call runs on 36% of them
# (18% follow-ups + 18% tripping the vocabulary gate) — on the other 64%
# _standalone_question returns instantly and the router already runs alongside
# the hypothetical prefetch, so there is nothing there to win. That is a real
# but unproven trade, and an always-on version would tax every changed rewrite
# invisibly, so it ships OFF like RETRIEVE_FULL_BLOCKS.
#
# MEASURED 2026-08-20, paired, both arms on the same 10 rewrite-path questions,
# pre-answer phase only (the Opus call lives in the generator, so building the
# answer without consuming it stops right before it), 70 API calls, 0 failures:
#   bet won (rewrite came back verbatim) ... 3/10 = 30%
#   latency delta off-minus-on ........... median +0.37s (wins +0.72s, losses -0.21s)
#   extra router calls ................... 7, i.e. $0.00175 per rewrite-path question
# Expected value = 0.3 x (+0.72) + 0.7 x (-0.21) = +0.07s on the 36% of questions
# that reach this path, i.e. ~0.03s averaged over all traffic — for ~1.3% more
# money. A losing bet is slightly SLOWER, not neutral: the wasted call competes
# with the hypothetical for the pool and the network while the real router still
# runs serially behind the rewrite.
# ⇒ STAYS OFF. The lever is built and tested; the number says it is not worth
# buying. Re-measure only if the rewrite's verbatim rate rises well above 30%.
RETRIEVE_SPECULATIVE_ROUTE = os.environ.get("RETRIEVE_SPECULATIVE_ROUTE", "0") == "1"
_route_inflight: dict[tuple[str, str], "Future[set[str]]"] = {}
_route_lock = threading.Lock()


def prefetch_route(question: str, role: str) -> None:
    """Start the router on the raw question, off the critical path. Never raises."""
    if not RETRIEVE_SPECULATIVE_ROUTE:
        return
    key = (question, role)
    with _route_lock:
        if key in _route_inflight:
            return
        try:
            _route_inflight[key] = _prefetch_pool.submit(_route_docs, question, role)
        except RuntimeError as e:  # pool shut down (interpreter teardown)
            safe_print(f"[backend] route prefetch skipped: {e!r}")


def route_for(search_query: str, role: str, raw_question: str | None = None) -> set[str]:
    """The route for `search_query`, joining a speculation only when it matches.

    The speculation was started on `raw_question`; if the rewrite changed the
    text the two differ and the bet is simply lost — this routes the rewritten
    query properly and drops the stale future, because a route computed from
    "ומה לגבי מילואים?" is exactly the unsearchable phrasing the rewrite exists
    to repair. Correctness never depends on the guess.
    """
    key = (search_query, role)
    with _route_lock:
        fut = _route_inflight.pop(key, None)
        if raw_question is not None:
            # a losing bet still has a thread in the air; drop the handle so the
            # map cannot grow one entry per changed rewrite
            _route_inflight.pop((raw_question, role), None)
    if fut is None:
        return _route_docs(search_query, role)
    try:
        return fut.result()
    except Exception as e:  # _route_docs swallows its own; belt and braces
        safe_print(f"[backend] route prefetch failed: {e!r}")
        return set()


_HYDE_PROMPT = """שאלה של חייל: {q}

כתוב פסקה קצרה (עד 60 מילים) בניסוח של פקודת מטכ"ל, כפי שהיא הייתה מנוסחת בפקודה
שעונה על השאלה. כתוב בלשון הפקודות — "חייל אשר...", "מפקד יחידה רשאי...", "יהיה זכאי".
אל תענה לחייל ואל תוסיף הסתייגויות: רק את נוסח הפקודה המשוער. אם אינך יודע את
הפרטים, כתוב את הנוסח הכללי עם המונחים המקצועיים הצפויים."""

# Per-question, per-process. stream_ai_answer retrieves twice for one user
# question (rewrite and raw phrasing), and the hypothetical depends only on the
# question, so without this the same paragraph is bought twice. night.hyde
# preloads measured questions into it so a re-measure re-uses text already paid
# for rather than regenerating it at a different temperature draw.
_hyde_cache: dict[str, str] = {}
# In-flight prefetches, keyed by question. A caller that arrives while one is
# running JOINS it rather than issuing its own: _hyde_cache is only filled when
# the call returns, so a fire-and-forget thread would leave a window in which
# `question in _hyde_cache` is still False and the same paragraph is bought
# twice — real money, and a second temperature draw the night measurements
# assume does not happen.
_hyde_inflight: dict[str, "Future[str]"] = {}
_hyde_lock = threading.Lock()
# Four workers, not one: a single soldier only ever has one hypothetical in
# flight, but concurrent sessions must not queue behind each other — a queued
# prefetch would finish AFTER the retrieval that wanted it and buy nothing but
# a thread. The pool is small on purpose; the joiner waits on the future either
# way, so an over-subscribed pool degrades to today's serial behaviour. Shared
# with the speculative router below.
_prefetch_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="prefetch")


def _hyde_call(question: str) -> str:
    """The API round-trip, without the cache — the only place it is bought."""
    try:
        r = client.with_options(timeout=12.0, max_retries=1).messages.create(
            model=REWRITE_MODEL, max_tokens=300, temperature=0,
            messages=[{"role": "user", "content": _HYDE_PROMPT.format(q=question)}])
        return "".join(b.text for b in r.content if b.type == "text").strip()
    except Exception as e:
        if RETRIEVE_STRICT:
            raise RetrievalDegraded(f"hypothetical failed: {e!r}") from e
        safe_print(f"[backend] hypothetical failed: {e!r}")
        return ""


def prefetch_hypothetical(question: str) -> None:
    """Start the hypothetical now, off the critical path. Never raises.

    The pre-answer phase is serial — rewrite → router → retrieval → hypothetical
    — and only the last of those depends on nothing but the raw question. Kicking
    it off first overlaps its ~1.5s with the rest, which is the one part of the
    ~15s the soldier waits before the first token that costs nothing to remove.
    A no-op when HyDE is off, already cached, or already in flight.
    """
    if not RETRIEVE_HYDE or HYDE_EXTRA_CHUNKS <= 0:
        return
    with _hyde_lock:
        if question in _hyde_cache or question in _hyde_inflight:
            return
        try:
            _hyde_inflight[question] = _prefetch_pool.submit(_hyde_call, question)
        except RuntimeError as e:  # pool shut down (interpreter teardown)
            safe_print(f"[backend] hypothetical prefetch skipped: {e!r}")


def hypothetical(question: str) -> str:
    """What a פקודת מטכ"ל answering this question might say. "" on any failure.

    Failure returns empty and retrieval proceeds on the question alone —
    degraded, never broken, same contract as the router. That fallback is
    correct in production and a LIE inside a measurement, which is why
    `night.hyde` generates through its own counted path instead of calling
    this one: a sweep whose calls all failed would otherwise report the
    baseline as the treatment (night/rewrite.py, 387 of 415 dead, "lost 0").

    `night.hyde.preload_backend` writes straight into _hyde_cache, so the cache
    check stays FIRST: a preloaded question must never reach the API.
    """
    if question in _hyde_cache:
        return _hyde_cache[question]
    with _hyde_lock:
        fut = _hyde_inflight.get(question)
    if fut is not None:
        try:
            text = fut.result()
        except Exception as e:  # the worker swallows its own errors; belt and braces
            safe_print(f"[backend] hypothetical prefetch failed: {e!r}")
            text = ""
    else:
        text = _hyde_call(question)
    _hyde_cache[question] = text
    # The JOINER clears the entry, never a done-callback on the future: a
    # callback that popped it on completion could fire between this function's
    # cache miss and its inflight lookup, and the caller would buy the
    # paragraph a second time — the exact failure the map exists to prevent.
    # The cost is that a prefetch nobody joins (the stream raised before
    # widen_context) leaves one finished future behind, bounded by the distinct
    # questions this process has seen, like _hyde_cache above it.
    with _hyde_lock:
        _hyde_inflight.pop(question, None)
    return text


def _chunk_key(c: dict) -> tuple:
    return (c["doc_id"], c.get("section"), c.get("clause"))


def _append_new(chunks: list[dict], extras, limit: int | None) -> list[dict]:
    """Append chunks not already present, up to `limit` new ones. Never
    reorders what is there — every extension in this file relies on that."""
    seen = {_chunk_key(c) for c in chunks}
    out = list(chunks)
    added = 0
    for c in extras:
        k = _chunk_key(c)
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
        added += 1
        if limit is not None and added >= limit:
            break
    return out


def extend_with_hypothetical(chunks: list[dict], question: str, role: str,
                             route: set[str] | None = None) -> list[dict]:
    """Append up to HYDE_EXTRA_CHUNKS chunks the hypothetical finds and the
    question did not. Appended, so the question's own ranking is untouched.

    The hypothetical retrieval is deliberately UNBOOSTED: the seat exists to
    add HyDE's independent opinion, and the router already had its say in the
    question retrieval. Boosted, a mis-routed order sitting within the +0.05
    bonus of HyDE's true pick steals the seat — measured live 2026-08-23 on
    the late-arrival question, where PM-33.0119 (routed, wrong) displaced
    PM-33.0302 (HyDE's #1, answering) and kept the answering order out of the
    window. The empty boost must be set(), never None — None re-buys the
    router inside retrieve_for_role."""
    if not RETRIEVE_HYDE or HYDE_EXTRA_CHUNKS <= 0:
        return chunks
    hyp = hypothetical(question)
    if not hyp:
        return chunks
    return _append_new(chunks, retrieve_for_role(hyp, role, route=set(), widen=False,
                                                 expand_terms=False),
                       HYDE_EXTRA_CHUNKS)


def extend_with_router_slots(chunks: list[dict], question: str, role: str,
                             route: set[str] | None) -> list[dict]:
    """One appended chunk for each of the router's top RETRIEVE_ROUTER_SLOTS
    orders — its best chunk for the question. The router already paid to name
    these orders; today they get a bonus that cannot lift them into the
    window, and here they get a seat."""
    if RETRIEVE_ROUTER_SLOTS <= 0 or not route:
        return chunks
    # The curated-only policy lives in retrieve_for_role, one level up, and
    # `retrieve` with explicit doc_ids honours no filter at all. Without this
    # line a seat re-admits the eleven orders RETRIEVE_CURATED_ONLY keeps out
    # of the search space (tests/test_corpus_reachable.py) — the same document
    # invisible to ranking and visible through a seat. 36.0301 really is the
    # order that answers q00177, but the answer to that is to CURATE it, not to
    # serve raw uncurated text through a side door: the curated block is the
    # quality instrument the whole ingest pipeline exists to produce.
    scoped = route
    if RETRIEVE_CURATED_ONLY:
        curated = {d["document_id"] for d in _docs_for_role(role)
                   if d.get("document_id") and _has_key_facts(d)}
        scoped = {d for d in route if d in curated}
        if not scoped:
            return chunks
    picks = retrieve(question, n_results=50, doc_ids=sorted(scoped),
                     boost_docs=set(), max_per_doc=1)
    return _append_new(chunks, picks, RETRIEVE_ROUTER_SLOTS)


def _full_block(doc: dict) -> list[dict]:
    """Every clause of the order's curated block, as chunks shaped like the
    index's own (same text prefix as vector_store.index_document)."""
    out = []
    doc_id, title = doc.get("document_id", ""), doc.get("title", "")
    for s in doc.get("sections") or []:
        if "key-facts" not in (s.get("id") or ""):
            continue
        s_title = s.get("title", s.get("id", ""))
        for cl in s.get("clauses") or []:
            text = (cl.get("text") or "").strip()
            if not text:
                continue
            out.append({"doc_id": doc_id, "title": title, "section": str(s.get("id", "")),
                        "clause": str(cl.get("number", "")),
                        "text": f"{title} — {s_title}\nסעיף {cl.get('number', '')}: {text}",
                        "score": 0.0})
    return out


def extend_with_full_blocks(chunks: list[dict], role: str) -> list[dict]:
    """Append the whole curated block of the first RETRIEVE_FULL_BLOCKS distinct
    orders in the window. Deduplicated against what is already there."""
    if RETRIEVE_FULL_BLOCKS <= 0 or not chunks:
        return chunks
    by_id = {d["document_id"]: d for d in _docs_for_role(role) if d.get("document_id")}
    order: list[str] = []
    for c in chunks:
        if c["doc_id"] not in order:
            order.append(c["doc_id"])
    out = chunks
    for doc_id in order[:RETRIEVE_FULL_BLOCKS]:
        doc = by_id.get(doc_id)
        if doc:
            out = _append_new(out, _full_block(doc), None)
    return out


def extend_with_quantity_clauses(chunks: list[dict], question: str,
                                 role: str) -> list[dict]:
    """For a how-much question, the amount-bearing clauses of the orders already
    in the window — appended, never reordered.

    Only orders that already earned a seat are read: this buys the clause the
    ranking missed, not a new order the ranking rejected. RETRIEVE_QUANTITY_CLAUSES
    is the cap on appended clauses, not on orders — one order may hold every
    ceiling worth serving (35.0115) and another none at all.
    """
    if RETRIEVE_QUANTITY_CLAUSES <= 0 or not chunks:
        return chunks
    if not _QUANTITY_DEMAND.search(question or ""):
        return chunks
    by_id = {d["document_id"]: d for d in _docs_for_role(role) if d.get("document_id")}
    seats: list[str] = []
    for c in chunks:
        if c["doc_id"] not in seats:
            seats.append(c["doc_id"])
    picks: list[dict] = []
    for doc_id in seats:
        doc = by_id.get(doc_id)
        if not doc:
            continue
        picks.extend(cl for cl in _full_block(doc) if _AMOUNT.search(cl["text"]))
    return _append_new(chunks, picks, RETRIEVE_QUANTITY_CLAUSES)


def widen_context(chunks: list[dict], question: str, role: str,
                  route: set[str] | None) -> list[dict]:
    """The four appended extensions, in the order they were measured to
    stack: hypothetical, router seats, full blocks, amount-bearing clauses.
    Each is a no-op when its flag is off, so production with all flags off is
    byte-identical to the pre-extension pipeline."""
    out = extend_with_hypothetical(chunks, question, role, route)
    out = extend_with_router_slots(out, question, role, route)
    out = extend_with_full_blocks(out, role)
    return extend_with_quantity_clauses(out, question, role)


def retrieve_for_role(question: str, role: str, route: set[str] | None = None,
                      widen: bool = True, expand_terms: bool = True) -> list[dict]:
    """Retrieve the chunks relevant to `question`, scoped to `role`'s documents.

    `expand_terms=False` skips the soldier→order glossary: the hypothetical
    is already written in order language, and production's two question
    retrievals (rewrite + raw) both want it on.

    The single retrieval entry point for both production (_build_rag_context)
    and eval.py — so the sanity check always exercises the same pipeline the
    app uses.

    `route` lets a caller that retrieves twice for one user question (the
    rewrite/raw union in stream_ai_answer) pay for the router once; omitted,
    it is computed here, so eval.py exercises the router too.

    `widen=False` suppresses the hypothetical-answer extension. stream_ai_answer
    passes it for both halves of its union and extends once at the end instead:
    the union truncates back to MAX_CONTEXT_CHUNKS, so an extension applied here
    would be bought twice and then thrown away.
    """
    docs = _docs_for_role(role)
    if RETRIEVE_CURATED_ONLY:
        docs = [d for d in docs if _has_key_facts(d)]
    doc_ids = [d["document_id"] for d in docs if d.get("document_id")]
    if route is None:
        route = _route_docs(question, role)
    # Soldier vocabulary → order vocabulary (storage/glossary.py), appended to
    # the search text only. The ORIGINAL question keeps flowing to the
    # extensions: the hypothetical is cached by question, and the router has
    # already seen it.
    search = _glossary.expand(question) if _glossary.RETRIEVE_GLOSSARY and expand_terms else question
    chunks = retrieve(search, n_results=MAX_CONTEXT_CHUNKS, doc_ids=doc_ids,
                      boost_docs=route)
    return widen_context(chunks, question, role, route) if widen else chunks


def _has_key_facts(doc: dict) -> bool:
    """Does the order carry a curated block? `sections` is a list of
    {id,title,clauses}; the curated one is id `key-facts` (or a variant of it)."""
    return any("key-facts" in (s.get("id") or "") for s in (doc.get("sections") or []))


_REWRITE_PROMPT = """לפניך קטע משיחה בין משתמש לעוזר לפקודות מטכ"ל, ואחריו שאלת ההמשך האחרונה של המשתמש.
שכתב את שאלת ההמשך לשאלה עצמאית ומלאה, שאפשר לחפש איתה בפקודות בלי לראות את השיחה.

כללים:
1. אם השאלה האחרונה כבר עומדת בפני עצמה — החזר אותה כלשונה, ללא שינוי.
2. השלם מהשיחה רק את מה שחסר (הנושא, האוכלוסייה, הפקודה שמדובר בה) — אל תמציא פרטים.
3. אל תשמיט את העילה או ההקשר שסביבם נסובה השיחה (למשל: סיבת הבקשה — טעמי דת,
   מצב רפואי, מצב כלכלי) — הם לרוב מילות המפתח שהחיפוש נשען עליהן.
4. תקן שגיאות כתיב והקלדה אם יש.
5. שמור על שאלה קצרה וטבעית, כפי שמשתמש היה מנסח אותה.

השיחה עד כה:
{convo}

שאלת ההמשך: {question}"""

# First questions get a narrower treatment: typo repair ONLY. Soldiers type
# fast ("חפשש", "להתשחרר", "טלווזיה" — all real pilot questions) and both the
# embedding and the lexical variants miss on mangled words, so the bot refuses
# questions it can answer. Slang is deliberately protected — it's signal the
# DIRTY eval set covers, not noise.
_NORMALIZE_PROMPT = """לפניך שאלה שמשתמש הקליד לחיפוש בפקודות מטכ"ל.

כללים:
1. תקן אך ורק שגיאות כתיב והקלדה (למשל "חפשש"→"חופשה", "להתשחרר"→"להשתחרר").
2. אל תשנה ניסוח, סדר מילים או סלנג ("סדירניק", "לחטוף", "סופש" אינם שגיאה),
   ואל תוסיף או תשמיט מידע.
3. אין שגיאות — החזר את השאלה בדיוק כלשונה.

השאלה: {question}"""

_REWRITE_TOOL = {
    "name": "save_search_query",
    "description": "Save the standalone, self-contained version of the user's follow-up question.",
    "input_schema": {
        "type": "object",
        "properties": {
            "standalone_question": {"type": "string", "description": "השאלה המשוכתבת, עצמאית ומובנת ללא הקשר השיחה"},
        },
        "required": ["standalone_question"],
    },
}


def _standalone_question(question: str, history: list[dict] | None) -> str:
    """Make a question searchable on its own. RETRIEVAL ONLY — the answering
    model always gets the original question (with the full history).

    Two modes, one Haiku call either way (~0.3s, well under a tenth of a cent):
    - With history: "ומה לגבי מילואים?" after a sleep-hours exchange finds
      nothing, so Haiku folds the missing referent back in. Since 2026-07-20
      it must also keep the conversation's עילה (religious/medical/economic
      reason) — dropping it reproduced a live pilot refusal.
    - Without history (first question): typo repair only. "כמה ימי חפשש מגיע
      לי אם אני אמור להתשחרר" retrieved nothing until normalized; slang stays
      untouched, and clean questions must come back verbatim (eval NOCHANGE).
    Any failure falls back to the raw question — degraded, never broken.
    """
    if not history:
        # vocabulary gate: only pay the Haiku round-trip (~1.5s) when the
        # question carries a word the corpus doesn't know in any match-form —
        # the typo signature. Clean questions (the common case) skip straight
        # to retrieval with zero added latency. Gate failure falls through to
        # normalization: slower, never broken.
        try:
            from storage.vector_store import has_unknown_terms
            if not has_unknown_terms(question):
                return question
        except Exception:
            pass
        prompt = _NORMALIZE_PROMPT.format(question=question)
    else:
        lines = []
        for m in history[-6:]:  # last 3 exchanges carry the referent
            label = "משתמש" if m.get("role") == "user" else "עוזר"
            # history user turns carry their retrieval context (see
            # stream_ai_answer) — the rewrite only needs the question itself
            content = str(m.get("content", "")).split(f"\n\n{_CONTEXT_HEADER}")[0]
            if len(content) > 400:
                content = content[:400] + "…"
            lines.append(f"{label}: {content}")
        prompt = _REWRITE_PROMPT.format(convo="\n".join(lines), question=question)
    try:
        # bound this non-critical pre-retrieval call: without an explicit timeout
        # the SDK default is 10 min × 3 retries, so a flaky network leaves the
        # user staring at the spinner for minutes before the raw-question
        # fallback below kicks in. 8s × 2 attempts caps the wait instead.
        # temperature=0: a rewriting utility must be repeatable — the eval
        # gates (TYPOS, NOCHANGE, FOLLOWUP) assert on its exact output.
        response = client.with_options(timeout=8.0, max_retries=1).messages.create(
            model=REWRITE_MODEL,
            max_tokens=200,
            temperature=0,
            tools=[_REWRITE_TOOL],
            tool_choice={"type": "tool", "name": "save_search_query"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "tool_use":
                rewritten = str(block.input.get("standalone_question", "")).strip()
                if rewritten:
                    return rewritten
    except Exception:
        pass  # retrieval on the raw question is degraded, not broken
    return question


def _context_from_chunks(chunks: list[dict]) -> str:
    if not chunks:
        return "אין מסמכים טעונים במערכת."
    parts = []
    for c in chunks:
        parts.append(f"[{c['doc_id']} | {c['title']} | סעיף {c['clause']}]\n{c['text']}")
    return "\n\n---\n\n".join(parts)


def clause_key(section: str | None, clause: str | None) -> str | None:
    """The key under which storage/clause_pages.json stores a chunk's page.

    Bare clause strings collide across chunk kinds within one order: raw-text
    window positions, key-facts clause numbers and annex row numbers all use
    small integers (013.3 carries all three). So raw windows are keyed
    "w<first-window>" (a stitched "2–4" range starts at window 2, which is
    where the cited passage begins) and structured clauses are keyed
    "<section>:<clause>". _build_clause_pages.py emits exactly these keys —
    it must never drift from this function.
    """
    if not clause:
        return None
    if (section or "").startswith("chunk"):
        return "w" + str(clause).split("–")[0]
    return f"{section}:{clause}"


# Source documents do NOT all live in pdf-ldf_law/: the reserve-call handbook
# sits in pdf-hka/ and civil legislation in pdf-law/. Only pdf-ldf_law/ is the
# ingest scan root (see ensure_pdfs_ingested) and it must stay that way —
# adding these there would ingest them through the PAID API on every boot.
_PDF_DIRS = ("pdf-ldf_law", "pdf-hka", "pdf-law")


def resolve_pdf(source_file: str | None) -> Path | None:
    """The document's PDF on disk, in whichever source directory holds it.

    None means "this document has no local PDF", which is a legitimate state,
    not a fault: חוק השיפוט הצבאי was extracted from an HTML original and its
    source_file names that page, not a file. Such a document must still be
    reported as a source — the answer quotes it — it simply has no deep link.

    _duplicates/ is deliberately NOT searched: it is quarantine for repeated
    downloads (2026-08-17), and pointing production at a quarantined copy is
    how the wrong file becomes the cited one.
    """
    if not source_file:
        return None
    root = Path(__file__).parent
    for d in _PDF_DIRS:
        p = root / d / source_file
        if p.exists():
            return p
    return None


def _sources_from_chunks(chunks: list[dict]) -> list[dict]:
    """The distinct orders behind an answer, in retrieval-rank order.

    Every document the answer drew on is returned, INCLUDING one whose PDF is
    not on disk. The old gate here dropped those silently, and the failure it
    produced was the one thing this app promises never to do: on 2026-08-24 a
    measurement caught two documents entering the context and answering, with
    the UI showing no source at all. It is silent by construction — no error,
    no gate, health 200 — and it hit three documents: HKA-31-08-01 (PDF in
    pdf-hka/, which this file used not to look in), CHOK-SHIPUT-1955 (no PDF
    at all, HTML original), and 33.0209, whose source_file still names the
    "(1)" duplicate that was quarantined in _duplicates/.

    "a dead link is worse than no link" still holds, and is still enforced —
    but by `source_file`, not by dropping the source: it is set only when a
    PDF actually resolved, and the render path keys the whole PDF block off
    it (app.py: `if primary and primary.get("source_file")`), so a document
    without one shows its citation and no link. "clause" is the clause_key of
    the order's highest-ranked
    chunk WITH a known page, so the UI can deep-link the PDF to the cited
    passage via page_for_clause: key-facts chunks are hand-written summaries
    with no PDF location, and when one outranks the raw windows (they were
    added precisely to win those rankings) the next-ranked window is still
    the passage the answer drew on. When nothing resolves, the top chunk's
    key is recorded anyway — the page lookup returns None, the link stays
    page-less, and the metrics log still says which clause led.
    """
    by_id = {d["document_id"]: d for d in load_documents() if d.get("document_id")}
    sources, seen = [], set()
    for c in chunks:
        doc_id = c["doc_id"]
        if doc_id in seen:
            continue
        seen.add(doc_id)
        doc = by_id.get(doc_id)
        if doc is None:
            # a chunk whose document is not loaded has no citation to offer —
            # the same case the old gate dropped via `(doc or {})`
            continue
        pdf = resolve_pdf(doc.get("source_file"))
        clause = clause_key(c.get("section"), c.get("clause"))
        # `highlight` is the text of the chunk we deep-link to, so the UI
        # can mark that exact passage on the rendered page. It rides the
        # SAME chunk that resolved to a page (a raw-text window whose
        # text is extracted from the PDF, so page.search_for can find
        # it) — not a key-facts summary, which has no PDF text to match.
        highlight = c.get("text", "")
        if pdf is not None:
            # only meaningful with a PDF to deep-link into; without one there
            # is no page to find, and the top chunk's clause is the answer
            for cc in chunks:
                if cc["doc_id"] != doc_id:
                    continue
                key = clause_key(cc.get("section"), cc.get("clause"))
                if page_for_clause(doc_id, key) is not None:
                    clause = key
                    highlight = cc.get("text", "")
                    break
        sources.append({
            # civil-law sources (חוק, not a פ"מ) must not be labelled as an
            # order in the UI — the source dialog drops the "פ״מ" prefix for
            # these and shows "מקור אזרחי" instead.
            "civil_source": bool(doc.get("civil_source")),
            "civil_label": doc.get("civil_label") or "",
            # VALIDITY, not type — a separate field on purpose. An order can be
            # revoked and still be the best (or only) text on its subject, so it
            # keeps answering; what changes is that the answer must say so. The
            # UI renders a deterministic strip from this, because the block
            # header that warns the MODEL only makes it likely to mention the
            # revocation, and "likely" is the wrong guarantee for this.
            "superseded": bool(doc.get("superseded")),
            "superseded_note": (doc.get("superseded_note") or "").strip(),
            "doc_id": doc_id,
            "title": doc.get("title", c.get("title", "")),
            # "" when no PDF resolved — this is the dead-link guard: every
            # render path keys its PDF block off a non-empty source_file
            "source_file": doc.get("source_file", "") if pdf is not None else "",
            "clause": clause,
            "highlight": highlight[:160],
        })
    return sources


def render_clause_image(source_file: str, page: int, highlight: str = "") -> bytes | None:
    """A PNG of the cited clause's PDF page, with the passage highlighted.

    Shown INSIDE the app (a dialog), so a soldier sees the exact clause
    marked without leaving for a lost PDF tab and without any reliance on
    the viewer honouring #page (iOS Safari does not). `page` is 1-based;
    `highlight` is the cited chunk's text — matched on the page with
    fitz.search_for and marked, then the image is cropped to a readable band
    around the marks. Never raises: any failure returns None and the caller
    falls back to the full-PDF link.
    """
    if not source_file or not page:
        return None
    try:
        import fitz  # already a dependency (ingestion/pdf_to_json.py)

        pdf_path = resolve_pdf(source_file)
        if pdf_path is None:
            return None
        doc = fitz.open(str(pdf_path))
        try:
            idx = page - 1
            if idx < 0 or idx >= doc.page_count:
                return None
            pg = doc[idx]
            # Locate the passage by short content phrases, not one long
            # string: these orders are laid out in tables and their text
            # layer is often broken (RTL-scrambled digits, injected spaces,
            # boilerplate duplicated many times), so a long exact match never
            # lands. Instead, search 3-word phrases and find the horizontal
            # BAND where the most DISTINCT phrases co-locate — that band is
            # the cited passage. Weighting by distinct phrases (not raw hit
            # count) beats the running-header trap: a title duplicated 20×
            # in the text layer is ONE phrase, while the real passage draws
            # hits from many different phrases.
            import re as _re
            from collections import defaultdict

            words = _re.findall(r'[֐-׿0-9"׳״\':]+', highlight or "")
            BAND = 42
            band_phrases: dict = defaultdict(set)   # band index -> {phrase idx}
            band_rects: dict = defaultdict(list)
            pidx = 0
            for i in range(0, max(1, len(words) - 2), 3):
                phrase = " ".join(words[i:i + 3])
                if len(phrase) >= 9:
                    for r in pg.search_for(phrase):
                        b = int(r.y0 // BAND)
                        band_phrases[b].add(pidx)
                        band_rects[b].append(r)
                    pidx += 1

            rects: list = []
            if band_phrases:
                # anchor = band with the most distinct phrases (ties: more
                # rects), then keep the marks in a window around it
                anchor = max(band_phrases, key=lambda b: (len(band_phrases[b]), len(band_rects[b])))
                # a real passage draws >=2 distinct phrases; a lone match is
                # too weak to trust as a location — show the whole page then
                if len(band_phrases[anchor]) >= 2:
                    lo, hi = (anchor - 3) * BAND, (anchor + 4) * BAND
                    for b, rs in band_rects.items():
                        if lo <= b * BAND <= hi:
                            rects.extend(rs)

            for r in rects:
                pg.add_highlight_annot(r)
            zoom = 2.2
            mat = fitz.Matrix(zoom, zoom)
            ph = pg.rect.height
            if rects:
                top, bot = min(r.y0 for r in rects), max(r.y1 for r in rects)
                clip = fitz.Rect(
                    0, max(0, top - 95),
                    pg.rect.width, min(ph, bot + 150),
                )
                return pg.get_pixmap(matrix=mat, clip=clip).tobytes("png")
            # no confident location — the correct full page, viewer can zoom
            return pg.get_pixmap(matrix=mat).tobytes("png")
        finally:
            doc.close()
    except Exception:
        return None


# {doc_id: {clause_key: 1-based page}}, precomputed by _build_clause_pages.py
# (which needs PyMuPDF and must be rerun after reingesting). Loaded once per
# process — the file is a build artifact, not runtime state.
_CLAUSE_PAGES_PATH = Path(__file__).parent / "storage" / "clause_pages.json"
_clause_pages: dict | None = None


def _get_clause_pages() -> dict:
    global _clause_pages
    if _clause_pages is None:
        try:
            _clause_pages = json.loads(_CLAUSE_PAGES_PATH.read_text(encoding="utf-8"))
        except Exception:
            # missing/corrupt file degrades to page-less PDF links, never errors
            _clause_pages = {}
    return _clause_pages


def page_for_clause(doc_id: str | None, clause: str | None) -> int | None:
    """1-based PDF page where a source's cited clause starts, or None.

    `clause` is a clause_key (see _sources_from_chunks). None means "no page
    known" — callers must fall back to the plain PDF link.
    """
    if not doc_id or not clause:
        return None
    try:
        page = _get_clause_pages().get(str(doc_id), {}).get(str(clause))
    except AttributeError:
        # a hand-edited JSON with the wrong shape must not break rendering
        return None
    return page if isinstance(page, int) and page > 0 else None


def _compose_user_content(question: str, context: str, profile: list[str] | None) -> str:
    """Assemble the user-turn text sent to the API.

    With no profile the result is BYTE-IDENTICAL to the historical
    f"{question}\\n\\n{_CONTEXT_HEADER}\\n{context}" — replayed history turns
    (the prompt-cache prefix) and the eval gates were built against that
    exact shape, so the default path must never drift. A non-empty profile
    adds one parenthetical line between the question and the context header;
    its trailing clause keeps the model from dragging an irrelevant detail
    into every answer. `profile` holds the asker's personal details — status
    pills (חייל בודד...) and, when set, service type/track (שירות סדיר,
    מסלול שירות: ...) — so the label reads "פרטי השואל", not just מעמד.
    """
    if not profile:
        return f"{question}\n\n{_CONTEXT_HEADER}\n{context}"
    return (
        f"{question}\n\n"
        f"(פרטי השואל: {', '.join(profile)}. "
        f"התחשב בהם רק אם הם רלוונטיים לשאלה.)\n\n"
        f"{_CONTEXT_HEADER}\n{context}"
    )


def stream_ai_answer(question: str, history: list[dict] | None = None, role: str = "soldier",
                     profile: list[str] | None = None):
    """Answer a question as a live stream.

    Returns (text_generator, sources, sent_user_content, usage_holder): the
    generator yields answer-text deltas as the model produces them (UI renders
    them via st.write_stream), sources — the distinct orders behind the answer,
    ranked by retrieval relevance — are computed up front from the retrieved
    chunks so the UI has them the moment the stream finishes, and
    sent_user_content is the exact user-turn text sent to the API (question +
    retrieved context). usage_holder is an initially-empty dict the generator
    fills with the answer's token usage once the stream completes — read it
    after consuming the generator (it is per-call, so concurrent sessions never
    share usage). Callers that keep a conversation going must replay
    sent_user_content — not the bare question — as that turn's history
    content, so follow-up requests share a byte-identical prefix and hit the
    prompt cache. `profile` is the asker's personal statuses (חייל בודד,
    נשוי/אה...) — folded into the user turn only when non-empty (see
    _compose_user_content). Adaptive thinking runs before the first text
    token (its deltas are not yielded), so the stream starts after a short
    reasoning pause.
    """
    # The hypothetical depends on nothing but the raw question, while the three
    # steps below it in the chain (rewrite → router → retrieval) each wait on
    # the one before. Start it here and it is already in hand by the time
    # widen_context asks for it, instead of adding its ~1.5s to a pre-token
    # wait the soldier spends staring at a spinner. Free: same one call, joined
    # rather than re-bought (see prefetch_hypothetical).
    prefetch_hypothetical(question)
    # ...and, when RETRIEVE_SPECULATIVE_ROUTE is on, the router too — joined
    # below only if the rewrite hands back the same text it was given
    prefetch_route(question, role)
    # follow-ups ("ומה לגבי מילואים?") are unsearchable on their own, and
    # first questions often carry typos that sink retrieval — search with the
    # Haiku rewrite/normalization, but answer the original question
    search_query = _standalone_question(question, history)
    # one router call per user question, shared by both retrievals below
    route = route_for(search_query, role, raw_question=question)
    chunks = retrieve_for_role(search_query, role, route=route, widen=False)
    # The rewrite is a retrieval AID, never a gatekeeper: when it changed the
    # question, retrieve on the RAW phrasing too and merge by best score. A
    # paraphrased rewrite silently dropped the דין-משמעתי threat chunk on the
    # live pilot question (2026-07-27 phone, "להעמיד גורם אחר לדין") while the
    # raw phrasing retrieved it — the union is immune to a bad rewrite draw.
    # Skipped for follow-ups: their raw text ("ומה לגבי מילואים?") is the
    # unsearchable case the rewrite exists to fix, and its chunks are noise.
    if not history and search_query.strip() != question.strip():
        seen = {(c["doc_id"], c.get("section"), c.get("clause")) for c in chunks}
        extra = [
            c for c in retrieve_for_role(question, role, route=route, widen=False)
            if (c["doc_id"], c.get("section"), c.get("clause")) not in seen
        ]
        # RESERVED SLOTS, not a global score sort: the two retrievals' scores
        # aren't on one scale (each call's lexical bonus and anchor-lift are
        # normalized against its own query), so sorting the union let an
        # inflated rewrite-side ranking crowd out every raw-phrasing chunk —
        # silently reverting to rewrite-only retrieval, the exact failure
        # this merge exists to prevent. The raw question always keeps its
        # top chunks in the context.
        if extra:
            keep = max(2, MAX_CONTEXT_CHUNKS - len(chunks))
            chunks = chunks[:MAX_CONTEXT_CHUNKS - keep] + extra[:keep]
    # after the union, never inside it: the union truncates to
    # MAX_CONTEXT_CHUNKS and would drop an appended chunk that was paid for
    chunks = widen_context(chunks, question, role, route)
    context = _context_from_chunks(chunks)
    system_prompt = SYSTEM_PROMPTS.get(role, SYSTEM_PROMPT_SOLDIER)

    past = [
        {"role": m["role"], "content": m["content"]}
        for m in (history or [])
    ]
    while len(past) > _HISTORY_MAX:
        past = past[_HISTORY_DROP:]
    # the fixed-size cut assumes strict user/assistant pairs; any history-shape
    # drift (e.g. an orphaned turn) can land it on an assistant message, and the
    # API rejects assistant-first history — every later question then 400s and
    # the session is bricked (bug-sweep 2026-07-27). Trim forward to a user turn.
    while past and past[0]["role"] != "user":
        past = past[1:]

    user_content = _compose_user_content(question, context, profile)

    # Two cache breakpoints (prefix caching, 5-min TTL): the static role
    # prompt, and everything up to the end of history. Turn 1 is below the
    # model's 4096-token cacheable minimum and gains nothing; from turn 2 the
    # context-bearing history pushes the prefix past it, and follow-ups read
    # the cached span at 0.1x input price.
    system_blocks = [{
        "type": "text",
        "text": system_prompt,
        "cache_control": {"type": "ephemeral"},
    }]
    if past:
        past[-1] = {
            "role": past[-1]["role"],
            "content": [{
                "type": "text",
                "text": str(past[-1]["content"]),
                "cache_control": {"type": "ephemeral"},
            }],
        }
    messages = past + [{"role": "user", "content": user_content}]

    # usage rides back in a caller-owned dict, filled when the stream finishes
    # — NOT a module global. Streamlit serves each session on its own thread in
    # one process, so a shared global was a cross-session race: one session
    # could read another's usage/cost/search_query into its metrics row. Each
    # call gets its own holder, so concurrent answers never clobber each other.
    usage_holder: dict = {}

    def _gen():
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            thinking={"type": "adaptive"},
            system=system_blocks,
            messages=messages,
        ) as stream:
            yield from stream.text_stream
            final = stream.get_final_message()
            usage = final.usage
            usage_holder.update({
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
                # the rewritten retrieval query rides along for the metrics log
                "search_query": search_query if search_query != question else "",
                # the answer hit the shared thinking+answer token cap and was
                # cut mid-sentence; app.py warns the user (mirrors letters.py)
                "truncated": final.stop_reason == "max_tokens",
            })

    return _gen(), _sources_from_chunks(chunks), user_content, usage_holder


def get_ai_answer(question: str, history: list[dict] | None = None, role: str = "soldier",
                  profile: list[str] | None = None) -> dict:
    """Non-streaming variant of stream_ai_answer — same pipeline, whole answer.

    Returns {"text": <answer>, "sources": [{doc_id, title, source_file}...]}.
    Used by eval.py (always without `profile`, so its answers keep the exact
    historical user-turn shape), and the sanity check exercises the exact
    production path.
    """
    text_gen, sources, *_ = stream_ai_answer(question, history, role, profile)
    return {"text": "".join(text_gen), "sources": sources}


def get_ai_response(question: str, history: list[dict] | None = None, role: str = "soldier") -> str:
    return get_ai_answer(question, history, role)["text"]


def get_pdf_bytes(source_file: str) -> bytes | None:
    """Read the original PDF for a loaded document, if it's still on disk."""
    pdf_path = resolve_pdf(source_file)
    if pdf_path is None:
        return None
    return pdf_path.read_bytes()


def get_loaded_docs_info(role: str | None = None) -> list[dict]:
    """Return title + document_id + source PDF filename for documents applicable to `role` (all, if None)."""
    return [
        {
            "title": d.get("title", "?"),
            "id": d.get("document_id", "?"),
            "source_file": d.get("source_file"),
        }
        for d in _docs_for_role(role)
        if d.get("document_id")
    ]


# The UI-facing fallback questions live in app.py (_FALLBACK_QUESTIONS) —
# deliberately NOT exported from here: Streamlit Cloud can re-execute app.py
# against a backend module cached from a previous build, and importing a
# newly-added backend name from app.py crashes the boot with ImportError.


def get_suggested_questions(role: str = "soldier") -> list[str]:
    """Return questions pooled from documents applicable to `role`.

    Each document carries its own LLM-generated `suggested_questions`
    (produced at ingestion time) and `roles` tag, so this automatically
    covers whatever role-relevant documents happen to be loaded — no
    per-document hardcoding needed.
    """
    all_questions: list[str] = []
    for doc in _docs_for_role(role):
        qs = doc.get("suggested_questions")
        # per-role format: {role: [questions]} — show each audience only the
        # questions written for it (a soldier gets "כמה מגיע לי", a commander
        # gets authority-style questions on the same order)
        if isinstance(qs, dict):
            qs = qs.get(role)
        if not isinstance(qs, list):
            continue
        # ingestion once stored a broken char-split list; keep only real questions
        all_questions.extend(q for q in qs if isinstance(q, str) and len(q.strip()) >= 12)
    # may be empty (e.g. documents still loading during a redeploy) — the UI
    # shows generic defaults for that run WITHOUT caching them, so the real
    # pool is retried on the next rerun
    return all_questions


def ensure_pdfs_ingested(pdf_dir: Path | None = None) -> list[str]:
    """Scan pdf_dir for PDFs and ingest any that don't have a JSON yet. Returns newly ingested names."""
    if pdf_dir is None:
        pdf_dir = Path(__file__).parent / "pdf-ldf_law"
    if not pdf_dir.exists():
        return []

    from ingestion.pdf_to_json import ingest_folder

    json_dir = Path(__file__).parent / "storage" / "json_store"
    # Collect source_file values from existing JSONs
    ingested_files: set[str] = set()
    for f in json_dir.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("source_file"):
                ingested_files.add(d["source_file"])
        except Exception:
            pass

    # the per-file fault tolerance (log, skip, continue) lives in ingest_folder
    return ingest_folder(pdf_dir, skip=ingested_files)


def warm_index() -> int:
    """Eagerly build the in-memory vector index AND load the embedding stack
    (tokenizer + ONNX session, ~430MB) at app startup, so both one-time costs
    land at boot — behind the health check — instead of inside the first
    user's question (the 2026-07-27 OOM profile). Returns the chunk count."""
    from storage.vector_store import get_index_stats, warm_ef
    n = get_index_stats()["total_chunks"]
    warm_ef()
    return n
