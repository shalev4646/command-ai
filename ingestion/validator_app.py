import streamlit as st
import json
from pathlib import Path

st.set_page_config(layout="wide", page_title="Military Doc Validator")

st.title("בדיקת פקודות צבאיות")
st.caption("כלי לוידוא שהמסמכים עברו עיבוד נכון לפני שנכנסים למערכת")

uploaded = st.file_uploader("העלה קובץ PDF של פקודה", type="pdf")

if uploaded:
    import fitz
    
    pdf_bytes = uploaded.read()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    pages = {}
    for i, page in enumerate(doc):
        pages[i+1] = page.get_text()
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("טקסט גולמי מה-PDF")
        page_num = st.selectbox("בחר עמוד", list(pages.keys()))
        st.text_area("תוכן העמוד", pages[page_num], height=600)
    
    with col2:
        st.subheader("JSON מובנה")
        
        template = {
            "document_id": uploaded.name.replace(".pdf", ""),
            "title": "",
            "published": "",
            "sections": [],
            "annex_exceptions": []
        }
        
        edited = st.text_area(
            "ערוך את ה-JSON כאן",
            value=json.dumps(template, ensure_ascii=False, indent=2),
            height=600
        )
        
        if st.button("שמור JSON"):
            try:
                parsed = json.loads(edited)
                save_path = Path("storage/json_store") / f"{template['document_id']}.json"
                save_path.write_text(
                    json.dumps(parsed, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                st.success(f"נשמר בהצלחה!")
            except json.JSONDecodeError as e:
                st.error(f"שגיאה ב-JSON: {e}")