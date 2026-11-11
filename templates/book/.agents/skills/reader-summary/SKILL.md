---
name: reader-summary
description: Rebuild the current book-reader cumulative summary when the user says summary. Process every saved part in fresh bounded batches, verify complete current coverage, and update the same summary.md. Do not read the PDF or advance progress.
---

# Rebuild the cumulative summary

1. Restore `python reader.py context`. Run `python reader.py summary-inputs`. If no parts exist, explain that nothing has been saved yet and stop without creating a summary.
2. Start a fresh pass every time. Keep its returned `session_id`. Read each batch with `python reader.py summary-batch --index N --session SESSION_ID`, from 0 through the returned batch count minus 1. Each batch is bounded at 24,000 characters. A long part can span batches; keep its fragments together in your notes. Do not substitute the old `summary.md`, question register, PDF, or chat memory for these saved parts. A newer pass invalidates the older session ID.
3. Keep short working notes in one ignored `.reader-summary-work.md` if needed to control context. Track the supplied part IDs. Combine related ideas, retain important conditions and formulas, and remove repetition. Do not introduce unread material, new examples, learner grades, or question lists. Do not claim complete coverage of the book unless every planned part is saved.
4. Write `.reader-summary-result.json`:

```json
{
  "session_id": "SESSION_ID_FROM_SUMMARY_INPUTS",
  "body": "The cumulative summary in simple English and Markdown.",
  "covered_part_ids": ["C01-P01", "C01-P02"]
}
```

Include every ID from the fresh manifest exactly once. Check main-idea coverage as well as IDs; a served batch alone does not prove the synthesis retained its important ideas.

5. Refresh context for the latest revision, then save with `python reader.py save-summary --input .reader-summary-result.json --revision N`. The helper rejects incomplete batches, missing/duplicate IDs, or files changed since the manifest was created. On a changed manifest, restart from all current parts.
6. Remove `.reader-summary-work.md` after success. Run `python reader.py validate` and `python reader.py render`. Present the saved `summary.md`, without its machine metadata comment. No reading cursor or question status changes.
