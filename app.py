import streamlit as st

from backend import get_ai_response, get_loaded_docs_info

st.set_page_config(page_title="CommandAI", page_icon="🛡️", layout="wide")

st.markdown("""
<style>
/* ---- Base typography: clean, bold, readable ---- */
html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
}

/* Center the main container, restrict width, give content room to breathe */
.main .block-container {
    max-width: 900px;
    padding-top: 3rem;
    padding-bottom: 4rem;
    padding-left: 1.5rem;
    padding-right: 1.5rem;
    margin: 0 auto;
}

h1 { font-size: 2.6rem !important; font-weight: 700 !important; line-height: 1.25 !important; margin-bottom: 0.4rem !important; }
h3 { font-size: 1.35rem !important; font-weight: 700 !important; }
p  { font-size: 1.1rem !important; line-height: 1.65 !important; }
small { font-size: 0.95rem !important; }

/* Breathing room between stacked elements */
[data-testid="stVerticalBlock"] > div { margin-bottom: 0.4rem; }

/* Style the search bar — large, touch-friendly */
.stTextInput > div > div > input {
    text-align: center;
    border-radius: 12px;
    border: 1.5px solid #c0c0c0;
    padding: 18px 16px;
    font-size: 1.15rem;
    min-height: 60px;
}

/* Style the 2x2 grid cards */
.question-card {
    background-color: #ffffff;
    padding: 28px;
    border-radius: 14px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    text-align: center;
    margin-bottom: 20px;
    border: 1px solid #e0e0e0;
    color: #333333;
    font-weight: 600;
    font-size: 1.1rem;
}

/* Buttons — large, bold, easy to tap with a thumb */
div[data-testid="stButton"] > button {
    width: 100%;
    border-radius: 12px;
    background-color: white;
    border: 1.5px solid #e0e0e0;
    color: #333;
    font-size: 1.15rem;
    font-weight: 600;
    padding: 22px 18px;
    min-height: 64px;
    line-height: 1.4;
    margin-bottom: 14px;
    white-space: normal;
}
div[data-testid="stButton"] > button:hover {
    border-color: #0f52ba;
    color: #0f52ba;
    box-shadow: 0 4px 12px rgba(15, 82, 186, 0.12);
}

/* Sidebar polish */
[data-testid="stSidebar"] { padding-top: 1.5rem; }
[data-testid="stSidebar"] h3 { font-size: 1.2rem !important; }

/* ---- Mobile-first responsiveness ---- */
@media (max-width: 768px) {
    .main .block-container { padding-top: 1.5rem; padding-left: 1rem; padding-right: 1rem; }
    h1 { font-size: 2rem !important; }
    h3 { font-size: 1.2rem !important; }
    p  { font-size: 1.05rem !important; }

    /* 2x2 grid collapses to a 1x4 vertical, thumb-friendly list */
    [data-testid="stHorizontalBlock"] { flex-direction: column !important; gap: 0 !important; }
    [data-testid="stHorizontalBlock"] > div { width: 100% !important; }

    div[data-testid="stButton"] > button {
        font-size: 1.05rem;
        padding: 20px 16px;
        min-height: 72px;
    }
    .stTextInput > div > div > input {
        font-size: 1.05rem;
        padding: 16px 14px;
        min-height: 56px;
    }
}
</style>
""", unsafe_allow_html=True)

if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None
if "role" not in st.session_state:
    st.session_state.role = None

