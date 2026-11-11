---
name: reader-answer
description: Assess a learner's immediate reply to book-reader comprehension questions, or answer ID followed by a reply. Explain what is correct or needs correction in chat, then save only the complete correct answer. Do not advance reading.
---

# Assess a reply without storing it

1. Restore `python reader.py context` if needed. Resolve the question ID from an explicit `answer C03-P02-Q01 ...` reference or clear numbering in an immediate reply to the last shown part. In a fresh session, a bare reply may be ambiguous: ask for the ID rather than guessing.
2. Read only the matching question and its part. `python reader.py questions` returns pending questions; an explicitly named answered question can be found by ID in `answers.md`. Do not read the PDF or browse unrelated topics to grade a question that the lesson should already answer.
3. Compare the learner's reasoning with the taught idea. In chat, briefly state what is correct and what needs correction. Then give the complete correct answer in simple English. Do not demand the original wording or invent a numerical grade.
4. Write only the correct answer to `.reader-answer.json`:

```json
{
  "answers": [
    {"id": "C03-P02-Q01", "correct_answer": "A complete standalone correct answer, with useful equations or code if needed."}
  ]
}
```

Never put the submitted reply, comparison, grading, score, or attempt history in a file, shell argument, helper input, log, or commit message. Use one entry per clearly answered question. Leave other questions unanswered.

5. Save with `python reader.py save-answers --input .reader-answer.json --revision N`, using the current revision. The helper marks those questions answered and stores only their correct answers. An incorrect attempt still becomes answered after a correct answer is provided. This is not a mastery judgment.
6. Run `python reader.py validate` and `python reader.py render`. Deliver the assessment in chat together with the same correct answer that was saved. Wait for the learner's next request; do not advance.

If a necessary correction to the saved lesson is discovered, do not hide it by inventing a new topic or changing IDs. Explain the corrected concept directly and preserve a consistent correct answer. Request an explicit lesson repair if it would require rewriting already saved material; ordinary answer saving does not overwrite a part.
