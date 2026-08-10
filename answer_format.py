# -*- coding: utf-8 -*-
"""שפת התשובה — פירוק גוף התשובה לבלוקים שהאפליקציה יודעת לעצב.

למה הקובץ קיים
--------------
לדיאלוגים ולכלים באפליקציה יש שפה ויזואלית שלמה — כרטיסים, תגים, שורות-ציטוט,
קולאאוטים. לתשובה בצ'אט, המשטח היחיד שכל משתמש פוגש בכל שאלה, היה צ'יפ-פסיקה
אחד (`app._verdict_chip`) ומתחתיו מרקדאון חשוף. המשטח הכי חשוב היה הכי פחות
מעוצב.

מה שמאפשר לסגור את זה בלי לנחש: **לתשובה יש דקדוק מוכתב.** הפרומפט
(`backend._COMMON_RULES` ותבניות מבנה-התשובה של שלוש הפרסונות) מחייב תוויות
קבועות — `**מקור:**`, `**תנאים:**`, `**מי מאשר:**`, `**התנהלות X:**` — ו-
`scope_routes` מוסיף שתי תוויות ניתוב. כאן מזהים בדיוק אותן, והופכים כל אחת
לשורה מעוצבת במקום לשורת-מרקדאון מודגשת.

כללי ברזל
---------
* **טהור, בלי Streamlit.** הפירוק והרינדור הם מחרוזות בלבד, ולכן נבדקים בלי
  להריץ אפליקציה ובלי לשלם על קריאת API. זה מה שהופך את הבדיקות לשער אמיתי.
* **מה שלא זוהה נשאר מרקדאון.** כל שורה שאינה תווית נאספת לריצת-פרוזה שהאפליקציה
  מרנדרת ב-`st.markdown` רגיל. פספוס של הזיהוי = המראה של היום, לא שבירה.
* **טקסט המודל לעולם אינו HTML.** כל ערך שמגיע מהמודל עובר `html.escape` לפני
  שהוא נכנס לתבנית, ורק אחרי ההברחה מומרים זוגות `**` ל-`<strong>`. זה הדפוס
  שכבר שומר על `app._answer_actions` מפני `</script>` בתוך תשובה.
* **תוויות מומצאות מקבלות טיפול גנרי, לא סמנטי.** המודל ממציא תוויות מדי פעם
  (`**סנקציה:**`). תווית מוכרת מקבלת את הטיפול שלה; כל `**X:**` אחרת מקבלת
  שורת-תווית גנרית — כדי שהתשובה לא תיראה חצי-מעוצבת. שום משמעות לא מומצאת.

ריצות-פרוזה חייבות להישאר שלמות
-------------------------------
Streamlit נותן ל-`stMarkdownContainer` ‏`margin-bottom:-1rem` שמקזז את ה-16px
של ה-`<p>` האחרון. לכן שתי קריאות `st.markdown` עוקבות נדבקות בלי רווח כלל.
הפירוק כאן אוסף שורות-פרוזה עוקבות לבלוק `md` **אחד** בדיוק מהסיבה הזו — לא
כאופטימיזציה.
"""
from __future__ import annotations

import html
import re

# סימני-כיווניות שהמודל משתיל מדי פעם בתוך טקסט עברי־מעורב־מספרים. הם בלתי
# נראים ולכן היו שוברים התאמת-תווית בשקט מוחלט.
_BIDI = "‎‏‪‫‬‭‮⁦⁧⁨⁩"

# `**תווית:**` או `**תווית**:` — שתי הצורות נצפו. הגבול של 28 תווים מפריד
# תווית מפסקה שנפתחת בהדגשה ארוכה.
_LABEL_RE = re.compile(r"^\s*\*\*\s*([^*\n]{1,28}?)\s*(?::\s*\*\*|\*\*\s*:)\s*(.*)$")

# "מה הפקודות לא קובעות" הוא הפריט היחיד בתשובה שהפרומפט מחייב אבל **לא** נותן
# לו תווית — כלל התמציתיות מגביל אותו למשפט אחד בניסוח חופשי. לכן זיהוי לפי
# תוכן, ורק בפתיחת פסקה (45 התווים הראשונים): אזכור כזה באמצע פסקה הוא חלק
# מהנימוק, לא הסתייגות עצמאית. פספוס משאיר את המשפט כפרוזה — בדיוק כמו היום.
_NOTE_RE = re.compile(
    r"הפקודות\s+(?:שסופקו\s+)?(?:אינן|לא)\s+"
    r"(?:קובעות|נוקבות|מפרטות|מגדירות|מתייחסות|מסדירות)"
)
_NOTE_SCAN = 45

# הסעיף בסוף שורת-המקור. נלקח מ"סעיף" ועד סוף השורה כדי שגם "סעיף 4(ב)" ו-
# "סעיפים 3-5" ייכנסו שלמים לתג. חלופה מפורשת ולא `סעיפים?`: ברבים העברי הסופית
# משתנה (ף->פ), ולכן הסיומת האופציונלית הייתה מפספסת דווקא את היחיד.
_CLAUSE_RE = re.compile(r"[\s,—–\-·]*(?<!\S)((?:סעיף|סעיפים)\s+\S.*)$")

