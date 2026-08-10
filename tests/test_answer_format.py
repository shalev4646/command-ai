# -*- coding: utf-8 -*-
"""שער שפת-התשובה — answer_format.blocks / to_html.

Run: venv\\Scripts\\python.exe tests\\test_answer_format.py
Prints only ASCII (cp1252 console pitfall) -- Hebrew lives in the assertions,
never in printed output.

The module is pure string work on purpose, so this suite covers the whole
behaviour without Streamlit and without a paid API call. Two classes of
assertion here:

* **Grammar** -- each label the prompt mandates lands in its own block kind.
* **Non-destruction** -- an answer whose grammar was NOT recognized must come
  out looking exactly like today (one markdown run), and no model text may
  ever reach the page as markup.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import answer_format as af  # noqa: E402


def kinds(text):
    return [k for k, _ in af.blocks(text)]


def payload(text, kind):
    return [p for k, p in af.blocks(text) if k == kind]


def rendered(text):
    return "".join(af.to_html(b) or b[1] for b in af.blocks(text))


# ── grammar: the mandated labels ─────────────────────────────────────────────

def test_source_line_splits_into_title_and_clause():
    src = payload('**מקור:** פ"מ 33.0213 — עבודה ומנוחה, סעיף 4', "src")[0]
    title, clause = src[0]
    assert clause == "סעיף 4", clause
    assert "33.0213" in title and "עבודה ומנוחה" in title
    assert "סעיף" not in title, "the clause must leave the title"


def test_source_without_a_clause_still_renders():
    src = payload('**מקור:** פ"מ 33.0213 עבודה ומנוחה', "src")[0]
    assert src[0][1] == "", "no clause -> empty tag, not an invented one"


def test_comma_does_not_split_sources_but_semicolon_does():
    """Order titles carry commas ("עבודה ומנוחה, סעיף 4"); a comma split would
    shred a single source into two."""
    one = payload('**מקור:** פ"מ 33.0213 עבודה ומנוחה, סעיף 4', "src")[0]
    assert len(one) == 1, one
    two = payload('**מקור:** פ"מ 33.0213 סעיף 4; פ"מ 33.0351 סעיף 12', "src")[0]
    assert len(two) == 2, two
    assert two[1][1] == "סעיף 12"


def test_conditions_label_with_a_list_stays_attached_to_the_list():
    out = af.blocks("**תנאים:**\n- מנוחה רצופה 7 שעות\n- חריגה בנספח א'")
    assert [k for k, _ in out] == ["field", "md"]
    assert out[0][1] == ("תנאים", "")
    assert "cai-ans-solo" in af.to_html(out[0])
    assert af.to_html(out[1]) is None, "the list must stay markdown"


def test_conditions_variants_are_all_fields():
    for label in ("תנאים", "תנאים \\ הגבלות", "תנאים / הגבלות", "סייגים",
                  "מי מאשר", "דרגה נדרשת לאישור"):
        assert kinds(f"**{label}:** ערך") == ["field"], label


def test_two_sided_conduct_paragraphs_are_their_own_kind():
    text = ("**התנהלות החייל:** דרש מחליף ואיים במשפט — אינה לגיטימית.\n"
            "**התנהלות האחראית:** הודיעה כי היא מטפלת — אינה מקימה עילה.")
    assert kinds(text) == ["side", "side"]
    assert payload(text, "side")[0][0] == "התנהלות החייל"


def test_routing_labels_match_scope_routes_exactly():
    """These two strings are contract with scope_routes/backend rule 2א — a
    drift there silently downgrades the routing block to a generic field."""
    import scope_routes
    out_label = scope_routes.MARK_OUT_OF_SCOPE.strip("*: ")
    miss_label = scope_routes.MARK_MISSING.strip("*: ")
    assert kinds(f"{scope_routes.MARK_OUT_OF_SCOPE} המוסד לביטוח לאומי — פנה אליו.") == ["route_out"]
    assert kinds(f"{scope_routes.MARK_MISSING} החזר נסיעות") == ["route_miss"]
    assert af._ROUTE_OUT == out_label, (af._ROUTE_OUT, out_label)
    assert af._ROUTE_MISS == miss_label, (af._ROUTE_MISS, miss_label)


def test_lead_labels_when_the_chip_declined():
    """_verdict_chip returns a compound ruling line to the body untouched; it
    must still read as the answer's lead, not as loose bold text."""
    assert kinds("**פסיקה:** אסור; מותר בתנאים") == ["lead"]
    assert kinds("**תשובה:** מגישים בקשה ליחידה") == ["lead"]


# ── the one free-form item: "מה הפקודות לא קובעות" ───────────────────────────

def test_free_form_not_determined_sentence_becomes_a_note():
    for line in ("מה הפקודות לא קובעות: משך מרבי למסדר ניקיון.",
                 "הפקודות אינן נוקבות בסכום המדויק.",
                 "הפקודות שסופקו אינן מגדירות מהו פרק זמן סביר."):
        assert kinds(line) == ["note"], line


