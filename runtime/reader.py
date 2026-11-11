#!/usr/bin/env python3
"""Book-local deterministic storage helpers. No model calls or learner replies."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict
from urllib.parse import unquote, urlsplit

if TYPE_CHECKING:
    from pypdf import PdfReader

type Record = dict[str, Any]


class SourceIdentity(TypedDict):
    filename: str
    sha256: str
    page_count: int


VERSION = 1
PART_ID = re.compile(r"C\d{2,}-P\d{2,}\Z")
QUESTION_ID = re.compile(r"C\d{2,}-P\d{2,}-Q0[12]\Z")
MAX_CONTEXT_WORDS = 400
MAX_BATCH_CHARS = 24000
JOURNAL = ".reader-transaction.json"
SUMMARY_SESSION = ".reader-summary.json"
BOOK_MARKER = ".reader-book"


class ReaderError(ValueError):
    pass


def part_filename(part_id: str) -> str:
    if not PART_ID.fullmatch(part_id):
        raise ReaderError("Invalid part ID.")
    return f"parts/{part_id}/part.md"


def pandoc_command() -> str:
    return os.environ.get("READER_PANDOC") or "pandoc"


def pandoc_environment() -> dict[str, str]:
    environment = dict(os.environ)
    libraries = environment.get("READER_PANDOC_LIBRARY_PATH")
    if libraries:
        environment["DYLD_LIBRARY_PATH"] = libraries
    return environment


def mermaid_blocks(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("t") == "CodeBlock" and "mermaid" in node["c"][0][1]:
            # Syntax-highlighting spans are HTML, not Mermaid source. Emit only
            # escaped diagram text so both Mermaid and the offline view see it.
            source = html.escape(node["c"][1])
            node.update(t="RawBlock", c=["html", f'<pre class="mermaid">{source}</pre>'])
            return
        for value in node.values():
            mermaid_blocks(value)
    elif isinstance(node, list):
        for item in node:
            mermaid_blocks(item)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def pdf_digest(reader: PdfReader) -> str:
    """Hash the stable stream actually parsed by pypdf, preserving its position."""
    stream = reader.stream
    position = stream.tell()
    stream.seek(0)
    h = hashlib.sha256()
    try:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    finally:
        stream.seek(position)
    return h.hexdigest()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def machine_block(kind: str, data: Any) -> str:
    # Avoid an unexpected HTML-comment terminator in ordinary book text.
    encoded = json_text(data).replace("-->", "--\\u003e")
    return f"<!-- reader-{kind}\n{encoded}-->\n"


def machine_read(text: str, kind: str) -> Any:
    found = re.search(r"<!-- reader-" + re.escape(kind) + r"\n(.*?)-->", text, re.S)
    if not found:
        raise ReaderError(f"Missing reader-{kind} metadata. Restore the saved file from a backup.")
    try:
        return json.loads(found.group(1))
    except ValueError as error:
        raise ReaderError(f"Invalid {kind} metadata: {error}") from error


def pdf_reader(path: Path) -> PdfReader:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise ReaderError(
            "Install dependencies before starting the reader: run uv sync at repository root."
        ) from error
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ReaderError("This PDF needs a password. Supply a readable copy in book/.")
        return reader
    except ReaderError:
        raise
    except Exception as error:
        raise ReaderError(f"Cannot open the PDF: {error}") from error


def atomic_write(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".reader-tmp-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Book:
    def __init__(self, root: Path) -> None:
        self.root = root.absolute()
        if self.root.is_symlink() or not (self.root / BOOK_MARKER).is_file():
            raise ReaderError(
                "Open an initialized book directory. Do not run the reader at repository root."
            )
        self.root = self.root.resolve()

    def path(self, relative: str, *, must_exist: bool = False) -> Path:
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ReaderError("Use a relative path inside the current book directory.")
        pieces = Path(relative).parts
        if ".." in pieces:
            raise ReaderError("Parent traversal outside the current book is forbidden.")
        cursor = self.root
        for piece in pieces:
            cursor = cursor / piece
            if cursor.is_symlink():
                raise ReaderError(f"Symlink paths are forbidden: {relative}")
        if not cursor.resolve().is_relative_to(self.root):
            raise ReaderError("Path escaped the current book directory.")
        if must_exist and not cursor.is_file():
            raise ReaderError(f"Missing file: {relative}")
        return cursor

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        path = self.path(".reader.lock")
        # Keep this inode in place. Deleting a lock file can let two processes
        # acquire different inodes at the same pathname after a stale-PID race.
        with path.open("a+b") as stream:
            try:
                if os.name == "nt":
                    import msvcrt

                    if stream.seek(0, os.SEEK_END) == 0:
                        stream.write(b"0")
                        stream.flush()
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                raise ReaderError("Another reader operation is running. Retry when it finishes.")
            try:
                self.recover()
                yield
            finally:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def recover(self) -> None:
        path = self.path(JOURNAL)
        if not path.exists():
            return
        try:
            journal = json.loads(path.read_text(encoding="utf-8"))
            files = journal["files"]
            if not isinstance(files, dict):
                raise ValueError("files must be a mapping")
            for relative, content in files.items():
                self._writable_output(relative)
                if not isinstance(content, str):
                    raise ValueError("content must be text")
            # The state pointer is always installed last.
            for relative in sorted(files, key=lambda name: name == "state.json"):
                atomic_write(self.path(relative), files[relative])
            path.unlink()
        except (KeyError, ValueError) as error:
            raise ReaderError(
                f"Invalid recovery journal; preserve it and repair before continuing: {error}"
            ) from error

    def _writable_output(self, relative: str) -> None:
        self.path(relative)
        if relative in {"state.json", "plan.md", "answers.md", "summary.md"}:
            return
        if re.fullmatch(r"parts/C\d{2,}-P\d{2,}/part\.md", relative):
            return
        raise ReaderError(f"Not a reader output path: {relative}")

    def commit(self, files: dict[str, str]) -> None:
        for relative in files:
            self._writable_output(relative)
        atomic_write(self.path(JOURNAL), json_text({"version": VERSION, "files": files}))
        self.recover()

    def state(self) -> Record:
        try:
            data = json.loads(self.path("state.json", must_exist=True).read_text(encoding="utf-8"))
        except ValueError as error:
            raise ReaderError(f"Invalid state.json: {error}") from error
        if data.get("version") != VERSION or data.get("phase") not in {
            "unprepared",
            "prepared",
            "finished",
        }:
            raise ReaderError("Unsupported or invalid reader state. Do not guess progress.")
        if not isinstance(data.get("revision"), int) or not isinstance(data.get("next_index"), int):
            raise ReaderError("Invalid state revision or cursor.")
        return data

    def plan(self) -> Record:
        return machine_read(
            self.path("plan.md", must_exist=True).read_text(encoding="utf-8"), "plan"
        )

    def answers(self) -> list[Record]:
        return machine_read(
            self.path("answers.md", must_exist=True).read_text(encoding="utf-8"), "answers"
        )

    def expect_revision(self, state: Record, expected: int) -> None:
        if expected != state["revision"]:
            raise ReaderError(
                "Saved progress changed. Reload context before saving; no files were changed."
            )

    def source(
        self, filename: str | None = None, *, check: bool = True
    ) -> tuple[Path, PdfReader, SourceIdentity]:
        state = self.state()
        pinned = state.get("source")
        directory = self.path("book")
        if not directory.is_dir():
            raise ReaderError(
                "Source missing. Restore the PDF inside book/; saved work is unchanged."
            )
        paths = sorted(p for p in directory.iterdir() if p.suffix.lower() == ".pdf")
        for path in paths:
            self.path(path.relative_to(self.root).as_posix(), must_exist=True)
        if filename:
            requested = self.path(filename, must_exist=True)
            if requested.parent != directory or requested.suffix.lower() != ".pdf":
                raise ReaderError("Select a PDF directly inside book/.")
            paths = [requested]
        if not paths:
            name = pinned["filename"] if pinned else "a source PDF in book/"
            raise ReaderError(f"Source missing. Restore {name}; saved work is unchanged.")
        if pinned and check:
            exact = [p for p in paths if p.relative_to(self.root).as_posix() == pinned["filename"]]
            if exact and file_digest(exact[0]) == pinned["sha256"]:
                selected = exact[0]
            else:
                matches = [p for p in paths if file_digest(p) == pinned["sha256"]]
                if len(matches) != 1:
                    raise ReaderError(
                        "Source changed: no unique PDF matches the saved SHA-256. Restore the original or explicitly choose a replacement plan. Progress was preserved."
                    )
                selected = matches[0]
        elif len(paths) != 1:
            raise ReaderError(
                "Several PDFs are in book/. Ask the user which filename to prepare; do not choose silently."
            )
        else:
            selected = paths[0]
        reader = pdf_reader(selected)
        opened_hash = pdf_digest(reader)
        if file_digest(selected) != opened_hash:
            raise ReaderError(
                "Source changed while it was being opened. Restore the pinned PDF; progress was preserved."
            )
        info: SourceIdentity = {
            "filename": selected.relative_to(self.root).as_posix(),
            "sha256": opened_hash,
            "page_count": len(reader.pages),
        }
        if pinned and check and info["sha256"] != pinned["sha256"]:
            raise ReaderError(
                "Source changed while it was being opened. Restore the pinned PDF; progress was preserved."
            )
        if pinned and check and info["page_count"] != pinned["page_count"]:
            raise ReaderError(
                "Source page count differs from saved identity. Progress was preserved."
            )
        return selected, reader, info

    def context(self) -> Record:
        state = self.state()
        next_part = None
        if state["phase"] != "unprepared":
            parts = self.plan()["parts"]
            if not 0 <= state["next_index"] <= len(parts):
                raise ReaderError("Reading cursor is outside the saved plan.")
            if state["next_index"] < len(parts):
                next_part = parts[state["next_index"]]
        return {
            "book_id": state["book_id"],
            "phase": state["phase"],
            "revision": state["revision"],
            "next_part": next_part,
            "last_part": state.get("last_part"),
            "continuation": state["continuation"],
        }

    def inspect(self, filename: str | None = None) -> Record:
        _, reader, source = self.source(filename)
        metadata = reader.metadata or {}
        outlines = []

        def walk(items: list[Any], depth: int = 0) -> None:
            for item in items:
                if len(outlines) >= 120:
                    return
                if isinstance(item, list):
                    walk(item, depth + 1)
                else:
                    try:
                        page_index = reader.get_destination_page_number(item)
                        if page_index is None:
                            continue
                        outlines.append(
                            {
                                "title": str(item.title),
                                "pdf_page": page_index + 1,
                                "depth": depth,
                            }
                        )
                    except Exception:
                        continue

        try:
            walk(reader.outline)
        except Exception:
            pass
        return {
            "source": source,
            "title": str(metadata.get("/Title", "")),
            "author": str(metadata.get("/Author", "")),
            "outline": outlines,
            "note": "Metadata and navigation only; no page text has been read.",
        }

    def extract(
        self,
        *,
        purpose: str,
        first: int,
        last: int,
        part_id: str | None = None,
        offset: int = 0,
        limit: int = MAX_BATCH_CHARS,
        filename: str | None = None,
    ) -> Record:
        if not 1000 <= limit <= MAX_BATCH_CHARS or offset < 0:
            raise ReaderError(
                "Extraction limit must be 1,000-24,000 characters and offset nonnegative."
            )
        _, reader, info = self.source(filename)
        if not 1 <= first <= last <= len(reader.pages) or last - first >= 8:
            raise ReaderError("Read 1-8 valid PDF pages per extraction; page numbers start at 1.")
        part = None
        if purpose == "part":
            context = self.context()
            part = context["next_part"]
            if not part or part["id"] != part_id:
                raise ReaderError("Only the next planned unread part may be extracted.")
            if first < part["pdf_start"] or last > part["pdf_end"]:
                raise ReaderError("Requested pages exceed the current part's boundary.")
        elif self.state()["phase"] != "unprepared":
            raise ReaderError(
                "Front-matter extraction is for preparation only; use saved parts afterward."
            )
        pieces, remaining, next_cursor = [], limit, None
        for page in range(first, last + 1):
            try:
                text = reader.pages[page - 1].extract_text() or ""
            except Exception as error:
                raise ReaderError(
                    f"Page {page} could not be extracted; inspect its rendered page: {error}"
                ) from error
            if part and page == part["pdf_start"] and part.get("start_at"):
                position = text.find(part["start_at"])
                if position < 0:
                    raise ReaderError(
                        "Start marker not found. Inspect the boundary page; do not silently read a different section."
                    )
                text = text[position:]
            if part and page == part["pdf_end"] and part.get("stop_before"):
                position = text.find(part["stop_before"])
                if position < 0:
                    raise ReaderError(
                        "Stop marker not found. Inspect the boundary page before proceeding."
                    )
                text = text[:position]
            start = offset if page == first else 0
            if start > len(text):
                raise ReaderError("Text offset exceeds the selected page's bounded text.")
            available = text[start:]
            taken = available[:remaining]
            pieces.append({"pdf_page": page, "text": taken})
            remaining -= len(taken)
            if len(taken) < len(available):
                next_cursor = {"page": page, "offset": start + len(taken)}
                break
            if remaining == 0 and page < last:
                next_cursor = {"page": page + 1, "offset": 0}
                break
        return {
            "source_sha256": info["sha256"],
            "pages": pieces,
            "next_cursor": next_cursor,
            "note": "Continue from next_cursor if present. Empty or broken text requires visual inspection, not guessing.",
        }

    def prepare(self, payload: Record, revision: int) -> Record:
        state = self.state()
        self.expect_revision(state, revision)
        if state["phase"] != "unprepared":
            self.source()
            return {"unchanged": True, "context": self.context()}
        _, _, source = self.source(payload.get("source_filename"))
        if payload.get("source_sha256") != source["sha256"]:
            raise ReaderError(
                "Preparation source hash is missing or changed. Inspect the source again."
            )
        parts = payload.get("parts", [])
        self.validate_plan_parts(parts, source["page_count"])
        front = payload.get("front_matter", {})
        if not isinstance(front, dict):
            raise ReaderError("front_matter must be an object.")
        for key in ("contents_pages", "introduction_pages"):
            if not isinstance(front.get(key), list):
                raise ReaderError(
                    f"Record front_matter.{key}, or an empty list with an explanation."
                )
            if any(type(p) is not int or not 1 <= p <= source["page_count"] for p in front[key]):
                raise ReaderError("Invalid front-matter PDF page number.")
        for key in ("title", "author", "context"):
            if key in payload and not isinstance(payload[key], str):
                raise ReaderError(f"{key} must be text.")
        plan = {
            "version": VERSION,
            "title": payload.get("title") or state["book_id"],
            "author": payload.get("author", ""),
            "front_matter": front,
            "parts": parts,
        }
        note = payload.get(
            "context", "Contents and introduction reviewed. No lesson has been saved."
        )
        self.check_context(note, [], valid_ids=set())
        state.update(
            phase="prepared",
            source=source,
            next_index=0,
            last_part=None,
            continuation={"text": note, "references": []},
            revision=revision + 1,
        )
        self.commit(
            {
                "plan.md": render_plan(plan),
                "answers.md": render_answers([]),
                "state.json": json_text(state),
            }
        )
        return self.context()

    @staticmethod
    def validate_plan_parts(parts: Any, page_count: int) -> None:
        if not isinstance(parts, list):
            raise ReaderError("parts must be an ordered list.")
        if not parts:
            raise ReaderError("A reading plan needs at least one part.")
        previous = None
        chapter_counts = {}
        for part in parts:
            try:
                if not isinstance(part, dict):
                    raise ValueError("each part must be an object")
                if type(part.get("chapter")) is not int or type(part.get("part")) is not int:
                    raise ValueError("chapter and part must be integers")
                chapter, number = part["chapter"], part["part"]
                if part["id"] != f"C{chapter:02d}-P{number:02d}" or chapter < 1:
                    raise ValueError("ID must match chapter and part")
                chapter_counts[chapter] = chapter_counts.get(chapter, 0) + 1
                if number != chapter_counts[chapter]:
                    raise ValueError("part numbers must be consecutive in each chapter")
                if not isinstance(part.get("title"), str) or not part["title"].strip():
                    raise ValueError("part title required")
                start, end = part["pdf_start"], part["pdf_end"]
                if (
                    type(start) is not int
                    or type(end) is not int
                    or not 1 <= start <= end <= page_count
                ):
                    raise ValueError("invalid PDF page range")
                for marker in ("start_at", "stop_before"):
                    value = part.get(marker)
                    if value is not None and (not isinstance(value, str) or not value.strip()):
                        raise ValueError(f"{marker} must be null or nonempty text")
                if "printed_pages" in part and not isinstance(part["printed_pages"], str):
                    raise ValueError("printed_pages must be a text label")
                if previous:
                    if chapter < previous["chapter"] or start < previous["pdf_end"]:
                        raise ValueError("parts must follow source order")
                    if start == previous["pdf_end"] and not (
                        previous.get("stop_before") and part.get("start_at")
                    ):
                        raise ValueError(
                            "shared boundary pages need stop_before and start_at markers"
                        )
                if not isinstance(part.get("sections"), list) or not part["sections"]:
                    raise ValueError("record the section headings used to define the part")
                if any(
                    not isinstance(section, str) or not section.strip()
                    for section in part["sections"]
                ):
                    raise ValueError("section headings must be nonempty text")
                previous = part
            except (KeyError, TypeError, ValueError) as error:
                raise ReaderError(f"Invalid reading-plan item: {error}") from error

    def refine_plan(self, payload: Record, revision: int) -> Record:
        state = self.state()
        self.expect_revision(state, revision)
        if state["phase"] != "prepared":
            raise ReaderError("Only the unread tail of a prepared unfinished book can be refined.")
        self.validate()
        self.source()
        plan = self.plan()
        parts = payload.get("parts")
        if not isinstance(parts, list):
            raise ReaderError("parts must be an ordered list.")
        self.validate_plan_parts(parts, state["source"]["page_count"])
        saved_count = state["next_index"]
        if parts[:saved_count] != plan["parts"][:saved_count] or len(parts) <= saved_count:
            raise ReaderError(
                "A plan refinement must preserve all saved parts and leave an unread tail."
            )
        plan["parts"] = parts
        state["revision"] += 1
        self.commit({"plan.md": render_plan(plan), "state.json": json_text(state)})
        return self.context()

    @staticmethod
    def check_context(text: str, references: list[str], valid_ids: set[str]) -> None:
        if not isinstance(text, str) or len(text.split()) > MAX_CONTEXT_WORDS:
            raise ReaderError("Continuation context must be text of at most 400 words.")
        if not isinstance(references, list) or any(item not in valid_ids for item in references):
            raise ReaderError("Continuation references must identify saved parts only.")

    def saved_parts(self) -> list[Record]:
        plan_parts = self.plan()["parts"]
        planned = {part["id"]: index for index, part in enumerate(plan_parts)}
        source = self.state().get("source")
        result = []
        for directory in self.path("parts").iterdir():
            if directory.name.startswith("."):
                continue
            self.path(directory.relative_to(self.root).as_posix())
            part_id = directory.name
            if not directory.is_dir() or part_id not in planned:
                raise ReaderError(
                    f"Unexpected lesson entry {directory.name}; do not omit it silently from summaries."
                )
            path = self.path(part_filename(part_id))
            if not path.exists():
                continue
            self.path(path.relative_to(self.root).as_posix(), must_exist=True)
            text = path.read_text(encoding="utf-8")
            metadata = machine_read(text, "part")
            expected = plan_parts[planned[part_id]]
            required = {"version", "id", "source_sha256", "pdf_pages", "questions"}
            if not isinstance(metadata, dict) or set(metadata) != required:
                raise ReaderError(f"Malformed part metadata: {path.name}")
            if (
                metadata["version"] != VERSION
                or metadata["id"] != part_id
                or not source
                or metadata["source_sha256"] != source["sha256"]
                or metadata["pdf_pages"] != [expected["pdf_start"], expected["pdf_end"]]
            ):
                raise ReaderError(f"Part metadata does not match its source or plan: {path.name}")
            questions = metadata["questions"]
            if not isinstance(questions, list) or len(questions) != 2:
                raise ReaderError(f"Part needs exactly two registered questions: {path.name}")
            for number, question in enumerate(questions, 1):
                if (
                    not isinstance(question, dict)
                    or set(question) != {"id", "part_id", "question"}
                    or question["id"] != f"{part_id}-Q{number:02d}"
                    or question["part_id"] != part_id
                    or not isinstance(question["question"], str)
                    or not question["question"].strip()
                ):
                    raise ReaderError(f"Invalid question metadata in {path.name}")
            visible = re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()
            title_line = f"# {part_id} · {expected['title']}"
            question_heading = "## Check your understanding"
            if not visible.startswith(title_line) or question_heading not in visible:
                raise ReaderError(f"Missing visible lesson or question section: {path.name}")
            body = visible[len(title_line) :].split(question_heading, 1)[0].strip()
            if not body:
                raise ReaderError(f"Missing visible lesson body: {path.name}")
            for question in questions:
                if f"### {question['id']}\n\n{question['question']}" not in visible:
                    raise ReaderError(f"Missing visible comprehension question in {path.name}")
            result.append(
                {
                    "id": part_id,
                    "path": path.relative_to(self.root).as_posix(),
                    "sha256": digest(text.encode()),
                    "text": text,
                }
            )
        result.sort(key=lambda item: planned[item["id"]])
        return result

    def save_part(self, payload: Record, revision: int) -> Record:
        state = self.state()
        part_id = payload.get("part_id", "")
        if not PART_ID.fullmatch(part_id):
            raise ReaderError("Invalid part ID.")
        plan = self.plan()
        matches = [part for part in plan["parts"] if part["id"] == part_id]
        if not matches:
            raise ReaderError("Part is not in the saved plan.")
        part = matches[0]
        body, questions = payload.get("body", ""), payload.get("questions", [])
        if (
            not isinstance(body, str)
            or not body.strip()
            or len(questions) != 2
            or any(not isinstance(q, str) or not q.strip() for q in questions)
        ):
            raise ReaderError("Save a nonempty lesson and exactly two nonempty questions.")
        if re.search(r"^#{1,6}\s+Check your understanding", body, re.M | re.I):
            raise ReaderError("Supply the body only. The helper adds the question section.")
        filename = part_filename(part_id)
        source = state.get("source")
        if not isinstance(source, dict):
            raise ReaderError("Source identity is missing. Prepare the book before saving parts.")
        output = render_part(part, body, questions, source)
        path = self.path(filename)
        if path.exists():
            if path.read_text(encoding="utf-8") == output:
                return {"unchanged": True, "path": filename, "context": self.context()}
            raise ReaderError(
                "This part already exists. Do not overwrite a saved lesson or change its question IDs."
            )
        self.expect_revision(state, revision)
        current = self.context()["next_part"]
        if not current or current["id"] != part_id:
            raise ReaderError("Only the next planned part can be saved.")
        self.source()
        entries = self.answers()
        for index, question in enumerate(questions, 1):
            entries.append(
                {
                    "id": f"{part_id}-Q{index:02d}",
                    "part_id": part_id,
                    "question": question.strip(),
                    "status": "unanswered",
                    "correct_answer": None,
                }
            )
        saved_ids = {item["id"] for item in self.saved_parts()} | {part_id}
        context = payload.get("context", "")
        references = payload.get("references", [part_id])
        if not context.strip():
            raise ReaderError("Supply a short continuation context for the next session.")
        self.check_context(context, references, saved_ids)
        state.update(
            next_index=state["next_index"] + 1,
            last_part=part_id,
            continuation={"text": context, "references": references},
            revision=revision + 1,
        )
        if state["next_index"] == len(plan["parts"]):
            state["phase"] = "finished"
        self.commit(
            {
                filename: output,
                "answers.md": render_answers(entries),
                "state.json": json_text(state),
            }
        )
        return {
            "path": filename,
            "question_ids": [entries[-2]["id"], entries[-1]["id"]],
            "context": self.context(),
        }

    def pending_questions(self) -> list[Record]:
        return [
            {"id": item["id"], "question": item["question"], "part": part_filename(item["part_id"])}
            for item in self.answers()
            if item["status"] == "unanswered"
        ]

    def save_answers(self, payload: Record, revision: int) -> Record:
        # The schema intentionally has no field for a submission, grading, or attempts.
        if (
            set(payload) != {"answers"}
            or not isinstance(payload["answers"], list)
            or not payload["answers"]
        ):
            raise ReaderError(
                "Answer payload accepts only an answers list of id/correct_answer objects."
            )
        entries, state = self.answers(), self.state()
        self.expect_revision(state, revision)
        by_id = {entry["id"]: entry for entry in entries}
        seen = set()
        for answer in payload["answers"]:
            if set(answer) != {"id", "correct_answer"}:
                raise ReaderError(
                    "Store only id and correct_answer. Do not persist submitted answers or grading."
                )
            identifier, text = answer["id"], answer["correct_answer"]
            if (
                identifier not in by_id
                or identifier in seen
                or not QUESTION_ID.fullmatch(identifier)
            ):
                raise ReaderError("Unknown or duplicate question ID.")
            if not isinstance(text, str) or not text.strip():
                raise ReaderError("A correct answer must be nonempty standalone text.")
            seen.add(identifier)
            by_id[identifier].update(status="answered", correct_answer=text.strip())
        state["revision"] += 1
        self.commit({"answers.md": render_answers(entries), "state.json": json_text(state)})
        return {
            "saved": sorted(seen),
            "remaining": len(self.pending_questions()),
            "revision": state["revision"],
        }

    def start_summary(self, limit: int = MAX_BATCH_CHARS) -> Record:
        if not 1000 <= limit <= MAX_BATCH_CHARS:
            raise ReaderError("Summary batch size must be 1,000-24,000 characters.")
        if self.state()["phase"] == "unprepared":
            return {"empty": True, "message": "No parts have been saved."}
        self.validate()
        parts = self.saved_parts()
        if not parts:
            return {"empty": True, "message": "No parts have been saved."}
        batches, current, size = [], [], 0
        for part in parts:
            for offset in range(0, len(part["text"]), limit):
                fragment = {
                    "id": part["id"],
                    "offset": offset,
                    "text": part["text"][offset : offset + limit],
                }
                if current and size + len(fragment["text"]) > limit:
                    batches.append(current)
                    current, size = [], 0
                current.append(fragment)
                size += len(fragment["text"])
        if current:
            batches.append(current)
        manifest = [{key: value for key, value in item.items() if key != "text"} for item in parts]
        # Store ranges, not duplicate lesson bodies or old cumulative summaries.
        ranges = [
            [
                {
                    "id": fragment["id"],
                    "offset": fragment["offset"],
                    "length": len(fragment["text"]),
                }
                for fragment in batch
            ]
            for batch in batches
        ]
        session = {
            "version": VERSION,
            "session_id": secrets.token_hex(16),
            "manifest": manifest,
            "batches": ranges,
            "served": [],
        }
        atomic_write(self.path(SUMMARY_SESSION), json_text(session))
        return {
            "session_id": session["session_id"],
            "part_ids": [part["id"] for part in parts],
            "batches": len(batches),
            "max_batch_chars": limit,
            "instruction": "Read every batch before saving. This is a fresh full-coverage pass.",
        }

    def summary_session(self) -> Record:
        path = self.path(SUMMARY_SESSION, must_exist=True)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise ReaderError(
                "Invalid summary session. Start a new complete summary pass."
            ) from error

    def check_manifest(self, session: Record) -> None:
        current = [
            {key: value for key, value in part.items() if key != "text"}
            for part in self.saved_parts()
        ]
        if current != session["manifest"]:
            raise ReaderError(
                "Saved parts changed during summary creation. Restart the full summary pass."
            )

    def summary_batch(self, index: int, session_id: str) -> Record:
        session = self.summary_session()
        if session.get("session_id") != session_id:
            raise ReaderError(
                "This summary pass was superseded. Start or join the current pass explicitly."
            )
        self.check_manifest(session)
        if not 0 <= index < len(session["batches"]):
            raise ReaderError("Invalid summary batch index; indexes start at 0.")
        by_id = {part["id"]: part for part in self.saved_parts()}
        result = []
        for item in session["batches"][index]:
            text = by_id[item["id"]]["text"]
            result.append(
                {
                    "id": item["id"],
                    "offset": item["offset"],
                    "text": text[item["offset"] : item["offset"] + item["length"]],
                }
            )
        if index not in session["served"]:
            session["served"].append(index)
            atomic_write(self.path(SUMMARY_SESSION), json_text(session))
        return {"batch": index, "total_batches": len(session["batches"]), "parts": result}

    def save_summary(self, payload: Record, revision: int) -> Record:
        state, session = self.state(), self.summary_session()
        self.expect_revision(state, revision)
        if payload.get("session_id") != session.get("session_id"):
            raise ReaderError(
                "This summary pass was superseded. Rebuild from the current saved parts."
            )
        self.check_manifest(session)
        if set(session["served"]) != set(range(len(session["batches"]))):
            raise ReaderError("Read all saved-part batches before saving a cumulative summary.")
        expected = [item["id"] for item in session["manifest"]]
        covered = payload.get("covered_part_ids", [])
        if sorted(covered) != sorted(expected) or len(covered) != len(set(covered)):
            raise ReaderError("Summary coverage must include every saved part exactly once.")
        body = payload.get("body", "")
        if not isinstance(body, str) or not body.strip():
            raise ReaderError("Summary body is empty.")
        text = machine_block("summary", {"version": VERSION, "manifest": session["manifest"]})
        noun = "part" if len(expected) == 1 else "parts"
        text += f"\n# Summary\n\nCovers {len(expected)} saved {noun}.\n\n{body.strip()}\n"
        state["revision"] += 1
        self.commit({"summary.md": text, "state.json": json_text(state)})
        self.path(SUMMARY_SESSION).unlink(missing_ok=True)
        return {"path": "summary.md", "covered_part_ids": expected, "revision": state["revision"]}

    def validate(self) -> Record:
        state = self.state()
        entries = self.answers()
        if state["phase"] == "unprepared":
            if entries or state["next_index"]:
                raise ReaderError("Unprepared state has unexpected questions or progress.")
            return {"ok": True, "phase": "unprepared"}
        plan, parts = self.plan(), self.saved_parts()
        expected_ids = [item["id"] for item in plan["parts"][: state["next_index"]]]
        if not 0 <= state["next_index"] <= len(plan["parts"]):
            raise ReaderError("Reading cursor is outside the saved plan.")
        if state["last_part"] != (expected_ids[-1] if expected_ids else None):
            raise ReaderError("Last-part pointer disagrees with saved progress.")
        if (state["phase"] == "finished") != (state["next_index"] == len(plan["parts"])):
            raise ReaderError("Reading phase disagrees with the remaining plan.")
        if [part["id"] for part in parts] != expected_ids:
            raise ReaderError(
                "Saved parts do not match the cursor. Restore consistency before continuing."
            )
        expected_questions = []
        for part in parts:
            meta = machine_read(part["text"], "part")
            expected_questions.extend(meta["questions"])
        actual_questions = [
            {"id": item["id"], "part_id": item["part_id"], "question": item["question"]}
            for item in entries
        ]
        if actual_questions != expected_questions:
            raise ReaderError("Question register differs from saved parts.")
        for item in entries:
            if set(item) != {"id", "part_id", "question", "status", "correct_answer"}:
                raise ReaderError(
                    "Unexpected answer-register fields; do not store submissions or grading."
                )
            if (item["status"] == "unanswered" and item["correct_answer"] is not None) or (
                item["status"] == "answered" and not item["correct_answer"]
            ):
                raise ReaderError("Question status and correct answer disagree.")
            if item["status"] not in {"unanswered", "answered"}:
                raise ReaderError("Unknown question status.")
        self.check_context(
            state["continuation"]["text"], state["continuation"]["references"], set(expected_ids)
        )
        summary = self.path("summary.md")
        stale = False
        if summary.exists():
            current = [
                {key: value for key, value in item.items() if key != "text"} for item in parts
            ]
            stale = (
                machine_read(summary.read_text(encoding="utf-8"), "summary")["manifest"] != current
            )
        return {
            "ok": True,
            "saved_parts": len(parts),
            "unanswered": len(self.pending_questions()),
            "summary_stale": stale,
        }

    def pandoc_ast(self, text: str) -> Record:
        try:
            result = subprocess.run(
                [pandoc_command(), "--from=gfm+tex_math_dollars-raw_html", "--to=json"],
                env=pandoc_environment(),
                input=text,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=True,
                cwd=self.root,
            )
        except FileNotFoundError as error:
            raise ReaderError(
                "Pandoc is not installed. Markdown was saved; install Pandoc to build reader.html."
            ) from error
        except subprocess.CalledProcessError as error:
            raise ReaderError(
                f"Pandoc could not parse the Markdown: {error.stderr[:600]}"
            ) from error
        document = json.loads(result.stdout)
        mermaid_blocks(document)
        return document

    def local_images(self, node: Any, base: Path = Path(".")) -> None:
        # Work on actual Image nodes: reference images are resolved by Pandoc,
        # while examples inside code blocks remain ordinary code.
        if isinstance(node, dict):
            if node.get("t") == "Image":
                target = node["c"][-1][0]
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or target.startswith("/"):
                    raise ReaderError("Use only relative local figures inside the current book.")
                relative = (base / unquote(parsed.path)).as_posix()
                self.path(relative, must_exist=True)
                node["c"][-1][0] = relative
            for value in node.values():
                self.local_images(value, base)
        elif isinstance(node, list):
            for item in node:
                self.local_images(item, base)

    def pdf_bytes(
        self, document: Record, answers: dict[str, tuple[str, Record]], title: str
    ) -> bytes:
        try:
            import typst
        except ImportError as error:
            raise ReaderError(
                "PDF engine is missing. Run uv sync --locked outside the reading session."
            ) from error

        pdf_document = deepcopy(document)

        def diagram_source(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("t") == "RawBlock" and node["c"][0] == "html":
                    match = re.fullmatch(r'<pre class="mermaid">(.*?)</pre>', node["c"][1], re.S)
                    if match:
                        node.update(
                            t="CodeBlock", c=[["", ["mermaid"], []], html.unescape(match[1])]
                        )
                        return
                for value in node.values():
                    diagram_source(value)
            elif isinstance(node, list):
                for item in node:
                    diagram_source(item)

        blocks = []
        for block in pdf_document["blocks"]:
            if block.get("t") == "Header" and block["c"][2] == [
                {"t": "Str", "c": "Reading"},
                {"t": "Space"},
                {"t": "Str", "c": "parts"},
            ]:
                continue
            token = (
                block["c"][0].get("c")
                if block.get("t") == "Para"
                and len(block["c"]) == 1
                and block["c"][0].get("t") == "Str"
                else None
            )
            if token in answers:
                identifier, answer = answers[token]
                blocks.append(
                    {"t": "Header", "c": [2, [identifier, [], []], [{"t": "Str", "c": identifier}]]}
                )
                blocks.extend(deepcopy(answer["blocks"]))
            else:
                blocks.append(block)
        pdf_document["blocks"] = blocks
        diagram_source(pdf_document)
        result = subprocess.run(
            [pandoc_command(), "--from=json", "--to=typst", "--wrap=none"],
            env=pandoc_environment(),
            input=json.dumps(pdf_document),
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=self.root,
        )
        if result.returncode:
            raise ReaderError(f"PDF conversion failed: {result.stderr[:600]}")
        runtime = os.environ.get("READER_PDF_RUNTIME")
        if not runtime:
            raise ReaderError(
                "Open the book through the launcher to configure the shared PDF engine."
            )
        assets = Path(runtime)
        template = (assets / "pdf.typ").read_text(encoding="utf-8")
        try:
            compiler = typst.Compiler(
                root=self.root,
                ignore_system_fonts=True,
                sys_inputs={"title": title},
                package_path=assets,
                package_cache_path=assets,
            )
            pdf = compiler.compile(input=(template + result.stdout).encode("utf-8"))
        except typst.TypstError as error:
            raise ReaderError(f"PDF compilation failed: {error}") from error
        if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
            raise ReaderError("PDF renderer did not return a PDF document.")
        return pdf

    def render(self) -> Record:
        self.validate()
        state = self.state()
        if state["phase"] == "unprepared":
            raise ReaderError("Prepare the book before rendering the reading view.")
        title = self.plan()["title"]
        document = self.pandoc_ast("# Reading parts\n")
        document["meta"]["pagetitle"] = {"t": "MetaString", "c": title}
        for part in self.saved_parts():
            text = re.sub(r"<!-- reader-.*?-->\s*", "", part["text"], flags=re.S)
            parsed = self.pandoc_ast(text)
            self.local_images(parsed, Path(part["path"]).parent)
            document["blocks"].extend(parsed["blocks"])
        summary = self.path("summary.md")
        if summary.exists():
            if self.validate()["summary_stale"]:
                document["blocks"].extend(
                    self.pandoc_ast(
                        "> The saved summary predates changes to the parts. Ask for `summary` to refresh it.\n"
                    )["blocks"]
                )
            text = re.sub(
                r"<!-- reader-.*?-->\s*",
                "",
                summary.read_text(encoding="utf-8"),
                flags=re.S,
            )
            parsed = self.pandoc_ast(text)
            self.local_images(parsed)
            document["blocks"].extend(parsed["blocks"])
        document["blocks"].extend(self.pandoc_ast("# Correct answers\n")["blocks"])
        entries = self.answers()
        replacements = {}
        for item in entries:
            if item["status"] == "answered":
                token = f"READERANSWER{len(replacements)}PLACEHOLDER"
                parsed = self.pandoc_ast(item["correct_answer"])
                self.local_images(parsed)
                replacements[token] = (item["id"], parsed)
                document["blocks"].extend(self.pandoc_ast(token)["blocks"])
        if not replacements:
            document["blocks"].extend(
                self.pandoc_ast("No correct answers have been saved yet.\n")["blocks"]
            )
        template = self.path(".reader-template.html", must_exist=True)
        command = [
            pandoc_command(),
            "--from=json",
            "--to=html5",
            "--standalone",
            "--mathml",
            "--highlight-style=breezedark",
            "--template",
            str(template),
        ]
        try:
            result = subprocess.run(
                command,
                env=pandoc_environment(),
                input=json.dumps(document),
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=True,
                cwd=self.root,
            )
        except subprocess.CalledProcessError as error:
            raise ReaderError(f"Pandoc could not render the view: {error.stderr[:600]}") from error
        output = result.stdout
        for token, (identifier, answer) in replacements.items():
            rendered = subprocess.run(
                [
                    pandoc_command(),
                    "--from=json",
                    "--to=html5",
                    "--mathml",
                    "--highlight-style=breezedark",
                ],
                env=pandoc_environment(),
                input=json.dumps(answer),
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=True,
                cwd=self.root,
            ).stdout
            details = f'<details class="answer"><summary>{html.escape(identifier)} · Correct answer</summary>{rendered}</details>'
            output = output.replace(f"<p>{token}</p>", details)

        def embed(match: re.Match[str]) -> str:
            source = unquote(html.unescape(match.group(1)))
            path = self.path(source, must_exist=True)
            import base64

            mime = {
                ".svg": "image/svg+xml",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
            }.get(path.suffix.lower())
            if not mime:
                raise ReaderError("Only local SVG, PNG, or JPEG figures may be embedded.")
            return (
                'src="data:'
                + mime
                + ";base64,"
                + base64.b64encode(path.read_bytes()).decode()
                + '"'
            )

        output = re.sub(r'src="([^"]+)"', embed, output)
        for target in re.findall(r'href="([^"]*)"', output):
            decoded = html.unescape(target)
            for _ in range(2):
                decoded = unquote(decoded)
            decoded = re.sub(r"[\x00-\x20]", "", decoded)
            scheme = urlsplit(decoded).scheme.lower()
            if scheme and scheme not in {"http", "https", "mailto"}:
                raise ReaderError(
                    "Unsupported hyperlink scheme in saved content. Use a normal web or local link."
                )
        atomic_write(self.path("reader.html"), output)
        try:
            pdf = self.pdf_bytes(document, replacements, title)
            atomic_write_bytes(self.path("reader.pdf"), pdf)
        except (ReaderError, OSError) as error:
            raise ReaderError(
                f"HTML was saved, but PDF could not be refreshed. The previous PDF is unchanged: {error}"
            ) from error
        return {
            "path": "reader.html",
            "pdf_path": "reader.pdf",
            "note": "Both files cover all saved parts. PDF includes linked contents, page numbers, bookmarks, and offline diagrams. Correct answers are in a separate section.",
        }


def render_plan(plan: Record) -> str:
    text = machine_block("plan", plan)
    text += f"\n# Reading plan: {plan['title']}\n\nPDF pages are 1-based; printed page labels are separate.\n\n"
    text += "| Part | Topic | PDF pages | Printed pages |\n| --- | --- | --- | --- |\n"
    for part in plan["parts"]:
        title = part["title"].replace("|", "\\|").replace("\n", " ")
        text += f"| {part['id']} | {title} | {part['pdf_start']}-{part['pdf_end']} | {part.get('printed_pages', '')} |\n"
    text += "\nThe metadata block is authoritative. Change an unread plan only with explicit user direction; preserve all saved IDs.\n"
    return text


def render_answers(entries: list[Record]) -> str:
    text = machine_block("answers", entries)
    text += "\n# Questions and correct answers\n\nOnly correct answers are stored. Unanswered entries stay available through `questions`.\n"
    for item in entries:
        text += f"\n## {item['id']}\n\nPart: [Read the part]({part_filename(item['part_id'])})\n\nStatus: **{item['status']}**\n\n{item['question']}\n"
        if item["status"] == "answered":
            text += f"\n### Correct answer\n\n{item['correct_answer']}\n"
    return text


def render_part(part: Record, body: str, questions: list[str], source: Record) -> str:
    entries = [
        {"id": f"{part['id']}-Q{i:02d}", "part_id": part["id"], "question": q.strip()}
        for i, q in enumerate(questions, 1)
    ]
    text = machine_block(
        "part",
        {
            "version": VERSION,
            "id": part["id"],
            "source_sha256": source["sha256"],
            "pdf_pages": [part["pdf_start"], part["pdf_end"]],
            "questions": entries,
        },
    )
    text += f"\n# {part['id']} · {part['title']}\n\n{body.strip()}\n\n## Check your understanding\n"
    for entry in entries:
        text += f"\n### {entry['id']}\n\n{entry['question']}\n"
    text += f"\n---\n\nSource: PDF pages {part['pdf_start']}-{part['pdf_end']}"
    if part.get("printed_pages"):
        text += f"; printed pages {part['printed_pages']}"
    text += ".\n"
    return text


def initial_state(book_id: str) -> Record:
    return {
        "version": VERSION,
        "book_id": book_id,
        "phase": "unprepared",
        "revision": 0,
        "source": None,
        "next_index": 0,
        "last_part": None,
        "continuation": {
            "text": "Not prepared. Read only contents and introduction when asked to prepare.",
            "references": [],
        },
    }


def main() -> int:
    if sys.version_info[:2] != (3, 13):
        print(
            "Use Python 3.13 from the project uv environment. Open this book through the launcher.",
            file=sys.stderr,
        )
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("context", "questions", "validate", "render", "summary-inputs"):
        command = sub.add_parser(name)
        if name == "summary-inputs":
            command.add_argument("--limit", type=int, default=MAX_BATCH_CHARS)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--source")
    extract = sub.add_parser("extract")
    extract.add_argument("--purpose", choices=("front", "part"), required=True)
    extract.add_argument("--first", type=int, required=True)
    extract.add_argument("--last", type=int, required=True)
    extract.add_argument("--part")
    extract.add_argument("--offset", type=int, default=0)
    extract.add_argument("--limit", type=int, default=MAX_BATCH_CHARS)
    extract.add_argument("--source")
    for name in ("prepare", "refine-plan", "save-part", "save-answers", "save-summary"):
        command = sub.add_parser(name)
        command.add_argument(
            "--input",
            required=True,
            help="Book-local ignored JSON payload; never include learner submissions.",
        )
        command.add_argument("--revision", type=int, required=True)
    batch = sub.add_parser("summary-batch")
    batch.add_argument("--index", type=int, required=True)
    batch.add_argument("--session", required=True)
    args = parser.parse_args()
    try:
        script_root = Path(__file__).resolve().parent
        if Path.cwd().resolve() != script_root:
            raise ReaderError("Run this book's reader.py from its own book directory.")
        book = Book(script_root)
        with book.locked():
            if args.command in {
                "context",
                "questions",
                "validate",
                "render",
                "summary-inputs",
                "summary-batch",
                "inspect",
                "extract",
            }:
                methods = {
                    "context": book.context,
                    "questions": book.pending_questions,
                    "validate": book.validate,
                    "render": book.render,
                    "summary-inputs": lambda: book.start_summary(args.limit),
                    "summary-batch": lambda: book.summary_batch(args.index, args.session),
                    "inspect": lambda: book.inspect(args.source),
                    "extract": lambda: book.extract(
                        purpose=args.purpose,
                        first=args.first,
                        last=args.last,
                        part_id=args.part,
                        offset=args.offset,
                        limit=args.limit,
                        filename=args.source,
                    ),
                }
                result = methods[args.command]()
            else:
                path = book.path(args.input, must_exist=True)
                if not path.name.startswith(".reader-"):
                    raise ReaderError(
                        "Put helper inputs in an ignored .reader-*.json file inside the book."
                    )
                payload = json.loads(path.read_text(encoding="utf-8"))
                method = {
                    "prepare": book.prepare,
                    "refine-plan": book.refine_plan,
                    "save-part": book.save_part,
                    "save-answers": book.save_answers,
                    "save-summary": book.save_summary,
                }[args.command]
                result = method(payload, args.revision)
                path.unlink(missing_ok=True)
        print(json_text(result), end="")
    except (ReaderError, ValueError, OSError, KeyError, TypeError) as error:
        print(json_text({"error": str(error)}), file=sys.stderr, end="")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