# התוויות המוכרות. המפתח הוא הטקסט כפי שהמודל כותב אותו; ההתאמה עצמה מתחשבת
# בקידומות ("תנאים \\ הגבלות" הוא "תנאים"), ראו _kind_of.
_LEAD = ("פסיקה", "תשובה")
_FIELD_EXACT = ("מי מאשר", "מי מוסמך", "דרגה נדרשת לאישור", "דרגה נדרשת",
                "מי מאשר את החריגה")
_ROUTE_OUT = 'לא נקבע בפקודות מטכ"ל'
_ROUTE_MISS = "טרם במאגר"


def _clean(text: str) -> str:
    return text.strip().strip(_BIDI).strip()


def _kind_of(label: str) -> str:
    """סוג הבלוק לפי התווית. תווית לא-מוכרת -> שורת-תווית גנרית."""
    if label.startswith("התנהלות"):
        return "side"
    if label in ("מקור", "מקורות"):
        return "src"
    if label in _LEAD:
        return "lead"
    if label == _ROUTE_OUT:
        return "route_out"
    if label == _ROUTE_MISS:
        return "route_miss"
    if label.startswith("הערה"):
        return "note"
    if label.startswith("תנאים") or label.startswith("סייג") or label in _FIELD_EXACT:
        return "field"
    return "field"


def _split_sources(value: str) -> list[tuple[str, str]]:
    """שורת-מקור -> [(שם הפקודה, הסעיף)]. ';' בלבד מפריד בין מקורות: פסיק
    מופיע בתוך שמות פקודות ("עבודה ומנוחה, סעיף 4") ולכן אינו מפריד."""
    out: list[tuple[str, str]] = []
    for part in value.split(";"):
        part = _clean(part)
        if not part:
            continue
        m = _CLAUSE_RE.search(part)
        if m:
            title = _clean(part[: m.start()])
            clause = _clean(m.group(1))
        else:
            title, clause = part, ""
        # ההפרדה שהמודל שם בין מספר הפקודה לשמה משתנה (מקף, פסיק, כלום);
        # מאחדים לנקודה-אמצעית אחת כדי שכל שורות-המקור ייראו זהות.
        title = re.sub(r"\s*[—–-]\s*", " · ", title, count=1)
        if title or clause:
            out.append((title, clause))
    return out


def blocks(body: str) -> list[tuple[str, object]]:
    """גוף התשובה -> רשימת בלוקים לרינדור.

    ‏`("md", טקסט)` נשאר מרקדאון ומרונדר ב-`st.markdown` רגיל; כל שאר הסוגים
    עוברים ל-`to_html`. הסדר נשמר במדויק — התשובה לא מסודרת מחדש, רק נלבשת.
    """
    out: list[tuple[str, object]] = []
    run: list[str] = []

    def flush() -> None:
        text = "\n".join(run).strip("\n")
        run.clear()
        if text.strip():
            out.append(("md", text))

    at_para_start = True
    for line in body.split("\n"):
        stripped = line.strip()
        if not stripped:
            run.append(line)
            at_para_start = True
            continue

        m = _LABEL_RE.match(line)
        if m:
            label, value = _clean(m.group(1)), _clean(m.group(2))
            kind = _kind_of(label)
            flush()
            if kind == "src":
                srcs = _split_sources(value)
                out.append(("src", srcs) if srcs else ("field", (label, value)))
            elif kind == "note":
                out.append(("note", value))
            else:
                out.append((kind, (label, value)))
            at_para_start = True
            continue

        if at_para_start and _NOTE_RE.search(stripped[:_NOTE_SCAN]):
            flush()
            out.append(("note", stripped))
            at_para_start = True
            continue

        run.append(line)
        at_para_start = False

    flush()
    return out


def stream_split(text: str) -> tuple[str, str]:
    """‏(מוגמר, זנב) — הגבול הוא השורה השלמה האחרונה.

    זה כל מה שהזרימה המעוצבת צריכה. הדקדוק כאן הוא **שורתי**: תווית נקבעת
    כשהשורה שלה נגמרת, ולפניה אי-אפשר לדעת אם `**מקור` יהפוך ל-`**מקור:**`
    או לטקסט מודגש. לכן מעצבים רק שורות שהסתיימו, והשורה שבאמצע כתיבה
    נשארת טקסט — כך כל שדה "ננעל" פעם אחת, ברגע שהשורה שלו נסגרת, במקום
    לקפוץ פנימה והחוצה תוך כדי הקלדה.

    ⚠ המסקנה החשובה לקורא: **רק הבלוק האחרון של `blocks(מוגמר)` עוד עשוי
    להשתנות** — ריצת-פרוזה ממשיכה לבלוע שורות עד שמגיעה תווית. כל בלוק
    שלפניו סופי, ולכן אפשר לצבוע אותו פעם אחת ולא לשלוח אותו שוב. זה ההבדל
    בין זרימה מעוצבת לבין רינדור-מחדש של כל התשובה בכל צ'אנק, שהיה משדר את
    כל ה-HTML שוב ושוב ומאריך את ההמתנה על קו סלולרי.
    """
    cut = text.rfind("\n")
    if cut < 0:
        return "", text
    return text[: cut + 1], text[cut + 1:]


