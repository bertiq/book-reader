#!/usr/bin/env python3
"""One-time setup and strict Codex launcher. Reading still has only four prompts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]
BOOK_MARKER = ".reader-book"
BOOK_IGNORE_RULES = (
    "/book/\n/.reader-*\n!/.reader-book\n!/.reader-template.html\n/.reader.lock\n"
    "**/.reader-tmp-*\n__pycache__/\n"
)


def reader_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "book_reader_runtime", REPOSITORY / "runtime/reader.py"
    )
    if spec is None or spec.loader is None:
        raise ValueError(
            "The reader runtime is missing. Restore runtime/reader.py from the repository."
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pdf_runtime_path() -> Path:
    assets = REPOSITORY / "runtime/assets"
    digest = hashlib.sha256()
    for name in ("pdf.typ", "merman-0.5.0.tar.gz"):
        digest.update((assets / name).read_bytes())
    return Path(sys.prefix).resolve() / "share/book-reader/pdf" / digest.hexdigest()[:20]


def prepare_pdf_runtime() -> Path:
    target = pdf_runtime_path()
    if target.is_dir():
        return target
    assets = REPOSITORY / "runtime/assets"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".reader-pdf-", dir=target.parent) as temporary:
        staging = Path(temporary)
        shutil.copy2(assets / "pdf.typ", staging / "pdf.typ")
        with tarfile.open(assets / "merman-0.5.0.tar.gz") as archive:
            archive.extractall(staging, filter="data")
        packages = staging / "reader/merman"
        packages.mkdir(parents=True)
        (staging / "merman").rename(packages / "0.5.0")
        try:
            staging.rename(target)
        except OSError:
            if not target.is_dir():
                raise
    return target


def new_book(destination: Path, source: Path | None = None) -> Path:
    if destination.exists():
        raise ValueError("Destination already exists. Existing books are never reset by setup.")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", destination.name):
        raise ValueError("Use a lowercase book ID with letters, numbers, and hyphens.")
    if source:
        if source.is_symlink() or not source.is_file() or source.suffix.lower() != ".pdf":
            raise ValueError("Source must be a regular readable PDF file.")
        reader_module().pdf_reader(source)
    prepare_pdf_runtime()
    shutil.copytree(REPOSITORY / "templates/book", destination)
    (destination / "book").mkdir()
    (destination / "parts").mkdir()
    (destination / "parts/.gitkeep").write_text("", encoding="utf-8")
    (destination / ".reader-book").write_text("book-reader v1\n", encoding="utf-8")
    # The marker is versioned through an explicit exception in the ignore rules.
    shutil.copy2(REPOSITORY / "runtime/reader.py", destination / "reader.py")
    shutil.copy2(REPOSITORY / "runtime/reader-template.html", destination / ".reader-template.html")
    shutil.copy2(REPOSITORY / "STYLE.md", destination / "STYLE.md")
    (destination / ".gitignore").write_text(BOOK_IGNORE_RULES, encoding="utf-8")
    module = reader_module()
    (destination / "state.json").write_text(
        module.json_text(module.initial_state(destination.name)), encoding="utf-8"
    )
    (destination / "plan.md").write_text(
        "# Reading plan\n\nNot prepared yet. Say `prepare` to read the contents and introduction.\n",
        encoding="utf-8",
    )
    (destination / "answers.md").write_text(module.render_answers([]), encoding="utf-8")
    if source:
        shutil.copy2(source, destination / "book" / source.name)
    return destination


def refresh_book(destination: Path) -> None:
    """Update framework files for one selected book, without touching reading data."""
    book = reader_module().Book(destination)
    prepare_pdf_runtime()
    shutil.copytree(
        REPOSITORY / "templates/book/.agents", destination / ".agents", dirs_exist_ok=True
    )
    for source, target in (
        (REPOSITORY / "templates/book/AGENTS.md", destination / "AGENTS.md"),
        (REPOSITORY / "STYLE.md", destination / "STYLE.md"),
        (REPOSITORY / "runtime/reader.py", destination / "reader.py"),
        (REPOSITORY / "runtime/reader-template.html", destination / ".reader-template.html"),
    ):
        shutil.copy2(source, target)
    ignore = book.path(".gitignore")
    original = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    managed_rules = set(BOOK_IGNORE_RULES.splitlines()) | {
        "!/.reader-assets/",
        "!/.reader-assets/**",
        "/reader.html",
        "/reader.pdf",
        "/parts/.reader-tmp-*",
    }
    custom_rules = [line for line in original.splitlines() if line not in managed_rules]
    custom = "\n".join(custom_rules).strip()
    existing = (custom + "\n" if custom else "") + BOOK_IGNORE_RULES
    if existing != original:
        ignore.write_text(existing, encoding="utf-8")
    obsolete_assets = book.path(".reader-assets")
    if obsolete_assets.is_dir():
        shutil.rmtree(obsolete_assets)


def reader_python() -> str:
    # Codex 0.167 canonicalizes readable roots on macOS. An allowance for a uv
    # version symlink therefore does not permit traversing that symlink at exec.
    return str(Path(sys.executable).resolve(strict=True))


def pandoc_runtime() -> tuple[str, list[Path]]:
    executable = shutil.which("pandoc")
    if executable is None:
        return "", []
    binary = Path(executable).resolve(strict=True)
    libraries: set[Path] = set()
    if sys.platform == "darwin":
        pending = [binary]
        inspected: set[Path] = set()
        while pending:
            path = pending.pop()
            if path in inspected:
                continue
            inspected.add(path)
            result = subprocess.run(
                ["/usr/bin/otool", "-L", str(path)],
                capture_output=True,
                text=True,
                check=True,
            )
            for line in result.stdout.splitlines()[1:]:
                name = line.strip().split(" (", 1)[0]
                if not name.startswith("/") or name.startswith(("/usr/lib/", "/System/")):
                    continue
                library = Path(name).resolve(strict=True)
                libraries.add(library)
                pending.append(library)
    return str(binary), sorted(libraries)


def reader_environment() -> dict[str, str]:
    environment = dict(os.environ)
    # Executing the resolved binary bypasses venv discovery. Keep its installed
    # dependencies available without traversing uv aliases or running uv.
    environment["PYTHONPATH"] = os.pathsep.join(
        dict.fromkeys(sysconfig.get_path(name) for name in ("purelib", "platlib"))
    )
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PATH"] = (
        str(Path(reader_python()).parent) + os.pathsep + environment.get("PATH", "")
    )
    pandoc, libraries = pandoc_runtime()
    environment["READER_PANDOC"] = pandoc
    # Keep loader settings local to Pandoc. System launchers can strip DYLD_*
    # variables, so pass the resolved directories through a reader-specific key.
    environment["READER_PANDOC_LIBRARY_PATH"] = os.pathsep.join(
        sorted({str(path.parent) for path in libraries})
    )
    environment["READER_PDF_RUNTIME"] = str(pdf_runtime_path())
    return environment


def profile_overrides(book: Path) -> list[str]:
    # Exact paths, no broad workspace roots. This avoids exposing a parent Git root.
    filesystem = {":root": "deny", ":minimal": "read", str(book): "write"}
    # A venv executable can resolve outside both its own prefix and the base
    # prefix (for example through a uv alias or a macOS framework launcher).
    # Keep the invoked paths as well as their targets. Do not grant a containing
    # project, home directory, or uv installation collection to reach one file.
    runtime_paths = {
        Path(sys.prefix),
        Path(sys.exec_prefix),
        Path(sys.base_prefix),
        Path(sys.base_exec_prefix),
        Path(sys.executable),
        *(Path(sysconfig.get_path(name)) for name in ("purelib", "platlib")),
        pdf_runtime_path(),
    }
    base_executable = getattr(sys, "_base_executable", None)
    if base_executable:
        runtime_paths.add(Path(base_executable))
    pandoc, libraries = pandoc_runtime()
    if pandoc:
        runtime_paths.add(Path(pandoc))
        runtime_paths.update(libraries)
    broad_roots = {Path("/"), Path.home().resolve(), REPOSITORY}
    for path in sorted(runtime_paths):
        resolved = path.resolve()
        if resolved not in broad_roots:
            for alias in sorted({path.absolute(), resolved}):
                filesystem[str(alias)] = "read"
    for relative in (
        "book",
        ".agents",
        "AGENTS.md",
        "STYLE.md",
        "reader.py",
        ".reader-template.html",
        ".reader-book",
    ):
        filesystem[str(book / relative)] = "read"
    filesystem[str(book / ".git")] = "deny"
    # TOML inline table avoids quoted dotted-key parsing differences in Codex.
    table = (
        "{"
        + ", ".join(json.dumps(key) + "=" + json.dumps(value) for key, value in filesystem.items())
        + "}"
    )
    overrides = [
        "-c",
        'default_permissions="reader"',
        "-c",
        "permissions.reader.filesystem=" + table,
        "-c",
        "permissions.reader.network.enabled=false",
        "-c",
        "project_root_markers=" + json.dumps([BOOK_MARKER]),
    ]
    # Codex constructs tool environments separately; retain the same package
    # paths even when user settings filter inherited environment variables.
    environment = reader_environment()
    for key in (
        "PYTHONPATH",
        "PYTHONNOUSERSITE",
        "READER_PANDOC",
        "READER_PANDOC_LIBRARY_PATH",
        "READER_PDF_RUNTIME",
    ):
        overrides.extend(
            ["-c", f"shell_environment_policy.set.{key}=" + json.dumps(environment[key])]
        )
    return overrides


def probe_command(book: Path, outside: Path) -> list[str]:
    # No outside content is printed, even if isolation unexpectedly fails.
    code = """import os, pathlib, sys
