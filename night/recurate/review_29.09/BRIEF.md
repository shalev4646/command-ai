# Cross-review brief — curated definitions vs. the page images

You are an independent second reader. Another session wrote these curated "definitions" (clauses that will be
written into the answering corpus of a Hebrew app that answers soldiers ONLY from IDF General Staff orders).
It checked its own work; self-review is not independent. Your job: compare every clause against the PAGE IMAGE
of the source order and report every defect. You change nothing except your own output file.

## Paths
- Scratchpad: `C:/Users/shale/AppData/Local/Temp/claude/D--app-soldier/308bfb3b-df1f-45ca-b84f-9193b544f8b5/scratchpad`
- Definitions: `<scratchpad>/defs/<file>.json` — each has `clauses`: [{number (a heading, often a soldier's question),
  src (sections + page), numbers_seen, text}], plus `omitted_on_purpose` {topic: reason}.
- Page images (130 dpi, 1-based PDF page index): `<scratchpad>/pages/<document_id>/p<N>.png` — open with the Read tool.
- Zoom for small print / digits (260 dpi; optional clip as page fractions x0 y0 x1 y1):
  `D:/app_soldier/venv/Scripts/python.exe <scratchpad>/render.py zoom <document_id> <page> [x0 y0 x1 y1]`
  (it prints the PNG path; then Read it). A page that isn't pre-rendered: the same command without the clip.
- Output: `<scratchpad>/review/<output name given in your assignment>.json`

## For every clause
1. Find the cited section(s) on the cited page(s). If a section is not there, look at the neighbouring pages and
   report the pointer as a `pointer` finding.
2. Compare claim by claim, READING THE IMAGE (the PDF text layer is not a source here):
   - **Every number** — days, hours, amounts, percentages, ranks, ages, form numbers, section/order numbers, dates.
     Hebrew pages are RTL; multi-digit numbers and dates are easy to misread — zoom whenever a digit is small or
     the number is decisive. The image decides.
   - **Every condition and scope** — who it applies to (חובה / קבע / מילואים, rank, role, gender, status),
     when, how long, who approves or decides (rank / role / body), exceptions („למעט", „אלא אם", „בכפוף ל",
     „רק", „לפחות", „עד"). A wrong or missing scope is an ERROR even when every number is right
     (e.g. "only company level and above", "approved by a lieutenant-colonel", "not an entitlement").
   - **Nothing added** — no obligation, right, deadline, authority or number that the page does not state.
   - **Omissions that change the answer** — an exception or condition in the SAME section that narrows, widens or
     reverses what a soldier would conclude from the clause.
   - `numbers_seen`: each value should be in the clause text and on the page.
   - The `number` heading: flag (minor) only if it promises something the text does not answer.
3. Do NOT flag: faithful paraphrase; dropped cross-references to other orders; formatting; topics listed in
   `omitted_on_purpose` (but DO flag an omitted_on_purpose reason that the page contradicts, e.g. "applies to
   civilians" when the page says soldiers).

## Lessons this project paid for
- "Not found by a text search" is not "invented" — decide only from the page image. Acronyms (עת"ש = ענף תנאי
  שירות) and scrambled digits hide real text from searches.
- A reviewer's "fix" can invert the meaning — when you propose a fix, quote the page wording it rests on.
- Check the SCOPE of a condition, not only its numbers.

## Independence
Do your own comparison first. Only after finishing a document may you read its `draft_note` / `review` fields;
if they explain one of your findings, keep the finding and add a note saying so.

## Output file (JSON, UTF-8, Hebrew for quotes)
{"document_id": "...", "clauses_checked": n, "clean": n,
 "findings": [{"clause": <0-based index>, "clause_title": "<number field>", "severity": "error|omission|pointer|minor",
               "def_says": "<the words in the clause>", "page_says": "<exact words from the image>",
               "page": N, "section": "<section id on the page>", "fix": "<corrected clause wording, or empty>"}],
 "notes": "<anything else: pages hard to read, zooms used, doubts>"}
One file per document (or per part, as assigned). Final message: counts, then each `error` / `omission` in one line.
Do not touch git, the repository, or any paid API.