def test_the_same_words_mid_paragraph_stay_prose():
    """Deep inside a paragraph the phrase is part of the reasoning, not a
    standalone caveat -- chipping it out would break the sentence."""
    text = ("הכלל חל על כל סוגי השירות, ולכן מה שנותר פתוח הוא שהפקודות "
            "אינן קובעות מהו פרק זמן סביר.")
    assert kinds(text) == ["md"]


def test_note_label_is_a_note_not_a_field():
    assert kinds("**הערה:** ההסדר חל רק בשגרה.") == ["note"]


def test_the_routing_label_drops_when_the_neutral_chip_already_said_it():
    """Pill + the model's refusal sentence + this label = the same words three
    times. A reader hit that screen and concluded the app had refused on
    content the corpus holds."""
    block = af.blocks('**לא נקבע בפקודות מטכ"ל:** המוסד לביטוח לאומי — פנה אליו.')[0]
    with_label = af.to_html(block)
    without = af.to_html(block, route_label=False)
    assert "לא נקבע בפקודות" in with_label
    assert "לא נקבע בפקודות" not in without
    for html_out in (with_label, without):
        assert "המוסד לביטוח לאומי" in html_out, "the routing itself never drops"
        assert "cai-ans-chev" in html_out


# ── non-destruction ──────────────────────────────────────────────────────────

def test_a_plain_answer_is_untouched():
    text = "המידע לא קיים בפקודות שסופקו.\n\nאפשר לנסח את השאלה מחדש."
    out = af.blocks(text)
    assert [k for k, _ in out] == ["md"]
    assert out[0][1] == text, "a run must survive byte-for-byte"


def test_consecutive_prose_lines_stay_in_one_run():
    """Streamlit's stMarkdownContainer margin-bottom:-1rem cancels the last
    <p>'s margin, so two adjacent st.markdown calls render with ZERO gap.
    Splitting prose would look like a rendering bug."""
    text = "שורה ראשונה.\n\nשורה שנייה.\n\nשורה שלישית."
    assert kinds(text) == ["md"]


def test_a_long_bold_opening_is_not_a_label():
    text = "**זהו משפט מודגש ארוך שאינו תווית כלל ולכן נשאר פרוזה:** המשך."
    assert kinds(text) == ["md"]


def test_colon_outside_the_asterisks_also_matches():
    assert kinds('**מקור**: פ"מ 33.0213 סעיף 4') == ["src"]


def test_bidi_marks_do_not_break_label_matching():
    assert kinds("**‏מקור‎:** פ\"מ 33.0213 סעיף 4") == ["src"]


def test_an_invented_label_gets_the_generic_field_treatment():
    """The model invents labels; leaving those bare would make the answer look
    half-designed. Generic row, no invented semantics."""
    assert kinds("**סנקציה:** עד 14 ימי ריתוק") == ["field"]


def test_every_line_survives_the_split():
    text = ('**פסיקה:** מותר בתנאים\n'
            '**מקור:** פ"מ 33.0213 עבודה ומנוחה, סעיף 4\n'
            '**תנאים:**\n- מנוחה רצופה 7 שעות\n'
            '**מי מאשר:** סא"ל ומעלה\n'
            'הפקודות אינן קובעות משך מרבי.\n'
            'עומד לרשותך לפנות למש"קית ת"ש.')
    flat = re.sub(r"<[^>]+>", " ", rendered(text))
    for needle in ("מותר בתנאים", "33.0213", "סעיף 4", "מנוחה רצופה 7 שעות",
                   'סא"ל ומעלה', "אינן קובעות משך מרבי", 'מש"קית ת"ש'):
        assert needle in flat, needle


# ── safety ───────────────────────────────────────────────────────────────────

def test_model_text_can_never_reach_the_page_as_markup():
    """The body used to render through st.markdown WITHOUT unsafe_allow_html,
    which made it safe by construction. Now that our rows are raw HTML, the
    escape is the only thing holding that line."""
    html_out = rendered('**מקור:** <script>alert(1)</script> סעיף 4')
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_bold_inside_a_value_survives_as_strong():
    assert "<strong>" in rendered('**תנאים:** אישור **סא"ל** בכתב')


def test_gershayim_are_not_entity_escaped():
    """quote=False keeps פ\"מ / סא\"ל readable in the source and identical on
    screen; &quot; everywhere would bloat every answer."""
    assert "&quot;" not in rendered('**מקור:** פ"מ 33.0213 סעיף 4')


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as exc:
                failures += 1
                print("FAIL", name, "-", str(exc).encode("ascii", "replace").decode())
    print(("FAILURES: %d" % failures) if failures else "ALL PASS")
    sys.exit(1 if failures else 0)
