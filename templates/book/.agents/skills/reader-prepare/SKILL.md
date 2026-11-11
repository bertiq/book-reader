---
name: reader-prepare
description: Prepare the current book-reader book when the user says prepare. Read only contents, introductory material, and navigation; create a bounded logical reading plan and preserve existing progress. Do not teach a chapter.
---

# Prepare the current book

1. Run `python reader.py context`. If already prepared, run `python reader.py inspect` to verify identity, then report the saved position. Do not reset or rebuild the plan.
2. Run `python reader.py inspect`. This reads metadata and outline, not page text. If several PDFs exist, ask which current `book/` filename to use, then pass `--source book/FILENAME.pdf`. Never choose another directory.
3. Locate the contents and introduction from the outline, then read small page ranges with `python reader.py extract --purpose front --first N --last M`. Maximum eight pages and 24,000 characters per call. Follow `next_cursor` until the selected pages are complete. Do not extract the entire PDF or chapter bodies. If navigation is missing, inspect a few early pages at a time. For scanned or broken text, render only the needed pages using a local PDF tool and inspect them; do not guess or OCR the whole book.
4. Create logical parts using chapter/subchapter boundaries. Keep complete explanations and worked examples together. Use 1-based PDF pages and separate printed labels. At shared page boundaries, set exact `start_at` and `stop_before` text markers. Do not assume printed page numbers equal PDF positions. Estimate size from page spans; avoid giant parts.
5. Write `.reader-prepare.json` with this schema, using actual values:

```json
{
  "title": "Book title",
  "author": "Author",
  "source_filename": "book/original.pdf",
  "source_sha256": "HASH_FROM_INSPECT",
  "front_matter": {"contents_pages": [3, 4], "introduction_pages": [5], "notes": ""},
  "parts": [
    {"id": "C01-P01", "chapter": 1, "part": 1, "title": "First topic", "pdf_start": 6, "pdf_end": 10, "printed_pages": "1-5", "sections": ["First section"], "start_at": null, "stop_before": null}
  ],
  "context": "Brief subject setup from the introduction. No chapter has been taught."
}
```

IDs are ordered by chapter and part. Part numbers start at 1 in each chapter. A chapter without formal numbering can still have an assigned sequence number. Parts cannot overlap beyond a shared boundary page; shared pages need both markers. List an empty front-matter page array with a note if that section truly does not exist.

6. Save with `python reader.py prepare --input .reader-prepare.json --revision N`, using the loaded revision. Verify success. Run `python reader.py validate` and `python reader.py render`.
7. Present the short reading plan and say that `next` starts the first part. Do not give a lesson, a book review, or a list of the author's errors.

If a source hash differs from a pinned identity, stop. Do not silently replace it or rebaseline progress. Recommend a separate initialized book directory for a different source.