import pypdf
root = pathlib.Path(sys.argv[1])
outside = pathlib.Path(sys.argv[2])
assert (root / '.reader-book').is_file(), 'active book is not readable'
try:
    outside.read_bytes()
except PermissionError:
    pass
else:
    raise SystemExit('Isolation failed: outside project data was readable')
test = root / '.reader-launch-probe'
test.write_text('ok')
test.unlink()
for path in (root / 'AGENTS.md', pathlib.Path(os.environ['READER_PDF_RUNTIME']) / 'pdf.typ'):
    try:
        with path.open('a'):
            pass
    except PermissionError:
        pass
    else:
        raise SystemExit('Isolation failed: operating instructions were writable')
try:
    (root / 'book' / '.reader-write-probe').write_text('blocked')
except PermissionError:
    pass
else:
    (root / 'book' / '.reader-write-probe').unlink(missing_ok=True)
    raise SystemExit('Isolation failed: original-source directory was writable')
if os.environ.get('READER_PANDOC'):
    import runpy
    runtime = runpy.run_path(str(root / 'reader.py'))
    ast = runtime['Book'](root).pandoc_ast('# Renderer check')
    assert ast['blocks'], 'Pandoc produced no document'
    pdf = runtime['Book'](root).pdf_bytes(ast, {}, 'Renderer check')
    assert pdf.startswith(b'%PDF-'), 'PDF renderer produced no document'
