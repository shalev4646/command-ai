"""Escalation paths ("למי פונים") for the loaded orders.

הכוונה כללית למסלולי פנייה בלבד — לא פסיקה משפטית ולא תחליף להוראות הפקודה
עצמה; הפקודה המצוטטת בתשובה היא המקור המחייב.

A deterministic document_id -> referral-chain lookup (zero LLM tokens): the
UI renders the primary source's chain under the answer, so a soldier who
just learned a rule also sees which door to knock on first. Keyed by the
document_id values stored in storage/json_store (backend.load_documents);
an id without a curated entry — including any future ingestion — falls back
to DEFAULT_PATH, so coverage can never silently gap.
"""


def _path(steps: list[str], note: str | None = None) -> dict:
    return {"steps": steps, "note": note}


# Shared chains. Steps are short role names (2-4 words), ordered first stop
# -> last resort; a step list is reused across every order in its domain so
# the UI stays consistent between related answers.
_TASH = ['מש"קית ת"ש', 'קצינת ת"ש', "ע' מפקד היחידה"]
_PERSONNEL = ['מפקד ישיר', 'שלישות היחידה', 'מפקד היחידה']
_MEDICAL = _path(
    ['מרפאה יחידתית', 'קר"פ היחידה', 'ראש מדור רפואה'],
    'על החלטה רפואית אפשר לבקש בדיקה חוזרת או חוות דעת נוספת דרך המרפאה.',
)
_RELIGION = _path(['רב היחידה', 'מפקד היחידה', 'הרבנות הצבאית הראשית'])
_ABSENCE = _path(
    ['מפקד ישיר', 'מש"קית ת"ש', 'היוועצות בסנגוריה הצבאית'],
    'אם ההיעדרות נובעת ממצוקה אישית או כלכלית — שתף את גורמי הת"ש מוקדם ככל האפשר.',
)

# The soldiers'-complaints commissioner is deliberately the LAST step of the
# default chain: the order itself (33.0336) expects unit-level handling
# first, and pointing every answer straight at the commissioner would bury
# the channel that exists for unresolved cases.
DEFAULT_PATH = _path(
    ['מפקד ישיר', 'מש"קית ת"ש', 'נציב קבילות החיילים'],
    'נציב קבילות החיילים הוא כתובת אחרונה — לאחר שמוצתה הפנייה בתוך היחידה.',
)

