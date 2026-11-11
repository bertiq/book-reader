# Validation

Validated on macOS Apple Silicon with Python 3.13.14, Codex CLI 0.167.0, and the generated uv lockfile.

| Check | Result |
| --- | --- |
| `uv run --locked pytest` | 69 passed; 2 opt-in macOS tests skipped |
| `READER_TEST_MACOS_SANDBOX=1 uv run --locked pytest -q -k macos_real_sandbox` | 2 passed, including HTML/PDF rendering |
| `uv run --locked ruff check .` | Passed |
| `uv run --locked ruff format --check .` | Passed |
| `uv run --locked pyright` | 0 errors, 0 warnings |
| Synthetic public sample | HTML and PDF regenerated successfully |

Tests use generated PDFs. Coverage includes ignored private workspaces, separate book checkpoints, source exclusion, per-part assets, cumulative exports, archive metadata privacy, and the shared PDF runtime.