print('reader isolation verified')
"""
    return [reader_python(), "-c", code, str(book), str(outside)]


def verify_isolation(book: Path, executable: str = "codex") -> None:
    outside = REPOSITORY / "README.md"
    # This subcommand requires an explicit profile in current Codex. The launcher
    # first rejects conflicting config that could make interactive launch use another
    # sandbox family despite its default_permissions override.
    command = [
        executable,
        "sandbox",
        *profile_overrides(book),
        "-P",
        "reader",
        "-C",
        str(book),
        *probe_command(book, outside),
    ]
    result = subprocess.run(command, env=reader_environment(), text=True, capture_output=True)
    if result.returncode or "reader isolation verified" not in result.stdout:
        raise ValueError(
            "Strict isolation could not be verified. Codex was not started.\n"
            + (result.stderr or result.stdout).strip()[:1400]
        )


def reject_conflicting_settings(book: Path, paths: list[Path] | None = None) -> None:
    if paths is None:
        codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        paths = [codex_home / "config.toml", Path("/etc/codex/config.toml")]
        if os.environ.get("PROGRAMDATA"):
            paths.append(Path(os.environ["PROGRAMDATA"]) / "OpenAI/Codex/config.toml")
        paths.extend(parent / ".codex/config.toml" for parent in (book, *book.parents))

    def contains_conflicting_settings(value: Any) -> bool:
        if isinstance(value, dict):
            return any(
                key in {"sandbox_mode", "sandbox_workspace_write"}
                or contains_conflicting_settings(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(contains_conflicting_settings(item) for item in value)
        return False

    for path in dict.fromkeys(paths):
        if path.is_file():
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError) as error:
                raise ValueError(
                    f"Cannot verify the Codex configuration at {path}; strict launch stopped."
                ) from error
            if contains_conflicting_settings(data):
                raise ValueError(
                    f"Conflicting sandbox settings in {path} could override the reading boundary. Remove or separate those settings before strict launch. No configuration was changed and Codex was not started."
                )


def checkpoint(book: Path) -> str:
    try:
        top = subprocess.run(
            ["git", "-C", str(book), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "No Git repository found; all reading files are still saved."
    root = Path(top).resolve()
    relative = book.relative_to(root).as_posix()
    if book != root:
        ignored = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--quiet", "--", relative]
        )
        if ignored.returncode == 0:
            return "Book files are saved locally; this directory is ignored by its Git repository."
        if ignored.returncode != 1:
            raise ValueError("Cannot verify Git ignore rules; refusing to checkpoint this book.")
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--", relative + "/book"],
        capture_output=True,
        text=True,
        check=True,
    )
    if tracked.stdout.strip():
        raise ValueError(
            "An original source is already tracked in Git. Refusing to checkpoint it; remove it from the index outside the reading session."
        )
    subprocess.run(["git", "-C", str(root), "add", "--", relative], check=True)
    changed = subprocess.run(
        ["git", "-C", str(root), "diff", "--cached", "--quiet", "--", relative]
    )
    if changed.returncode == 0:
        return "No new book changes to checkpoint."
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "commit",
            "--only",
            "-m",
            f"Save reading progress for {book.name}",
            "--",
            relative,
        ],
        check=True,
    )
    return "Saved a local Git checkpoint for this book."


def launch(book: Path, executable: str = "codex", verify_only: bool = False) -> int:
    module = reader_module()
    module.Book(book)  # Ensure this is an initialized directory before probing.
    prepare_pdf_runtime()
    reject_conflicting_settings(book)
    verify_isolation(book, executable)
    if verify_only:
        return 0
    command = [
        executable,
        "--strict-config",
        *profile_overrides(book),
        "--ask-for-approval",
        "never",
        "--search",
        "--cd",
        str(book),
        "Use this directory's book-reader instructions. Restore compact saved state. Wait for prepare, next, questions, summary, or an answer. Do not read source pages yet. "
        f"Use this exact Python 3.13 interpreter for every reader helper: {reader_python()}. "
        "Dependencies are already managed by uv outside the book. Do not run uv or discover a parent project in this reading session.",
    ]
    result = subprocess.run(command, cwd=book, env=reader_environment())
    # This code is outside the reader sandbox and never runs inside its context.
    try:
        print(checkpoint(book))
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f"Files were saved, but the Git checkpoint failed: {error}", file=sys.stderr)
    return result.returncode


def main() -> int:
    if sys.version_info[:2] != (3, 13):
        print(
            "Use Python 3.13: run this command with uv run from repository root.", file=sys.stderr
        )
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("new", help="Set up one book directory; does not read its contents.")
    create.add_argument("directory", type=Path)
    create.add_argument("--source", type=Path)
    open_book = sub.add_parser(
        "open", help="Verify strict isolation, then open Codex for this book."
    )
    open_book.add_argument("directory", type=Path)
    open_book.add_argument("--verify-only", action="store_true")
    save = sub.add_parser("checkpoint", help="Save only this book's files in a local Git commit.")
    save.add_argument("directory", type=Path)
    refresh = sub.add_parser(
        "refresh", help="Update reader framework files without changing saved reading data."
    )
    refresh.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "new":
            result = new_book(
                args.directory.absolute(), args.source.absolute() if args.source else None
            )
            print(f"Created {result}.")
            if args.source:
                print(f"Copied the source PDF into {result / 'book' / args.source.name}.")
            else:
                print(f"Put the source PDF in {result / 'book'}.")
            print(
                f"Open this same book with: uv run --locked python scripts/book_reader.py open {result}"
            )
            print("Then say prepare.")
            return 0
        book = args.directory.absolute().resolve()
        if args.command == "refresh":
            refresh_book(book)
            print(
                "Reader framework updated. Source, plan, parts, answers, summary, and progress were preserved."
            )
            return 0
        if args.command == "checkpoint":
            reader_module().Book(book)
            print(checkpoint(book))
            return 0
        return launch(book, verify_only=args.verify_only)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