# ---------------------------------------------------------------------------
# Entry / role gate — must be cleared before the dashboard is shown
# ---------------------------------------------------------------------------
if st.session_state.role is None:
    st.markdown("""
    <style>
    .entry-wrap { text-align: center; margin-top: 3rem; margin-bottom: 1rem; }
    .entry-wrap h1 { color: #0f52ba !important; margin-bottom: 0.3rem !important; }
    .entry-wrap p { color: #555555 !important; font-size: 1.2rem !important; margin-bottom: 2rem !important; }
    .st-key-role_soldier button, .st-key-role_commander button {
        height: 160px;
        font-size: 1.3rem;
        font-weight: 700;
        border-radius: 14px;
        border: 1.5px solid #e0e0e0;
        background-color: #ffffff;
        color: #333333;
    }
    .st-key-role_soldier button:hover, .st-key-role_commander button:hover {
        border-color: #0f52ba;
        color: #0f52ba;
        box-shadow: 0 4px 12px rgba(15, 82, 186, 0.15);
    }

    @media (max-width: 768px) {
        .entry-wrap { margin-top: 1.5rem; }
        .entry-wrap p { font-size: 1.05rem !important; }
        .st-key-role_soldier button, .st-key-role_commander button {
            height: 110px;
            font-size: 1.15rem;
        }
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown(
        "<div class='entry-wrap'><h1>CommandAI</h1>"
        "<p>בחר את סוג הכניסה שלך למערכת</p></div>",
        unsafe_allow_html=True,
    )

    _, mid, _ = st.columns([1, 4, 1])
    with mid:
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

SUGGESTED = [
    "כמה שעות שינה מגיעות לי?",
    "האם אפשר לקצר שינה בתרגיל?",
    "מה קורה אם הפרו את זכות השינה שלי?",
    "מי צריך לאשר חריגה משעות שינה?",
]


def queue_question(question: str):
    st.session_state.pending_question = question


def submit_search():
    query = st.session_state.get("search_box", "")
    if query:
        st.session_state.pending_question = query
        st.session_state.search_box = ""


def handle_question(question: str):
    st.session_state.messages.append({"role": "user", "content": question})
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1]
    ]
    with st.spinner("מחפש בפקודות..."):
        answer = get_ai_response(question, history)
    st.session_state.messages.append({"role": "assistant", "content": answer})


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    role_label = "חייל" if st.session_state.role == "soldier" else "מפקד"
    st.caption(f"מחובר כ-{role_label}")
    if st.button("🔄 החלף תפקיד", key="switch_role"):
        st.session_state.role = None
        st.rerun()

    st.markdown("---")
    st.markdown("### 🛡️ פקודות טעונות")
    docs = get_loaded_docs_info()
    if docs:
        for doc in docs:
            st.markdown(f"**{doc['id']}**  \n{doc['title']}")
    else:
        st.caption("אין פקודות טעונות")

    st.markdown("---")
    st.markdown("### היסטוריה")
    user_msgs = [m for m in st.session_state.messages if m["role"] == "user"]
    for msg in user_msgs[-6:]:
        st.caption(msg["content"][:35] + "..." if len(msg["content"]) > 35 else msg["content"])

    st.markdown("---")
    if st.button("🗑️ שיחה חדשה"):
        st.session_state.messages = []
        st.rerun()

# ---------------------------------------------------------------------------
# Main area — strict flow: header -> search -> spacing -> 2x2 question grid
# ---------------------------------------------------------------------------
st.markdown(
    "<div style='text-align: center; color: #0f52ba;'>"
    "<h1>CommandAI</h1>"
    "<p>מערכת חכמה לניתוח פקודות מטכ\"ל</p>"
    "</div>",
    unsafe_allow_html=True,
)

st.text_input(
    "חיפוש",
    key="search_box",
    placeholder="שאל שאלה על פקודות צבאיות...",
    label_visibility="collapsed",
    on_change=submit_search,
)

st.markdown("<br><br>", unsafe_allow_html=True)

col1, col2 = st.columns(2)
with col1:
    if st.button(SUGGESTED[0], key="sug_0", use_container_width=True):
        queue_question(SUGGESTED[0])
with col2:
    if st.button(SUGGESTED[1], key="sug_1", use_container_width=True):
        queue_question(SUGGESTED[1])

col3, col4 = st.columns(2)
with col3:
    if st.button(SUGGESTED[2], key="sug_2", use_container_width=True):
        queue_question(SUGGESTED[2])
with col4:
    if st.button(SUGGESTED[3], key="sug_3", use_container_width=True):
        queue_question(SUGGESTED[3])

if st.session_state.pending_question:
    q = st.session_state.pending_question
    st.session_state.pending_question = None
    handle_question(q)

# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------
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

if prompt := st.chat_input("שאל שאלה על פקודות צבאיות..."):
    handle_question(prompt)
    st.rerun()

doc_count = len(get_loaded_docs_info())
st.caption(f"CommandAI — {doc_count} פקודות טעונות | המערכת עונה אך ורק על בסיס המקורות | v2.0")
