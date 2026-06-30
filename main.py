import json
import os
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
client = Anthropic(api_key=api_key)

def load_documents():
    docs = []
    json_dir = Path("storage/json_store")
    for f in json_dir.glob("*.json"):
        docs.append(json.loads(f.read_text(encoding="utf-8")))
    return docs

def ask(question: str) -> str:
    docs = load_documents()
    context = json.dumps(docs, ensure_ascii=False, indent=2)
    
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=f"""אתה עוזר צבאי המסייע לחיילים להבין פקודות מטכ"ל.

חוקים מוחלטים:
1. ענה אך ורק על בסיס המסמכים שסופקו.
2. אם המידע לא קיים במסמכים — אמור בדיוק: "המידע לא קיים בפקודה שסופקה."
3. כל תשובה חייבת לכלול מספר סעיף.

מבנה תשובה:
**פסיקה:** מותר / אסור / מותר בתנאים
**מקור:** סעיף X / נספח א שורה X
**תנאים:** רשימה מפורטת
**מי מאשר:** דרגה נדרשת

המסמכים:
{context}""",
        messages=[{"role": "user", "content": question}]
    )
    return response.content[0].text

if __name__ == "__main__":
    print("מערכת שאילתות פקודות צבאיות")
    print("כתוב 'יציאה' כדי לסיים")
    print("-" * 40)
    
    while True:
        question = input("\nשאלה: ")
        if question == "יציאה":
            break
        if not question.strip():
            continue
        print("\n" + ask(question))