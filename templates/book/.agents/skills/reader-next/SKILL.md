---
name: reader-next
description: Teach and save exactly the next planned part in the current book-reader book when the user says next. Restore saved progress automatically, read only that part, and leave older unanswered questions pending.
---

# Teach the next part

1. Run `python reader.py context`. If unprepared, request `prepare` and stop. If `next_part` is null, say all planned parts have been saved and stop. Do not create a recap without a request.
2. Read `STYLE.md`. Load only the relevant earlier part files listed by continuation references when needed. Do not read the growing `summary.md` or all earlier parts by default.
3. Run `python reader.py inspect` to check source identity. Read only the next part with `python reader.py extract --purpose part --part C01-P01 --first N --last M`. Stay within its recorded pages, at most eight pages per call. Honor `next_cursor` with `--first PAGE --offset OFFSET`; never treat a truncated extraction as complete. The helper trims recorded shared-page markers. Check boundary figures, tables, equations, or broken text by rendering only the needed PDF pages. Do not read ahead to explain the next topic.
4. Teach the useful ideas directly in simple English. Follow the part's scope and preserve important reasoning, assumptions, formulas, and code. Include original book figures needed for understanding, following `STYLE.md`; extract or crop them from the current part, save them beside `part.md` in `parts/PART-ID/`, and link them beside the explanation with source captions. Check doubtful points against targeted primary sources or small safe calculations if necessary. Give the correct explanation without a correction report or extra topics. State real uncertainty plainly.
5. Write `.reader-part.json` containing only generated content:

```json
{
  "part_id": "C01-P01",
  "body": "Lesson Markdown, without a title, source footer, or question section.",
  "questions": ["First comprehension question?", "Second comprehension question?"],
  "context": "Updated short subject context for continuing the book.",
  "references": ["C01-P01"]
}
```

Use exactly two open-ended questions answerable from this lesson. Do not write their answers in the part. Keep the continuation note at most 400 words and references to saved parts only. Replace the prior note; do not append a growing history. Do not record learner judgments.

6. Save with `python reader.py save-part --input .reader-part.json --revision N`. The helper adds stable question IDs and saves the lesson, question register, and progress together. Stop on a failed save. Do not manually move the cursor.
7. Run `python reader.py validate` and `python reader.py render` to update the cumulative `reader.html` and `reader.pdf`. Show the saved `parts/PART-ID/part.md` in chat, omitting its machine metadata comment. Keep its text and question IDs the same. Link to both reading views when the interface supports it; report an export failure without undoing the lesson save.
8. Wait. Do not teach another part automatically or require an answer before a later `next`.

If a planned part proves too large, propose a split at existing subsection boundaries before saving. Do not silently renumber saved parts. If the user wants that refinement, write `.reader-refine.json` with a `parts` list containing the full revised plan. Keep the saved prefix identical and edit only the unread tail. Run the internal helper `python reader.py refine-plan --input .reader-refine.json --revision N`, then refresh context and resume. This helper is not a fifth reading command.
