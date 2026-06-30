import random
import streamlit as st

from backend import get_ai_response, get_loaded_docs_info

st.set_page_config(
    page_title="CommandAI",
    page_icon="🛡️",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
/* ── Reset & base ── */
html, body, [data-testid="stAppViewContainer"] {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    background-color: #0d1117;
    color: #e6edf3;
}

/* Hide Streamlit chrome */
#MainMenu, footer, header { visibility: hidden; }
[data-testid="stToolbar"] { display: none; }
[data-testid="collapsedControl"] { display: none; }

/* ── Main container ── */
.main .block-container {
    max-width: 480px;
    padding: 1rem 1rem 5rem 1rem;
    margin: 0 auto;
}

/* ── Typography ── */
h1 {
    font-size: 1.8rem !important;
    font-weight: 800 !important;
    color: #3b82f6 !important;
    margin: 0.5rem 0 0.2rem 0 !important;
    text-align: center;
}
p { font-size: 0.95rem !important; line-height: 1.5 !important; }

/* ── Buttons — compact, tappable ── */
div[data-testid="stButton"] > button {
    width: 100%;
    border-radius: 10px;
    background-color: #161b22;
    border: 1px solid #30363d;
    color: #c9d1d9;
    font-size: 0.95rem;
    font-weight: 600;
    padding: 12px 14px;
    min-height: 48px;
    line-height: 1.3;
    margin-bottom: 8px;
    white-space: normal;
    text-align: right;
    transition: border-color 0.15s, color 0.15s;
}
div[data-testid="stButton"] > button:hover {
    border-color: #3b82f6;
    color: #3b82f6;
}

/* ── Entry screen role buttons ── */
.st-key-role_soldier button,
.st-key-role_commander button {
    min-height: 72px !important;
    font-size: 1.1rem !important;
    background-color: #161b22 !important;
}

/* ── Search / text input ── */
.stTextInput > div > div > input {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    color: #e6edf3;
    padding: 12px 14px;
    font-size: 0.95rem;
    min-height: 46px;
}
.stTextInput > div > div > input::placeholder { color: #6e7681; }

/* ── Chat input ── */
[data-testid="stChatInput"] textarea {
    background-color: #161b22 !important;
    border: 1px solid #30363d !important;
    border-radius: 10px !important;
    color: #e6edf3 !important;
    font-size: 0.95rem !important;
}

/* ── Chat messages ── */
[data-testid="stChatMessage"] {
    background-color: #161b22;
    border: 1px solid #21262d;
    border-radius: 10px;
    padding: 10px 14px;
    margin-bottom: 8px;
}

/* ── Section gaps ── */
[data-testid="stVerticalBlock"] > div { margin-bottom: 0.2rem; }
.stMarkdown { margin-bottom: 0.2rem !important; }

/* ── Sidebar (hidden by default on mobile) ── */
[data-testid="stSidebar"] {
    background-color: #0d1117;
    border-right: 1px solid #21262d;
}

/* ── Caption / small text ── */
.stCaption, small { color: #6e7681 !important; font-size: 0.8rem !important; }

/* ── Spinner ── */
.stSpinner > div { border-top-color: #3b82f6 !important; }
</style>
""", unsafe_allow_html=True)

# ── Session state ──
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None
if "role" not in st.session_state:
    st.session_state.role = None

# ── Entry / role gate ──
if st.session_state.role is None:
    st.markdown("<div style='text-align:center; padding-top:2rem;'>", unsafe_allow_html=True)
    st.markdown("# CommandAI")
    st.markdown("<p style='text-align:center; color:#6e7681;'>בחר את סוג הכניסה שלך</p>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🪖 כניסת חיילים", key="role_soldier", use_container_width=True):
            st.session_state.role = "soldier"
            st.rerun()
    with col2:
        if st.button("⭐ כניסת מפקדים", key="role_commander", use_container_width=True):
            st.session_state.role = "commander"
            st.rerun()
    st.stop()

ALL_QUESTIONS = [
    "כמה שעות שינה מגיעות לי?",
    "האם אפשר לקצר שינה בתרגיל?",
    "מה קורה אם הפרו את זכות השינה שלי?",
    "מי צריך לאשר חריגה משעות שינה?",
    "מה הם תנאי החופשה לחייל בשירות חובה?",
    "כמה ימי חופשה מגיעים לי בשנה?",
    "האם מפקד יכול לבטל חופשה?",
    "מה זכויותיי אם אני חולה בזמן חופשה?",
    "מה המינימום שינה בתרגיל מבצעי?",
    "האם מגיעה לי שינה רצופה?",
]

if "suggested" not in st.session_state:
    st.session_state.suggested = random.sample(ALL_QUESTIONS, 4)


def queue_question(q: str):
    st.session_state.pending_question = q



def handle_question(question: str):
    st.session_state.messages.append({"role": "user", "content": question})
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1]
    ]
    with st.spinner("מחפש בפקודות..."):
        answer = get_ai_response(question, history)
    st.session_state.messages.append({"role": "assistant", "content": answer})


# ── Sidebar ──
with st.sidebar:
    role_label = "חייל" if st.session_state.role == "soldier" else "מפקד"
    st.caption(f"מחובר כ-{role_label}")
    if st.button("🔄 החלף תפקיד", key="switch_role"):
        st.session_state.role = None
        st.rerun()
    st.markdown("---")
    st.markdown("### פקודות טעונות")
    docs = get_loaded_docs_info()
    if docs:
        for doc in docs:
            st.markdown(f"**{doc['id']}**  \n{doc['title']}")
    else:
        st.caption("אין פקודות טעונות")
    st.markdown("---")
    if st.button("🗑️ שיחה חדשה"):
        st.session_state.messages = []
        st.rerun()

# ── Header ──
role_label = "חייל" if st.session_state.role == "soldier" else "מפקד"
st.markdown(
    f"<div style='text-align:center; padding: 0.5rem 0 0.8rem 0;'>"
    f"<h1>CommandAI</h1>"
    f"<p style='color:#6e7681; margin:0; font-size:0.85rem;'>מערכת חכמה לניתוח פקודות מטכ\"ל · {role_label}</p>"
    f"</div>",
    unsafe_allow_html=True,
)

# ── Conversation ──
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        content = msg["content"]
        if msg["role"] == "assistant":
            if "מותר בתנאים" in content:
                st.markdown("⚠ **מותר בתנאים**")
            elif "אסור" in content:
                st.markdown("✗ **אסור**")
            elif "מותר" in content:
                st.markdown("✓ **מותר**")
        st.markdown(content)

# ── Suggested questions (only when no conversation yet) ──
if not st.session_state.messages:
    st.markdown("<p style='color:#6e7681; font-size:0.85rem; text-align:center; margin-bottom:6px;'>שאלות נפוצות</p>", unsafe_allow_html=True)
    for i, q in enumerate(st.session_state.suggested):
        if st.button(q, key=f"sug_{i}", use_container_width=True):
            queue_question(q)

# ── Process pending question ──
if st.session_state.pending_question:
    q = st.session_state.pending_question
    st.session_state.pending_question = None
    handle_question(q)
    st.rerun()

# ── Chat input (always visible) ──
if prompt := st.chat_input("שאל שאלה על פקודות צבאיות..."):
    handle_question(prompt)
    st.rerun()

doc_count = len(get_loaded_docs_info())
st.caption(f"CommandAI · {doc_count} פקודות · v2.1")