# document_id -> path. Orders NOT listed here ride DEFAULT_PATH on purpose
# (routine/camp-regime orders whose only real first stop is the direct
# commander): 2.0101, 5040.05, 05.104, 33-05-01, 33.0220, 33.0501, 35.0818,
# PM-33.0137, PM-33.0161, PM-33.0202, PM-33.0207, PM-33.0213.
PATHS: dict[str, dict] = {
    # welfare / money (ת"ש)
    '35.0201': _path(_TASH),   # דמי קיום
    '35.0210': _path(_TASH),   # תשלומים למשפחות
    '35.0808': _path(_TASH),   # חיילים בודדים
    '36.0218': _path(_TASH),   # הנחות בטיסות

    # discipline / arrest / pardon — every chain here must carry the
    # defense-counsel consultation and an appeal step: these are the orders
    # where a wrong first move costs rights, not just time.
    'PM-33.0302': _path(
        ['מפקד ישיר', 'היוועצות בסנגוריה הצבאית', 'ערר לקצין שיפוט בכיר'],
        'לפני דיון משמעתי עומדת לך זכות היוועצות; ערר מוגש בתוך המועד הקבוע בפקודה.',
    ),
    'PM-33.0309': _path(
        ['היוועצות בסנגוריה הצבאית', 'ערר על המעצר', 'נציב קבילות החיילים'],
        'זכות ההיוועצות בסנגוריה עומדת לך מרגע המעצר.',
    ),
    '30.33': _path(
        ['היוועצות בסנגוריה הצבאית', 'בקשת חנינה דרך היחידה', 'ערר על ההחלטה'],
    ),
    '33.0111': _path(
        ['היוועצות בסנגוריה הצבאית', 'מפקד ישיר', 'קב"ן — תמיכה אישית'],
        'לפני חקירת מצ"ח עומדת לך זכות היוועצות בסנגור.',
    ),
    'PM-33.0352': _path(['מפקד ישיר', 'ערר למפקד בכיר', 'נציב קבילות החיילים']),
    '31.0513': _ABSENCE,
    '31.0521': _ABSENCE,

    # complaints — the one order whose chain IS the commissioner
    '33.0336': _path(
        ['מפקד ישיר', 'נציב קבילות החיילים'],
        'את הקבילה לנציב אפשר להגיש ישירות, בלי לעבור דרך שרשרת הפיקוד.',
    ),

    # medical
    '61.0104': _MEDICAL,
    '36.0413': _MEDICAL,
    '36.0511': _MEDICAL,

    # mental distress — gentle by design: no gatekeepers in the chain, and
    # the note says so explicitly (direct, confidential, always available)
    '33.0219': _path(
        ['קב"ן — פנייה ישירה', 'קו הקשב', 'מפקד או חבר קרוב'],
        'אפשר לפנות לקב"ן ישירות, בלי אישור ובלי שאלות — הפנייה חסויה. '
        'לא חייבים להחזיק בזה לבד, ובכל שעה יש עם מי לדבר.',
    ),

    # sexual harassment — parallel addresses, not an escalation ladder
    '33.0145': _path(
        ['הממונה למניעת הטרדה מינית', 'מצ"ח — הגשת תלונה', 'קב"ן — תמיכה אישית'],
        'אפשר לפנות לכל גורם ישירות ובאופן חסוי; אין סדר מחייב, והבחירה אם להתלונן היא שלך בלבד.',
    ),

    # religion
    'PM-34.0101': _RELIGION,
    'PM-34.0205': _RELIGION,
    '31.0901': _path(['מפקד ישיר', 'רב היחידה', 'מש"קית ת"ש']),

    # reserve
    '013.3': _path(
        ['שליש יחידת המילואים', 'מוקד המילואים', 'מדור תשלומי מילואים'],
        'השגה על חישוב התגמול מוגשת בכתב בתוך המועד הקבוע בפקודה.',
    ),
    '31.0703': _path(['מפקד יחידת המילואים', 'שליש היחידה', 'מוקד המילואים']),
    '31.0603': _path(
        ['מפקד יחידת המילואים', 'ולת"ם — בקשת דחייה', 'ערר על ההחלטה'],
        'בקשות ולת"ם מוגשות מראש, בתוך המועדים הקבועים בפקודה.',
    ),

    # leave / career / release — approvals and personnel paperwork all run
    # through the unit adjutancy, whatever the specific order
    'PM-35.0402': _path(_PERSONNEL),   # חופשות חובה
    '36.0401': _path(_PERSONNEL),      # חופשות קבע
    '31.0517': _path(_PERSONNEL),      # חל"ת קבע
    '31.0701': _path(
        _PERSONNEL,
        'בקשת יציאה לחו"ל מוגשת מראש דרך השלישות, בתוך המועדים הקבועים בפקודה.',
    ),
    '31.0103': _path(_PERSONNEL),      # שחרור — פעולות היחידה
    '31.0203': _path(_PERSONNEL),      # התחייבות לקבע
    '3.0501': _path(_PERSONNEL),       # שירות קבע
    '36.0527': _path(_PERSONNEL),      # החזרת הטבות בשחרור
    '33.0115': _path(_PERSONNEL),      # עבודה פרטית — אישור מפקד יחידה
}


def path_for(doc_id: str) -> dict:
    """The referral chain for a document id: {"steps": [...], "note": str|None}.

    Total by construction — unknown/missing ids get DEFAULT_PATH, so the UI
    never has to special-case a freshly ingested order.
    """
    return PATHS.get(doc_id, DEFAULT_PATH)
