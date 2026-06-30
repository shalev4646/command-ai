import sys
sys.path.insert(0, "D:/app_soldier")
from backend import get_ai_response

questions = [
    "כמה ימי חופשה מגיעים לי בשנה?",
    "מה קורה אם הפרו את זכות השינה שלי?",
]
for q in questions:
    print(f"Q: {q}")
    print(f"A: {get_ai_response(q)}")
    print("-" * 60)
