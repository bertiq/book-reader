# book-reader specification

## Purpose

Configure Codex as a book-reading agent. Use four prompts, simple English, direct teaching, silent corrections, bounded source reading, and persistent files. The system is a repository and workflow, not a new model or an API service.

## Python toolchain

Target Python 3.13, pinned by `.python-version` and `requires-python` in `pyproject.toml`. Use uv for dependency resolution, environment creation, and the real generated `uv.lock`; pytest for tests; Ruff for formatting, import ordering, and linting; and Pyright in standard mode for static typing. Keep tool settings in `pyproject.toml`.

Run dependency setup and quality checks at repository root, outside a reading session. Start Codex with that environment's exact Python interpreter in its launch instructions and PATH. Never run uv's parent-project discovery from inside an isolated book directory. Refreshing framework files must preserve saved state and parts.

## One book per reading session

The active book is the initialized current directory. It contains a `.reader-book` marker, `AGENTS.md`, four local skills, `STYLE.md`, `reader.py`, `.reader-template.html`, `state.json`, `plan.md`, `answers.md`, `parts/`, and `book/`. It may also contain `summary.md` and generated `reader.html` and `reader.pdf` files.

Use `scripts/book_reader.py` for setup and launch, `reader-*` for the four local skills, and `.reader-*` for working files. Markdown metadata uses `reader-` labels. Keep private working files ignored by Git, with explicit exceptions for the versioned `.reader-book` marker and `.reader-template.html`. Prepare PDF support files once in a content-addressed cache under `.venv/share/book-reader/`, outside book directories and Git. The launcher pins that shared runtime in its environment and grants read-only access. Do not copy third-party renderer packages or notices into each book.

Each part has one directory under `parts/`, such as `parts/C01-P01/`, containing `part.md` and that part's images or other assets. Asset links in the lesson are relative to its own directory. All questions and correct answers live in one root `answers.md`. There are no practice, recap, verification, or memory folders. Small hidden temporary files support transactions and summary batching; they are ignored and contain no learner replies.

The launcher uses a selected book as project root. It forbids outside project-data reads and writes, source-book writes, and editing of reader instructions. It allows minimal runtime files and any needed Python installation. Built-in web search is enabled for targeted checking; direct command networking is off. A preflight must fail closed if the runtime cannot enforce the selected profile. Plain `codex` inside a directory only supplies instruction-level isolation, not a proven filesystem boundary.

Ignore the entire root `/books/` directory in the framework repository, including sources, lessons, figures, progress, and generated views. Public examples must be synthetic. The setup helper must skip checkpoints for ignored books without force-adding them. A book may have its own separate private Git repository for checkpoints; original source PDFs must remain ignored there too.

No reader session uses Git or scans siblings. Repository-level Git work is done by the human or setup helper outside the reader session. Do not automatically push or publish anything.

## Source identity

Keep source PDFs under `book/`. Ignore that entire directory in Git, archives, and releases. Pin filename, SHA-256, and page count during preparation. Check identity again before extracting new source text or saving a new part. SHA-256, not page count, establishes exact file identity. A changed hash does not prove the content is a different title; metadata changes can also change it.

If the pinned filename moved, accept exactly one PDF with the same hash inside `book/`. Reject source symlinks. If no file matches, distinguish missing source from changed source and report it without changing state. If several unprepared PDFs are present, ask the user to choose one in the current book directory. Never inspect other books for a replacement.

Existing saved material remains usable without the PDF. Do not silently rebaseline a changed source. Explicit replacement handling is a separate user decision, not a fifth routine command. Initialize a separate book directory by default.

## Preparation and boundaries

Inspect metadata and outline first. Locate the contents and introduction with small, page-limited reads. Read only those introductory/navigation sections. Do not bulk-extract the whole book, read chapter bodies to make the plan, or run a full-book embedding/indexing task.

Create logical parts from chapter and subsection headings, complete arguments, and worked examples. Keep derivations and their explanations together. Estimate size from contents page spans; refine an unread part only if its actual density requires it. A refinement must be explicit, preserve saved IDs and question IDs, and not overwrite existing parts. Typical parts should fit roughly 2,000-6,000 source words; use semantic boundaries rather than arbitrary page quotas.

Record 1-based PDF page numbers separately from printed page labels. Record `start_at` and/or `stop_before` when a boundary shares a page. These delimit extracted text, not chapter boilerplate. Repeated headings can require a more specific exact marker. Keep content after `stop_before` out of the lesson.

`plan.md` is the authoritative ordered plan. Its embedded machine-readable block drives the helper. The visible table is for people. `state.json` holds no duplicate full plan.

## Progress and state

State has a version, book ID, revision counter, phase, pinned source, `next_index`, `last_part`, and a continuation record. The continuation record contains a short subject-context note (at most 400 words) and references to saved parts. Aim for 200-400 words once enough material exists; shorter is better for a short book. Replace it after each saved part. Do not store learner grades, replies, inferred personal traits, or attempt histories.

On a fresh session, run the context helper. It returns only compact state and the next planned part. Load earlier parts only when relevant. Do not reload every saved part or use the growing cumulative summary as mandatory startup context.