def _inline(text: str) -> str:
    """טקסט מודל -> HTML בטוח. ההברחה קודמת להמרה, ולכן `<script>` בתשובה
    נשאר טקסט. quote=False: הטקסט נכנס לצומת-טקסט ולעולם לא לתכונה, וגרשיים
    הם התו הנפוץ ביותר בעברית צבאית (פ"מ, סא"ל)."""
    out = html.escape(text, quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)


_DOC_SVG = (
    "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.7' "
    "stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>"
    "<path d='M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z'></path>"
    "<path d='M14 3v5h5'></path><path d='M9 13h6'></path><path d='M9 17h4'></path></svg>"
)

# השברון הכפול של המותג בגרסה מעומעמת — אותה צורה בדיוק כמו .cai-splash-chev
# ו-.cai-entry-chev, בקנה מידה של שורה. הוא מסמן את הרגע שבו התשובה מפנה הלאה
# במקום להכריע.
_CHEV = "<span class='cai-ans-chev'><span></span><span></span></span>"


def _wrap(inner: str, solo: bool = False) -> str:
    """עטיפת-מרווח סביב הבלוק המעוצב.

    ‏`.cai-ans` נושא **רק** מרווחים: ‏`padding-bottom` שמקזז את
    `margin-bottom:-1rem` של `stMarkdownContainer` (הדפוס שכבר עובד ב-
    `.verdict-stack` ו-`.cai-escal`; מרווח-שוליים נבדק שם ונפסל — הוא קורס דרך
    העוטף). המעטפת נדרשת כי בלוק עם רקע או מסגרת אינו יכול לשאת בעצמו ריפוד
    תחתון של 16px — הקופסה הייתה נמתחת 16px מתחת לטקסט שלה.
    """
    return f"<div class='cai-ans{' cai-ans-solo' if solo else ''}'>{inner}</div>"


def to_html(block: tuple[str, object], *, route_label: bool = True) -> str | None:
    """HTML לבלוק אחד, או None לבלוק `md` (שהאפליקציה מרנדרת כמרקדאון).

    ‏`route_label=False` משמיט את תווית בלוק-הניתוב. הקורא מעביר את זה כשהצ'יפ
    הנייטרלי כבר נורה: אז "לא נקבע בפקודות מטכ"ל" מופיע גם בצ'יפ, גם במשפט
    שהמודל כתב וגם כאן, ושלושה עותקים של אותו מסר נקראים כתקלה — משתמש שקרא
    מסך כזה הסיק שהאפליקציה סירבה על תוכן שקיים במאגר. כשהתשובה כן הכריעה חלק
    מהשאלה הצ'יפ צבוע ולא נייטרלי, והתווית היא המקום היחיד שאומר את זה.
    """
    kind, payload = block
    if kind == "md":
        return None

    if kind == "src":
        rows = "".join(
            f"<div class='r'><span class='ic'>{_DOC_SVG}</span>"
            f"<span class='t'>{_inline(title)}</span>"
            + (f"<span class='c'>{_inline(clause)}</span>" if clause else "")
            + "</div>"
            for title, clause in payload  # type: ignore[union-attr]
        )
        return _wrap(f"<div class='cai-ans-src'>{rows}</div>")

    if kind == "note":
        return _wrap(
            "<div class='cai-ans-note'><span class='ic'>ⓘ</span>"
            f"<span class='v'>{_inline(str(payload))}</span></div>"
        )

    label, value = payload  # type: ignore[misc]

    if kind == "lead":
        return _wrap(
            "<div class='cai-ans-lead'>"
            f"<span class='l'>{_inline(label)}</span>"
            f"<span class='v'>{_inline(value)}</span></div>"
        )

    if kind == "side":
        return _wrap(
            "<div class='cai-ans-side'>"
            f"<span class='p'>{_inline(label)}</span>"
            f"<span class='v'>{_inline(value)}</span></div>"
        )

    if kind in ("route_out", "route_miss"):
        head = f"<span class='l'>{_inline(label)}</span>" if route_label else ""
        return _wrap(
            f"<div class='cai-ans-route'>{_CHEV}<div class='bd'>{head}"
            f"<span class='v'>{_inline(value)}</span></div></div>"
        )

    # field — תווית תלויה. בלי ערך היא כותרת לרשימה שמגיעה אחריה (`**תנאים:**`
    # ואז נקודות), ואז המעטפת מצטמצמת כדי שהתווית תיצמד לרשימה שלה.
    return _wrap(
        "<div class='cai-ans-f'>"
        f"<span class='l'>{_inline(label)}</span>"
        + (f"<span class='v'>{_inline(value)}</span>" if value else "")
        + "</div>",
        solo=not value,
    )
