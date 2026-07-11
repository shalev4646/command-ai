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
import re


def _path(steps: list[str], note: str | None = None) -> dict:
    return {"steps": steps, "note": note}


# Shared chains. Steps are short role names (2-4 words), ordered first stop
# -> last resort; a step list is reused across every order in its domain so
# the UI stays consistent between related answers.
_TASH = ['מש"קית ת"ש', 'קצינת ת"ש', "ע' מפקד היחידה"]
_PERSONNEL = ['מפקד ישיר', 'שלישות היחידה', 'מפקד היחידה']
_MEDICAL = _path(
    ['מרפאה יחידתית', 'רופא היחידה', 'ועדה רפואית'],
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
# commander): 5040.05, 05.104, 33-05-01, 33.0220, 33.0501, 35.0818,
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
        ['קב"ן — פנייה ישירה', 'מרפאת בריאות הנפש', 'מפקד או חבר קרוב'],
        'אפשר לפנות לקב"ן ישירות, בלי אישור מראש — הפנייה לטיפול נפשי חסויה. '
        'לא צריך להתמודד עם זה לבד: מפקד, חבר ליחידה, או קו הסיוע הנפשי '
        'ער"ן (1201, בכל שעה) — כולם כתובת.',
    ),

    # sexual harassment — parallel addresses, not an escalation ladder
    '33.0145': _path(
        ['הממונה למניעת הטרדה מינית', 'מצ"ח — הגשת תלונה', 'קב"ן — תמיכה אישית'],
        'אפשר לפנות לכל גורם ישירות ובאופן חסוי; אין סדר מחייב, והבחירה אם להתלונן היא שלך בלבד.',
    ),

    # weapons on leave — authorization sits with a senior commander (סא"ל+)
    # and the armory holds the weapon, so the welfare NCO of DEFAULT would
    # misdirect; this order carries its own chain.
    '2.0101': _path(
        ['מפקד ישיר', 'מפקד מוסמך (סא"ל ומעלה)', 'נציב קבילות החיילים'],
        'אישור לנשיאת נשק אישי בחופשה ניתן בידי מפקד בכיר (סא"ל ומעלה) '
        'לפי הפקודה; את הנשק מפקידים בנשקיית היחידה.',
    ),

    # religion
    'PM-34.0101': _RELIGION,
    'PM-34.0205': _RELIGION,
    '31.0901': _path(['מפקד ישיר', 'רב היחידה', 'מש"קית ת"ש']),

    # reserve
    '013.3': _path(
        ['שליש יחידת המילואים', 'מוקד המילואים', 'נציב קבילות החיילים'],
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


# Orders whose whole subject IS the referral path — complaints, distress,
# harassment, discipline/arrest: the chain renders on every question about
# them, trigger words or not (a soldier reading about a summons should see
# the defense-counsel step even if he only asked "what happens if...").
_ALWAYS_SHOW = {
    '33.0336',      # נציב קבילות
    '33.0219',      # מצוקה נפשית
    '33.0145',      # הטרדה מינית
    'PM-33.0302',   # דין משמעתי
    'PM-33.0309',   # מעצר וחיפושים
    '30.33',        # חנינה
    '33.0111',      # סמים / חקירת מצ"ח
    'PM-33.0352',   # מניעת חופשה (עונשית)
}

# A question earns the strip when the asker has something to PURSUE: an
# entitlement to claim, a request to file, a refusal/denial to challenge, a
# punishment to appeal, or an authority question ("המפקד יכול...?"). Plain
# information questions ("מותר להכניס נרגילה לבסיס?") stay clean — the
# chain under them is noise. NOTE the singular "מגיע ל[י/נו/ו/ה]" pattern:
# a bare "מגיע" would false-positive on "לא מגיעים לדיון".
_TRIGGERS = re.compile("|".join((
    r"מגיע(?:ה|ות)?\s+ל(?:י|נו|ו|ה)\b",             # entitlement claims
    r"זכא|זכות|זכויות",
    r"איך\s+(?:מבקש|מגיש|פונ|מערער)",                # how do I file/appeal
    r"להגיש|לבקש|לערער|בקשת|טופס",
    r"מסרב|סירב|לא\s+(?:נותנ|מאשר|משלמ|מקבל)|שלל|נשלל|שולל|מונע|מעכב|עיכב|ביטלו?\s+את|דחו\s+את",
    r"(?:מפקד|מ״פ|מ\"פ|קצין)[^.?!]{0,12}(?:יכול|רשאי|מוסמכ)",  # authority challenge
    r"מותר\s+ל(?:מפקד|ו|הם)",
    r"עונש|נענש|ריתוק|מחבוש|קנס|ערר|דין\s+משמעתי|זימון|שפט",
    r"קביל|תלונ|להתלונן|נציב",
    r"מצוקה|קב\"ן|קבן",
    r"הורידו\s+לי|חייבו\s+אותי|לא\s+שילמו",
)))


def relevant_for(question: str, doc_id: str) -> bool:
    """Should the "למי פונים" strip render for this question+source?

    Deterministic like everything in this module: always-show orders pass
    unconditionally; otherwise the QUESTION must carry a pursue-signal
    (claim / request / refusal / appeal / authority). Default is hidden —
    the strip has to earn its place under an answer.
    """
    if doc_id in _ALWAYS_SHOW:
        return True
    return bool(_TRIGGERS.search(question or ""))