`next` creates exactly one planned part. `parts/C01-P01/part.md` is authoritative for the lesson shown in chat. Its metadata identifies its plan item and source. Exactly two questions are registered in the same save operation. IDs follow `<part-id>-Q01` and `-Q02` and never change after saving.

Use a revision check, kernel advisory lock, validated staged content, and a recovery journal for writes across files. Keep the lock inode in place; the operating system releases its lock when a process dies. Write state last. If a process stops after a journal is written, the next helper call completes that operation before reading state. Never advance a cursor for a failed validation. Reject conflicting concurrent work. An identical retried part save is harmless; a conflicting rewrite is rejected.

`answered` only means a reply was assessed and a correct answer was supplied. It is not a mastery score. Unanswered questions never block reading.

## Questions

`answers.md` is the authoritative register. Each entry has a stable ID, the exact question text, a part link, status, and a correct answer after assessment. Do not persist the user's submission, comparison with it, score, or further attempts. Questions are intentionally repeated in parts and the register. Pending entries have no correct answer.

Show comparison and the full correct answer in chat, save the same correct answer, and mark only clearly addressed questions answered. `questions` outputs pending questions without reading the PDF or revealing stored correct answers. A fresh session with a bare answer must ask for an ID when the target is unclear.

## Cumulative summary

Every `summary` starts a new manifest of all saved `parts/*/part.md` lesson files and their hashes. Include every valid part, including valid files recovered from a previous session. Reject unplanned or malformed lesson files rather than silently omitting them.

Serve all part text in bounded batches. Large individual parts can span multiple internal batches. Give each pass a unique session token, require it for batch retrieval and saving, and mark which batches were returned. A new pass invalidates the prior token. The agent keeps brief working notes in a single ignored temporary file if needed, combines them, and verifies coverage by part ID. Do not substitute the previous summary for the underlying parts. Batch receipts prove retrieval, not semantic completeness; the agent must retain the main ideas and important conditions.

Before saving, require every batch to have been served and every manifest ID to be listed exactly once as covered. Reject a stale manifest if any part was added or edited meanwhile. Update the same `summary.md`; do not create a growing sequence of recap files. Save the coverage manifest in its metadata so staleness can be detected later. The result covers saved parts, not unread material or the whole source by assumption.

## Rendering

Markdown is the source of truth. Generate `reader.html` with Pandoc, code highlighting, native MathML, tables, anchors, light/dark styling, a contents sidebar, and collapsed correct-answer entries. Do not maintain a second manually written lesson version. Pandoc and a modern browser are required for the styled view. Markdown remains usable if rendering is unavailable.

The same `render` operation must also rebuild one cumulative `reader.pdf` from the same saved material. Include all saved parts, the current saved summary when present, and assessed correct answers in a separate section. Use a print layout with a title page, linked contents with page numbers, page numbering, and PDF bookmarks. Preserve code, mathematical notation, tables, local images, and diagrams. Use the native Typst engine and vendored Merman renderer offline within the same sandbox; do not require a browser, remote rendering service, or broader filesystem access. Replace `reader.pdf` atomically only after successful compilation. If PDF export fails, keep the previous PDF intact and clearly report which outputs refreshed. Allow both generated views to be committed in a separate private book repository; the framework's `/books/` directory and original sources remain ignored.

Lessons must include original figures from the current book part when they are necessary for understanding or convey important visual information that prose alone would lose. Extract or crop only the relevant image or page region, preserve the details needed to interpret it, and save it beside `part.md` in that part's `parts/PART-ID/` directory. Link it beside the explanation with meaningful alt text and a source caption. Omit decorative or redundant figures. Generated diagrams must not be presented as original book images. If available tools cannot extract a necessary figure, explain the limitation and describe its important content in text. Figure inspection and extraction must respect the current part's source boundaries.

Resolve the installed Pandoc executable and its required macOS libraries before isolation. Allow those exact runtime files read-only, and supply resolved library directories only to Pandoc's process. Verify Pandoc execution during launch preflight when it is installed. Do not grant access to an entire package-manager installation to enable rendering.

Code and MathML work offline. Local SVG/PNG figures work offline. Mermaid uses a pinned browser library when online; emit its source as escaped, unhighlighted text and keep it readable as an offline fallback. Do not fetch arbitrary remote images during rendering or allow raw lesson HTML to run. Render source links as links, not automatic downloads. Read only active-book assets.

## Acceptance checks

- Preparation does not consume chapter bodies or overwrite progress.
- A fresh `next` returns the next unsaved planned part and preserves unanswered questions.
- IDs are stable, and answers save no learner input or grading.
- Missing, changed, renamed, and ambiguous source cases are distinct.
- Boundary markers trim shared pages correctly; PDF/printed page numbers remain distinct.
- Concurrent/stale saves fail without moving progress; interrupted saves recover.
- Every summary re-reads all parts and refuses incomplete or stale coverage.
- Questions and summaries work without the original PDF.
- Source PDFs are ignored and excluded from packaged deliverables.
- The strict launcher refuses to run if isolation cannot be verified.
- The rendered view shows colored code, readable equations, diagrams, and mobile layouts.
- Each render replaces the same cumulative PDF with valid contents links, page numbers, bookmarks, and all saved material; failed export preserves the previous PDF.
