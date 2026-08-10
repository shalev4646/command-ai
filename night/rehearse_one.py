"""Run the one-order rehearsal end to end: measure, fix, re-index, re-measure.

Target is 33.0304 (בדיקה וחקירת מצ"ח) — the largest of the 27 orders with no
`sections`, and one where the failure is already visible: a long anecdotal
question about a soldier's rights hands the model that order's `chunk14`,
which is a half-sentence about signing a statement followed by the order's
publication colophon ("עודכנה בתאריכים: 3 ביוני 1979; ..."). That is the exact
failure vector_store.py:554 documents from the live "לאיים במשפט" miss.

The key-facts written below are extracted from the order's own raw_text, with
clause numbers preserved. Two deliberate omissions:

  * Nothing about a right to counsel. The order contains zero occurrences of
    עורך דין / סניגור / להיוועץ — asserting one would be inventing law, and a
    clause mentioning lawyers would also make the order retrieve FOR lawyer
    questions it cannot answer, which is worse than silence.
  * No "what this order does not cover" clause. None of the 71 curated orders
    use that pattern; introducing it here would be an untested change riding
    along inside a rehearsal.

CONTENT REVIEW IS STILL OWED. This proves the mechanism, not the wording.
"""
from __future__ import annotations

import json

import backend
from night import config as C
from night.rehearse import compare, context_for, doc_path, gate_snapshot

DOC_ID = "33.0304"

KEY_FACTS = {
    "id": "key-facts",
    "title": "עיקרי הפקודה — בדיקה וחקירת מצ\"ח: זכויות חשוד ועד",
    "clauses": [
        {
            "number": "האם חייבים לענות לחוקר — אזהרת חשוד ונוסחה",
            "text": "קצין בודק לא ייגבה עדות מאדם שהוא סבור שיש מקום להאשימו בעבירה, ולא יקבל "
                    "ממנו אמרה, אלא לאחר שהזהיר אותו — בשפה שהחשוד שומע ובמילים פשוטות — ולאחר "
                    "שראה שהאזהרה הובנה. נוסח האזהרה: \"אתה חשוד בביצוע עבירה פלונית. האם ברצונך "
                    "לומר דבר בקשר לעבירה האמורה? אין אתה חייב לומר דבר, אם אין רצונך בכך, ואולם "
                    "כל מה שתאמר יירשם ויכול לשמש ראיה במשפטך\" (סעיפים 44–45).",
        },
        {
            "number": "מה חייבים להודיע לחשוד שנעצר",
            "text": "חשוד שנעצר — הקצין הבודק חייב להודיע לו מה החשד נגדו. כן רשאי הקצין הבודק "
                    "לצלמו ולקחת את טביעות אצבעותיו, ככל שיידרש לצורכי החקירה (סעיף 43).",
        },
        {
            "number": "מתי עד רשאי לסרב לטביעות אצבע או לצילום",
            "text": "קצין בודק רשאי, לשם גילוי האמת, ליטול או לצלם ראיות מוחשיות, ולקחת מכל עד את "
                    "טביעת אצבעותיו או לצלמו. אולם העד רשאי לסרב לדרישה זו אם יש בדבר כדי להפלילו "
                    "או לספק חומר אישום נגד עצמו (סעיף 28).",
        },
        {
            "number": "האם ממשיכים לחקור בזמן מסירת אמרה",
            "text": "חשוד או עציר המוסר אמרה לא ייחקר על ידי הקצין הבודק, ולא יישאל אלא שאלות "
                    "הדרושות להבהיר את אמרתו (סעיף 49).",
        },
        {
            "number": "אזהרה כשהחשד מתעורר תוך כדי מסירת האמרה",
            "text": "חשוד או עציר המביע רצון למסור אמרה — יזהירו הקצין הבודק תחילה. נמסרה האמרה "
                    "בטרם היה סיפק בידי הקצין להזהירו, או שהמסקנה כי יש מקום להאשימו התגבשה תוך "
                    "כדי מסירת האמרה — יזהירנו בהזדמנות הראשונה (סעיף 48).",
        },
        {
            "number": "חתימה על האמרה ואמרה בשפה זרה",
            "text": "הקצין הבודק יקרא את הדברים לפני מוסר האמרה, ייתן לו הזדמנות לתקנם ויחתימו "
                    "עליהם; סירב מוסר האמרה לחתום — יירשם דבר הסירוב. נמסרה האמרה בשפה שאין הקצין "
                    "הבודק שומעה, תירשם כלשונה בידי מי שהקצין הבודק ימנה לשם כך (סעיף 47).",
        },
    ],
}

# Long, anecdotal, and not lifted from any anchor — the shape that pushes a
# query into anchor-win territory, which is where the missing block bites.
PROBE = ("מצח פתחו לי בדיקה אחרי שהאשימו אותי במשהו ביחידה ואני לא יודע מה הזכויות "
         "שלי ואם אני חייב לדבר איתם בלי עורך דין")


def show(tag: str) -> None:
    labels, ctx = context_for(PROBE)
    C.log(f"[{tag}] chunks: {labels}")
    C.log(f"[{tag}] {DOC_ID} slots: {sum(1 for l in labels if l.startswith(DOC_ID))}"
          f" / {len(labels)}   context words: {len(ctx.split())}")


def main() -> None:
    C.log("=" * 72)
    C.log(f"[rehearse] one-order rehearsal on {DOC_ID}")

    C.log("[rehearse] BEFORE — gate snapshot (415 cases, router bypassed)")
    before_gate = gate_snapshot()
    show("before")

    path = doc_path(DOC_ID)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("sections"):
        raise SystemExit(f"{DOC_ID} already has sections — pick another order")
    doc["sections"] = [KEY_FACTS]
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    C.log(f"[rehearse] wrote key-facts ({len(KEY_FACTS['clauses'])} clauses) to {path.name}")

    backend.load_documents.cache_clear() if hasattr(backend.load_documents, "cache_clear") else None
    from storage.vector_store import index_document
    fresh = json.loads(path.read_text(encoding="utf-8"))
    n = index_document(fresh)
    C.log(f"[rehearse] re-indexed {DOC_ID}: {n} chunks")

    show("after")
    C.log("[rehearse] AFTER — gate snapshot")
    after_gate = gate_snapshot()

    diff = compare(before_gate, after_gate)
    C.log(f"[rehearse] gate cases changed: {diff['n_changed']}/{diff['n_cases']}")
    for k, (b, a) in list(diff["changed"].items())[:15]:
        C.log(f"[rehearse]   {k[:70]}\n              {b} -> {a}")

    (C.OUT / "rehearse_gate_diff.json").write_text(
        json.dumps(diff, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
