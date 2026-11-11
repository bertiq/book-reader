# book-reader

A PDF reading companion for Codex. Read one useful part at a time, get clear explanations, answer questions, and continue from saved progress across sessions.

Lessons are saved as Markdown and collected in one HTML reading view and one PDF with linked contents. Each part keeps its images beside its lesson. The reader uses simple English and includes useful source figures, code, equations, and diagrams.

See the [synthetic reading example](examples/reader.html) and its [Markdown lesson](examples/sample-part.md). Download or open the HTML file locally to view it in a browser.

**Platform support:** tested on macOS Apple Silicon. Linux and Windows are unverified. See [VALIDATION.md](VALIDATION.md) for the tested tool versions and checks.

## Quickstart

Install these tools first:

- [Codex CLI](https://developers.openai.com/codex/quickstart/), authenticated with your account or configured provider.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) to manage Python 3.13 and the locked dependencies.
- [Pandoc](https://pandoc.org/installing.html) for HTML and PDF export.
- Git for obtaining the repository and optional reading checkpoints.

From a checkout of this repository, run:

```bash
uv sync --locked
```

uv finds or installs Python 3.13 and installs the locked runtime and development dependencies into `.venv/`. PDF support is included; no browser or TeX installation is needed for PDF export. Saved Markdown remains usable without Pandoc. A PDF page renderer such as Poppler is useful for inspecting source figures and equations.

Create a workspace for your PDF and open the reader:

```bash
uv run --locked python scripts/book_reader.py new books/my-book --source "/path/to/my-book.pdf"
uv run --locked python scripts/book_reader.py open books/my-book
```

The first command initializes blank progress and copies the PDF into `books/my-book/book/`. The second checks isolation and opens Codex inside that book. Say `prepare` to create a reading plan, then `next` for the first lesson. Use lowercase letters, numbers, and hyphens in book IDs, such as `my-book`.

You can omit `--source` and later place a PDF inside the workspace's `book/` directory. Keep one source PDF there unless you want to choose a filename during preparation.

The entire `/books/` directory is local and ignored by this repository: sources, lessons, images, progress, and generated views stay private. The public repository contains the framework, templates, and synthetic examples. Do not force-add reading workspaces.

The launcher checks that outside project files cannot be read, original sources cannot be changed, and instructions are read-only. It then starts Codex inside that book. When the session closes, it saves a local Git checkpoint only if the book has a separate Git repository or belongs to a repository that does not ignore it. It never pushes.

**If the isolation check fails, the launcher stops.** It does not silently fall back to broad filesystem access. Codex permission profiles are version- and platform-dependent; older clients, managed settings, or missing sandbox tools can prevent launch. See [Isolation](#isolation) below.

## Four prompts

| Say | What happens |
| --- | --- |
| `prepare` | Read only the contents and introduction, then save a logical reading plan. |
| `next` | Restore progress and teach the next part. Continue across chapters automatically. |
| `questions` | Show unanswered questions with their IDs. |
| `summary` | Read all saved parts and update the same overall summary. |

After a lesson, simply answer its questions. The agent tells you what is correct or needs correction, then gives the correct answer. Only the correct answer is saved. Your submitted answer and grading comments are not written into this repository.

You can answer an older question like this:

```text
answer C03-P02-Q01 Your answer here.
```

Say `next` whenever you want to continue. Unanswered questions remain available. In a fresh session, the same prompts restore saved progress automatically. There is no `resume` command to remember.

These are ordinary prompts interpreted by the included instructions and skills. They are not new built-in Codex commands. Setup/helper arguments are not part of the reading conversation.

## Your book files

| Path inside a book directory | Purpose |
| --- | --- |
| `book/` | Original source PDF. Always ignored by Git and excluded from releases. |
| `plan.md` | Logical parts and their PDF/printed page ranges. |
| `state.json` | Reading position and a short continuation note for the agent. |
| `parts/C01-P01/part.md` | Saved lesson, including two questions and their IDs. |
| `parts/C01-P01/*.png` | Images and other supporting assets for that part. |
| `answers.md` | All questions, their status, and correct answers once assessed. |
| `summary.md` | Cumulative summary, rebuilt from every saved part on request. |
| `reader.html` | Generated reading view. Open it in a modern browser. |
| `reader.pdf` | Cumulative reading notes with linked contents, page numbers, and bookmarks. |

Each book keeps its own `AGENTS.md`, `STYLE.md`, four skills, helpers, and reading data. The PDF engine and its supporting files are prepared once under the ignored `.venv/share/book-reader/` directory and shared read-only by all books. No renderer packages or license directories are copied into book folders. Temporary files handle interrupted saves and summary batching and are ignored.

Code is colored in the HTML view; equations use native MathML. Both work offline. Local figures also work offline. Mermaid diagrams load a pinned online library; when unavailable, their source remains readable. Correct answers are collapsed so they are not revealed while reading a lesson. Markdown is always the source of truth.

Every render updates the same `reader.html` and `reader.pdf` from all saved lessons, the current summary, and assessed correct answers. The PDF has a title page, a linked table of contents with page numbers, PDF bookmarks, typeset equations, local images, and offline diagrams. Correct answers appear in a separate section. Both generated files can be committed in a separate private book repository. If PDF export fails, the helper reports it and preserves the previous PDF.

## Source checks and moving between computers

Preparation records the source's SHA-256 checksum, page count, and filename. New source reading checks that identity. Renaming the same PDF is fine. Missing sources and changed sources produce separate messages and preserve existing progress. The same page count does not mean the same book. A changed checksum can also result from re-saving a PDF; it is never silently accepted.

To move a local book to another computer, copy its workspace privately; `/books/` does not travel with the framework repository. If you use a separate private book repository, restore the exact source PDF separately into its `book/` directory. Questions and cumulative summaries still work without it. Original PDFs are excluded from Git and packaged downloads. Do not force-add ignored sources.

## Isolation

Start the reader through `scripts/book_reader.py open`, not from the repository root. It sets the book marker as the Codex project root and supplies an exact-path permission profile. Source and instruction files are read-only; the active book's progress and parts are writable. Other project files are denied. Minimal runtime and Python files are allowed so tools can run. Built-in web search is available for targeted checks; direct shell networking is off.

The launcher uses the resolved Python 3.13 binary with this environment's installed packages. Python runtime files stay read-only, and user-site packages are disabled. When Pandoc is installed, the launcher also allows its resolved executable and required macOS libraries as read-only files. The preflight checks dependency imports, HTML conversion, native PDF compilation, access to the selected book, and protection of outside files, sources, instructions, and rendering assets.

The preflight tests actual access and refuses to start if its checks fail. It cannot prove every property of your operating system, Codex installation, or third-party connector. The agent is also instructed not to use connectors, parent directories, Git history, or other books. Dependencies are synced before launch. The reader uses the exact Python 3.13 interpreter supplied by the launcher; it never runs uv to discover a parent project inside a reading session. An already attached chat/file or global instruction is outside this project's control.

Conflicting `sandbox_mode` or `sandbox_workspace_write` settings in Codex configuration block launch because they can override the permission profile. Managed settings can also prevent launch. The launcher does not change global configuration. Opening a book directly in a Codex client does not perform this isolation check.

To check isolation without starting a reading session:

```bash
uv run --locked python scripts/book_reader.py open books/my-book --verify-only
```

## Saved summaries and context

Every `summary` rebuilds from all current part files in bounded batches, verifies ID coverage, and updates one file. The previous overall summary is not used as its only input. A changed or added part invalidates an in-progress summary pass.

The agent's separate continuation record lives in `state.json`: at most 400 words of subject context plus relevant part IDs. It is replaced after each part. Startup does not load all past parts or depend on you having requested a summary.

Files are saved immediately, before a session closes. The framework skips Git checkpoints for ignored books. For optional revision history, initialize a separate private repository with `git -C books/my-book init` outside the reading session. Original sources remain ignored; lessons, progress, and generated views can be committed there. Configure your normal Git identity outside the reader, then run:

```bash
uv run --locked python scripts/book_reader.py checkpoint books/my-book
```

Codex sends prompts and source excerpts to its configured model backend. Its account, chat-history, and data-retention settings apply. This project's rule against saving learner replies covers reading files and helper inputs; the model service and Codex session storage have their own controls.

## Refresh book instructions

Each book has its own copies of the reading instructions, four `reader-*` skills, and helpers. To copy the current framework into a book, close its reading session and run:

```bash
uv run --locked python scripts/book_reader.py refresh books/my-book
```

This refreshes instructions, skills, and helpers. It preserves the source, reading plan, saved parts, answers, summary, progress, custom skills, and ignore rules.

## Project files and checks

- [SPEC.md](SPEC.md): behavior and persistence rules.
- [COMMANDS.md](COMMANDS.md): exact prompt behavior and edge cases.
- [STYLE.md](STYLE.md): plain-English writing and presentation.
- [AGENTS.md](AGENTS.md): instructions for maintaining this repository.
- [VALIDATION.md](VALIDATION.md): current validation results.
- [Example reading view](examples/reader.html): a small sample with code, equations, and a figure.

The Python toolchain is configured in `pyproject.toml`:

| Tool | Role |
| --- | --- |
| uv | Python version, dependencies, `.venv/`, and `uv.lock` |
| pytest | Tests using fixtures and plain assertions |
| Ruff | Formatting, import ordering, and lint checks |
| Pyright | Static type checking in standard mode for Python 3.13 |

Run these checks from repository root:

```bash
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
```

To apply formatting and safe lint fixes:

```bash
uv run --locked ruff format .
uv run --locked ruff check --fix .
```

Keep `pyproject.toml`, `.python-version`, and the generated `uv.lock` in Git. Keep `.venv/` and tool caches out of Git. The runtime and launcher are the canonical Python files; copies inside book folders are refreshed from them rather than linted separately.

Tests use tiny generated PDFs in temporary directories. They do not read your books or make model/API calls. Renderer checks require Pandoc. Platform sandbox enforcement must also be checked on the machine that runs Codex; passing storage tests alone does not prove it.

On macOS, include the real sandbox test with `READER_TEST_MACOS_SANDBOX=1 uv run --locked pytest`. Run it outside an enclosing sandbox so macOS can apply the reader's own isolation profile.

Codex writes the teaching content. The Python helpers handle source checks, file boundaries, question IDs, saves, and summary coverage. The included skills are copied into each book workspace.

## License

The project's original code and documentation are available under the [MIT License](LICENSE).

The bundled Merman renderer and its dependencies retain their own licenses and notices inside `runtime/assets/merman-0.5.0.tar.gz`.
