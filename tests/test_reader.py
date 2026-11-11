from __future__ import annotations

import base64
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from runtime import reader
from scripts import book_reader as setup

ROOT = Path(__file__).resolve().parents[1]


def test_pdf_runtime_archive_has_no_local_packaging_metadata() -> None:
    path = ROOT / "runtime/assets/merman-0.5.0.tar.gz"
    header = path.read_bytes()[:10]
    assert header[:2] == b"\x1f\x8b"
    assert header[3] == 0  # No original filename, comment, or extra fields.
    assert int.from_bytes(header[4:8], "little") == 0
    with tarfile.open(path) as archive:
        for member in archive:
            assert (member.uid, member.gid, member.uname, member.gname) == (0, 0, "", "")
            assert member.mtime == 0
            assert set(member.pax_headers) <= {"path", "linkpath"}


def make_pdf(path: Path, texts: list[str] | None = None, title: str = "A small test book") -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    texts = texts or [
        "Contents",
        "Introduction",
        "First topic",
        "End of first topic\nSecond topic\nNew material",
        "End of second topic",
    ]
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    for text in texts:
        page = writer.add_blank_page(width=400, height=500)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        lines = [
            line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            for line in text.splitlines()
        ]
        content_stream = DecodedStreamObject()
        content_stream.set_data(
            (
                "BT /F1 12 Tf 40 450 Td "
                + " 0 -18 Td ".join(f"({line}) Tj" for line in lines)
                + " ET"
            ).encode("ascii")
        )
        page[NameObject("/Contents")] = writer._add_object(content_stream)
    writer.add_metadata({"/Title": title, "/Author": "Test author"})
    with path.open("wb") as pdf_stream:
        writer.write(pdf_stream)


class TestReader:
    @pytest.fixture(autouse=True)
    def workspace(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.base = tmp_path
        self.root = self.base / "test-book"
        setup.new_book(self.root)
        monkeypatch.setenv("READER_PDF_RUNTIME", str(setup.pdf_runtime_path()))
        make_pdf(self.root / "book/source.pdf")
        self.book = reader.Book(self.root)

    def payload(self) -> reader.Record:
        info = self.book.inspect()
        return {
            "title": "A small test book",
            "author": "Test author",
            "source_sha256": info["source"]["sha256"],
            "front_matter": {"contents_pages": [1], "introduction_pages": [2]},
            "parts": [
                {
                    "id": "C01-P01",
                    "chapter": 1,
                    "part": 1,
                    "title": "First topic",
                    "pdf_start": 3,
                    "pdf_end": 4,
                    "printed_pages": "1-2",
                    "sections": ["First topic"],
                    "stop_before": "Second topic",
                },
                {
                    "id": "C02-P01",
                    "chapter": 2,
                    "part": 1,
                    "title": "Second topic",
                    "pdf_start": 4,
                    "pdf_end": 5,
                    "printed_pages": "2-3",
                    "sections": ["Second topic"],
                    "start_at": "Second topic",
                },
            ],
            "context": "The introduction is reviewed; no lesson has been saved.",
        }

    def prepare(self) -> reader.Record:
        return self.book.prepare(self.payload(), 0)

    def part_payload(self, identifier: str = "C01-P01") -> reader.Record:
        return {
            "part_id": identifier,
            "body": "Points record sampled locations. A surface connects locations.\n\n```python\nz = points[:, 2]\n```\n\n$$d = \\sqrt{x^2+y^2+z^2}$$",
            "questions": ["What does a point record?", "What does a surface add?"],
            "context": "Points give locations. Surface models add connections.",
            "references": [identifier],
        }

    def save_first(self) -> reader.Record:
        self.prepare()
        return self.book.save_part(self.part_payload(), 1)

    def test_initialization_copies_reading_framework_and_does_not_prepare(self) -> None:
        assert self.book.context()["phase"] == "unprepared"
        assert self.book.state()["source"] is None
        assert len(list((self.root / ".agents/skills").glob("*/SKILL.md"))) == 4
        assert (self.root / "reader.py").is_file()
        assert not (self.root / ".reader-assets").exists()
        with pytest.raises(ValueError):
            setup.new_book(self.root)

    def test_books_share_one_pdf_runtime_without_vendor_copies(self) -> None:
        runtime = setup.prepare_pdf_runtime()
        second = setup.new_book(self.base / "second-book")
        assert setup.prepare_pdf_runtime() == runtime
        assert setup.reader_environment()["READER_PDF_RUNTIME"] == str(runtime)
        assert (runtime / "pdf.typ").is_file()
        assert (runtime / "reader/merman/0.5.0/merman_typst_plugin.wasm").is_file()
        for book in (self.root, second):
            assert not (book / ".reader-assets").exists()
            assert not any("LICENSE" in path.name for path in book.rglob("*"))

    def test_outline_skips_unresolved_pages_but_keeps_page_zero(self) -> None:
        fake_reader = SimpleNamespace(
            metadata={},
            outline=[SimpleNamespace(title="unresolved"), SimpleNamespace(title="first")],
            get_destination_page_number=lambda item: None if item.title == "unresolved" else 0,
        )
        source = {"filename": "book/source.pdf", "sha256": "fixture", "page_count": 5}
        with patch.object(
            self.book, "source", return_value=(self.root / "book/source.pdf", fake_reader, source)
        ):
            result = self.book.inspect()
        assert result["outline"] == [{"title": "first", "pdf_page": 1, "depth": 0}]

    def test_refinement_without_parts_preserves_progress(self) -> None:
        self.prepare()
        before = self.book.state()
        with pytest.raises(reader.ReaderError, match="parts must be an ordered list"):
            self.book.refine_plan({}, 1)
        assert self.book.state() == before

    def test_missing_source_identity_cannot_save_or_advance_a_part(self) -> None:
        self.prepare()
        state = self.book.state()
        state["source"] = None
        (self.root / "state.json").write_text(reader.json_text(state), encoding="utf-8")
        with pytest.raises(reader.ReaderError, match="Source identity is missing"):
            self.book.save_part(self.part_payload(), 1)
        assert self.book.state() == state
        assert not (self.root / "parts/C01-P01/part.md").exists()

    def test_prepare_reads_no_page_bodies_by_itself_and_repeat_preserves_progress(self) -> None:
        self.prepare()
        self.book.save_part(self.part_payload(), 1)
        before = self.book.state()
        assert self.book.prepare({}, before["revision"])["unchanged"]
        assert before == self.book.state()

    def test_fresh_context_crosses_chapter_boundary_and_keeps_pending_questions(self) -> None:
        self.save_first()
        fresh = reader.Book(self.root)
        assert fresh.context()["next_part"]["id"] == "C02-P01"
        assert [q["id"] for q in fresh.pending_questions()] == ["C01-P01-Q01", "C01-P01-Q02"]
        fresh.save_part(self.part_payload("C02-P01"), 2)
        assert reader.Book(self.root).context()["next_part"] is None
        assert fresh.state()["phase"] == "finished"
        assert len(fresh.pending_questions()) == 4
        assert fresh.validate()["ok"]

    def test_next_cannot_extract_unprepared_or_future_parts(self) -> None:
        with pytest.raises(reader.ReaderError):
            self.book.extract(purpose="part", first=3, last=3, part_id="C01-P01")
        self.prepare()
        with pytest.raises(reader.ReaderError):
            self.book.extract(purpose="part", first=4, last=5, part_id="C02-P01")

    def test_boundaries_trim_shared_pages(self) -> None:
        self.prepare()
        result = self.book.extract(purpose="part", first=3, last=4, part_id="C01-P01")
        text = "".join(p["text"] for p in result["pages"])
        assert "First topic" in text
        assert "New material" not in text
        assert "Second topic" not in text
        self.book.save_part(self.part_payload(), 1)
        result = self.book.extract(purpose="part", first=4, last=5, part_id="C02-P01")
        text = "".join(p["text"] for p in result["pages"])
        assert text.startswith("Second topic")
        assert "End of first topic" not in text

    def test_missing_boundary_marker_stops_reading(self) -> None:
        payload = self.payload()
        payload["parts"][0]["stop_before"] = "Not in the PDF"
        self.book.prepare(payload, 0)
        with pytest.raises(reader.ReaderError, match="Stop marker"):
            self.book.extract(purpose="part", first=4, last=4, part_id="C01-P01")

    def test_extract_limits_have_resumable_cursor(self) -> None:
        text = "Word " * 800
        make_pdf(self.root / "book/source.pdf", [text])
        result = self.book.extract(purpose="front", first=1, last=1, limit=1000)
        assert len(result["pages"][0]["text"]) == 1000
        assert result["next_cursor"] == {"page": 1, "offset": 1000}
        following = self.book.extract(purpose="front", first=1, last=1, offset=1000, limit=1000)
        assert len(following["pages"][0]["text"]) == 1000

    def test_invalid_overlapping_plan_is_rejected_without_pin(self) -> None:
        payload = self.payload()
        del payload["parts"][0]["stop_before"]
        with pytest.raises(reader.ReaderError):
            self.book.prepare(payload, 0)
        assert self.book.state()["source"] is None

    def test_invalid_boundary_and_section_types_cannot_poison_preparation(self) -> None:
        for key, value in [
            ("stop_before", 17),
            ("start_at", ""),
            ("sections", [17]),
            ("chapter", True),
            ("printed_pages", 5),
        ]:
            payload = self.payload()
            payload["parts"][0][key] = value
            with pytest.raises(reader.ReaderError):
                self.book.prepare(payload, 0)
            assert self.book.state()["phase"] == "unprepared"

    def test_authorized_unread_refinement_preserves_saved_prefix_and_questions(self) -> None:
        self.save_first()
        original_questions = self.book.answers()
        plan = self.book.plan()["parts"]
        tail = dict(plan[1], pdf_end=4)
        second = dict(
            plan[1], id="C02-P02", part=2, title="Final topic", pdf_start=5, start_at=None
        )
        self.book.refine_plan({"parts": [plan[0], tail, second]}, 2)
        assert self.book.state()["next_index"] == 1
        assert self.book.answers() == original_questions
        assert len(self.book.plan()["parts"]) == 3
        bad = self.book.plan()["parts"]
        bad[0]["title"] = "Changed saved topic"
        with pytest.raises(reader.ReaderError, match="preserve"):
            self.book.refine_plan({"parts": bad}, 3)

    def test_missing_changed_and_renamed_source(self) -> None:
        self.prepare()
        path = self.root / "book/source.pdf"
        renamed = self.root / "book/renamed.pdf"
        path.rename(renamed)
        assert self.book.source()[2]["filename"] == "book/renamed.pdf"
        original = renamed.read_bytes()
        renamed.unlink()
        with pytest.raises(reader.ReaderError, match="Source missing"):
            self.book.source()
        renamed.write_bytes(original)
        make_pdf(renamed, title="Same pages but different file")
        with pytest.raises(reader.ReaderError, match="Source changed"):
            self.book.source()
        assert self.book.state()["next_index"] == 0

    def test_source_is_rechecked_before_part_save(self) -> None:
        self.prepare()
        make_pdf(self.root / "book/source.pdf", title="Replaced")
        with pytest.raises(reader.ReaderError, match="Source changed"):
            self.book.save_part(self.part_payload(), 1)
        assert not ((self.root / "parts/C01-P01/part.md").exists())

    def test_source_replaced_while_opening_is_rejected(self) -> None:
        self.prepare()
        original = reader.pdf_reader

        def replace_during_open(path: Path):
            result = original(path)
            make_pdf(path, title="Changed during open")
            return result

        with patch.object(reader, "pdf_reader", side_effect=replace_during_open):
            with pytest.raises(reader.ReaderError, match="Source changed"):
                self.book.source()

    def test_multiple_unprepared_sources_need_selection(self) -> None:
        make_pdf(self.root / "book/other.pdf")
        with pytest.raises(reader.ReaderError, match="Several PDFs"):
            self.book.inspect()
        assert self.book.inspect("book/source.pdf")["source"]["page_count"] == 5

    def test_traversal_and_symlink_sources_are_rejected(self) -> None:
        with pytest.raises(reader.ReaderError):
            self.book.path("../another-book/state.json")
        with pytest.raises(reader.ReaderError):
            self.book.path(str(self.base / "outside"))
        outside = self.base / "outside.pdf"
        make_pdf(outside)
        (self.root / "book/source.pdf").unlink()
        (self.root / "book/source.pdf").symlink_to(outside)
        with pytest.raises(reader.ReaderError, match="Symlink"):
            self.book.inspect()

    def test_answer_stores_only_correct_answer_and_changes_one_status(self) -> None:
        self.save_first()
        self.book.save_answers(
            {
                "answers": [
                    {"id": "C01-P01-Q01", "correct_answer": "A point records a sampled location."}
                ]
            },
            2,
        )
        assert [q["id"] for q in self.book.pending_questions()] == ["C01-P01-Q02"]
        entries = self.book.answers()
        assert entries[0]["status"] == "answered"
        assert set(entries[0]) == {"id", "part_id", "question", "status", "correct_answer"}
        assert self.book.validate()["ok"]

    def test_submission_feedback_attempt_fields_are_rejected(self) -> None:
        self.save_first()
        for key in ["user_answer", "feedback", "attempts"]:
            entry = {"id": "C01-P01-Q01", "correct_answer": "Correct answer", key: "DO NOT SAVE"}
            with pytest.raises(reader.ReaderError):
                self.book.save_answers({"answers": [entry]}, 2)
        assert "DO NOT SAVE" not in (self.root / "answers.md").read_text()

    def test_no_wrong_question_or_duplicate_can_be_answered(self) -> None:
        self.save_first()
        for identifiers in [["C99-P01-Q01"], ["C01-P01-Q01", "C01-P01-Q01"]]:
            with pytest.raises(reader.ReaderError):
                self.book.save_answers(
                    {"answers": [{"id": q, "correct_answer": "Correct"} for q in identifiers]}, 2
                )
        assert len(self.book.pending_questions()) == 2

    def test_stale_revision_and_long_context_do_not_advance(self) -> None:
        self.prepare()
        with pytest.raises(reader.ReaderError):
            self.book.save_part(self.part_payload(), 0)
        payload = self.part_payload()
        payload["context"] = "word " * 401
        with pytest.raises(reader.ReaderError):
            self.book.save_part(payload, 1)
        assert self.book.state()["next_index"] == 0
        assert not ((self.root / "parts/C01-P01/part.md").exists())

    def test_exact_retry_is_idempotent_but_conflicting_rewrite_is_not(self) -> None:
        self.save_first()
        state = self.book.state()
        assert self.book.save_part(self.part_payload(), 1)["unchanged"]
        assert state == self.book.state()
        payload = self.part_payload()
        payload["body"] = "A conflicting replacement"
        with pytest.raises(reader.ReaderError):
            self.book.save_part(payload, 2)

    def test_transaction_recovers_after_interrupted_write(self) -> None:
        self.prepare()
        original = reader.atomic_write
        writes = 0

        def fail_once(path: Path, text: str) -> None:
            nonlocal writes
            writes += 1
            if writes == 3:
                raise OSError("simulated interruption")
            return original(path, text)

        with patch.object(reader, "atomic_write", side_effect=fail_once):
            with pytest.raises(OSError):
                self.book.save_part(self.part_payload(), 1)
        assert (self.root / reader.JOURNAL).exists()
        fresh = reader.Book(self.root)
        with fresh.locked():
            assert fresh.validate()["ok"]
            assert fresh.state()["last_part"] == "C01-P01"
        assert not ((self.root / reader.JOURNAL).exists())

    def test_lock_blocks_concurrent_operation(self) -> None:
        with self.book.locked():
            with pytest.raises(reader.ReaderError, match="Another reader"):
                with self.book.locked():
                    pass

    def test_lock_keeps_one_inode_and_releases_without_pid_cleanup(self) -> None:
        path = self.root / ".reader.lock"
        path.write_text("99999999")
        inode = path.stat().st_ino
        with self.book.locked():
            assert path.stat().st_ino == inode
        assert path.exists()
        with self.book.locked():
            assert path.stat().st_ino == inode

    def test_superseded_summary_pass_cannot_borrow_another_pass_receipts(self) -> None:
        self.save_first()
        old = self.book.start_summary()
        new = self.book.start_summary()
        assert old["session_id"] != new["session_id"]
        for index in range(new["batches"]):
            self.book.summary_batch(index, new["session_id"])
        with pytest.raises(reader.ReaderError, match="superseded"):
            self.book.summary_batch(0, old["session_id"])
        with pytest.raises(reader.ReaderError, match="superseded"):
            self.book.save_summary(
                {
                    "session_id": old["session_id"],
                    "body": "Summary",
                    "covered_part_ids": ["C01-P01"],
                },
                2,
            )

    def test_summary_needs_every_batch_and_restarts_each_time(self) -> None:
        self.save_first()
        manifest = self.book.start_summary(1000)
        payload = {"body": "Points record locations.", "covered_part_ids": ["C01-P01"]}
        with pytest.raises(reader.ReaderError):
            self.book.save_summary(
                dict(payload, session_id=self.book.summary_session()["session_id"]), 2
            )
        for index in range(manifest["batches"]):
            self.book.summary_batch(index, self.book.summary_session()["session_id"])
        self.book.save_summary(
            dict(payload, session_id=self.book.summary_session()["session_id"]), 2
        )
        first = (self.root / "summary.md").read_text()
        again = self.book.start_summary(1000)
        assert self.book.summary_session()["served"] == []
        for index in range(again["batches"]):
            self.book.summary_batch(index, self.book.summary_session()["session_id"])
        payload["body"] = "A clearer current summary."
        self.book.save_summary(
            dict(payload, session_id=self.book.summary_session()["session_id"]), 3
        )
        assert first != (self.root / "summary.md").read_text()
        assert len(list(self.root.glob("summary*.md"))) == 1
        assert self.book.state()["next_index"] == 1

    def test_summary_rejects_changed_or_added_parts_and_missing_coverage(self) -> None:
        self.save_first()
        manifest = self.book.start_summary()
        for index in range(manifest["batches"]):
            self.book.summary_batch(index, self.book.summary_session()["session_id"])
        with pytest.raises(reader.ReaderError, match="coverage"):
            self.book.save_summary(
                {
                    "session_id": self.book.summary_session()["session_id"],
                    "body": "Summary",
                    "covered_part_ids": [],
                },
                2,
            )
        self.book.save_part(self.part_payload("C02-P01"), 2)
        with pytest.raises(reader.ReaderError, match="changed"):
            self.book.save_summary(
                {
                    "session_id": self.book.summary_session()["session_id"],
                    "body": "Summary",
                    "covered_part_ids": ["C01-P01"],
                },
                3,
            )
        manifest = self.book.start_summary()
        (self.root / "parts/C01-P01/part.md").write_text(
            (self.root / "parts/C01-P01/part.md").read_text() + "\nEdited.\n"
        )
        with pytest.raises(reader.ReaderError, match="changed"):
            self.book.summary_batch(0, self.book.summary_session()["session_id"])

    def test_unexpected_part_file_is_not_silently_omitted(self) -> None:
        self.save_first()
        (self.root / "parts/extra.md").write_text("Unexpected saved lesson")
        with pytest.raises(reader.ReaderError, match="Unexpected lesson"):
            self.book.start_summary()

    def test_malformed_planned_part_is_not_accepted_as_summary_input(self) -> None:
        self.save_first()
        path = self.root / "parts/C01-P01/part.md"
        path.write_text(
            reader.machine_block("part", {"id": "C01-P01"})
            + "\nMissing provenance and questions.\n"
        )
        with pytest.raises(reader.ReaderError, match="Malformed part"):
            self.book.start_summary()

    def test_metadata_only_part_cannot_enter_summary(self) -> None:
        self.save_first()
        path = self.root / "parts/C01-P01/part.md"
        metadata = reader.machine_read(path.read_text(), "part")
        path.write_text(reader.machine_block("part", metadata))
        with pytest.raises(reader.ReaderError, match="Missing visible lesson"):
            self.book.start_summary()

    def test_questions_answers_summary_work_without_pdf(self) -> None:
        self.save_first()
        (self.root / "book/source.pdf").unlink()
        assert len(self.book.pending_questions()) == 2
        self.book.save_answers(
            {
                "answers": [
                    {"id": "C01-P01-Q01", "correct_answer": "A point is a sampled location."}
                ]
            },
            2,
        )
        manifest = self.book.start_summary()
        for index in range(manifest["batches"]):
            self.book.summary_batch(index, self.book.summary_session()["session_id"])
        self.book.save_summary(
            {
                "session_id": self.book.summary_session()["session_id"],
                "body": "Points are sampled locations.",
                "covered_part_ids": ["C01-P01"],
            },
            3,
        )
        assert self.book.validate()["ok"]

    def test_no_saved_parts_means_no_empty_summary(self) -> None:
        assert self.book.start_summary()["empty"]
        self.prepare()
        assert self.book.start_summary()["empty"]
        assert not ((self.root / "summary.md").exists())

    @pytest.mark.skipif(not (shutil.which("pandoc")), reason="Pandoc is optional")
    def test_renderer_has_colored_code_math_and_collapsed_answers(self) -> None:
        self.save_first()
        self.book.save_answers(
            {"answers": [{"id": "C01-P01-Q01", "correct_answer": "A sampled location."}]}, 2
        )
        self.book.render()
        output = (self.root / "reader.html").read_text()
        assert "<math" in output
        assert 'class="sourceCode python"' in output
        assert '<details class="answer">' in output
        assert "data-theme" in output
        assert "<span" in output
        assert "READERANSWER0PLACEHOLDER" not in output

    @pytest.mark.skipif(not shutil.which("pandoc"), reason="Pandoc is optional")
    def test_renderer_keeps_mermaid_source_unhighlighted_and_escaped(self) -> None:
        self.prepare()
        diagram = 'flowchart LR\n    A["x < 3 & y > 1"] --> B["Analysis and decisions"]'
        payload = self.part_payload()
        payload["body"] += f"\n\n```mermaid\n{diagram}\n```"
        self.book.save_part(payload, 1)
        self.book.render()
        output = (self.root / "reader.html").read_text()
        match = re.search(r'<pre class="mermaid">(.*?)</pre>', output, re.S)
        assert match is not None
        assert html.unescape(match.group(1)) == diagram
        assert "<" not in match.group(1)
        assert 'class="sourceCode python"' in output

    @pytest.mark.skipif(not shutil.which("pandoc"), reason="Pandoc is optional")
    def test_render_updates_one_pdf_with_contents_bookmarks_and_all_saved_material(self) -> None:
        from pypdf import PdfReader

        self.save_first()
        first = self.book.render()
        path = self.root / "reader.pdf"
        initial = path.read_bytes()
        assert first["pdf_path"] == "reader.pdf"
        self.book.save_part(self.part_payload("C02-P01"), 2)
        self.book.save_answers(
            {"answers": [{"id": "C01-P01-Q01", "correct_answer": "A sampled location."}]}, 3
        )
        session = self.book.start_summary()
        for index in range(session["batches"]):
            self.book.summary_batch(index, session["session_id"])
        self.book.save_summary(
            {
                "session_id": session["session_id"],
                "covered_part_ids": ["C01-P01", "C02-P01"],
                "body": "Cumulative explanation of sampled locations and surfaces.",
            },
            4,
        )
        (self.root / "book/source.pdf").unlink()
        state = (self.root / "state.json").read_bytes()
        self.book.render()
        assert path.read_bytes() != initial
        assert list(self.root.glob("reader*.pdf")) == [path]
        assert (self.root / "state.json").read_bytes() == state
        pdf = PdfReader(path)
        assert pdf.metadata is not None
        assert pdf.metadata.title == "A small test book"
        contents = pdf.pages[1].extract_text()
        assert "Contents" in contents
        assert "C01-P01" in contents and "C02-P01" in contents
        assert pdf.pages[1].get("/Annots")
        assert pdf.outline
        for entry in pdf.outline:
            if not isinstance(entry, list):
                page_number = pdf.get_destination_page_number(entry)
                assert page_number is not None
                assert str(page_number + 1) in contents
        text = "\n".join(page.extract_text() for page in pdf.pages)
        assert "Cumulative explanation" in text
        assert "A sampled location." in text
        assert "READERANSWER" not in text
        assert "x" in text and "points" in text

    @pytest.mark.skipif(not shutil.which("pandoc"), reason="Pandoc is optional")
    def test_pdf_failure_preserves_previous_pdf_and_reports_fresh_html(self) -> None:
        self.save_first()
        self.book.render()
        pdf = (self.root / "reader.pdf").read_bytes()
        state = (self.root / "state.json").read_bytes()
        with patch.object(self.book, "pdf_bytes", side_effect=reader.ReaderError("compile failed")):
            with pytest.raises(reader.ReaderError, match="HTML was saved, but PDF could not"):
                self.book.render()
        assert (self.root / "reader.pdf").read_bytes() == pdf
        assert (self.root / "state.json").read_bytes() == state
        assert (self.root / "reader.html").is_file()

    @pytest.mark.skipif(not (shutil.which("pandoc")), reason="Pandoc is optional")
    def test_renderer_rejects_remote_images(self) -> None:
        self.prepare()
        payload = self.part_payload()
        payload["body"] += "\n\n![Remote](https://example.com/image.png)"
        self.book.save_part(payload, 1)
        with pytest.raises(reader.ReaderError):
            self.book.render()

    @pytest.mark.skipif(not (shutil.which("pandoc")), reason="Pandoc is optional")
    def test_renderer_embeds_neighboring_part_figure(self) -> None:
        self.prepare()
        payload = self.part_payload()
        payload["body"] += "\n\n![Triangle](C01-P01-figure.svg)"
        self.book.save_part(payload, 1)
        (self.root / "parts/C01-P01/C01-P01-figure.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><circle cx="10" cy="10" r="5"/></svg>'
        )
        self.book.render()
        output = (self.root / "reader.html").read_text()
        assert 'src="data:image/svg+xml;base64,' in output

    @pytest.mark.skipif(not shutil.which("pandoc"), reason="Pandoc is optional")
    def test_each_part_keeps_its_own_markdown_and_same_named_assets(self) -> None:
        self.prepare()
        images = []
        for revision, identifier in enumerate(("C01-P01", "C02-P01"), 1):
            directory = self.root / "parts" / identifier
            directory.mkdir()
            source = (
                '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">'
                f'<text x="1" y="10">{identifier}</text></svg>'
            ).encode()
            (directory / "figure.svg").write_bytes(source)
            images.append(source)
            payload = self.part_payload(identifier)
            payload["body"] += "\n\n![Part figure](figure.svg)"
            result = self.book.save_part(payload, revision)
            assert result["path"] == f"parts/{identifier}/part.md"
            assert (directory / "part.md").is_file()
        assert not list((self.root / "parts").glob("*.md"))
        assert {item["path"] for item in self.book.saved_parts()} == {
            "parts/C01-P01/part.md",
            "parts/C02-P01/part.md",
        }
        assert self.book.pending_questions()[0]["part"] == "parts/C01-P01/part.md"
        assert "(parts/C02-P01/part.md)" in (self.root / "answers.md").read_text()
        self.book.render()
        output = (self.root / "reader.html").read_text()
        for source in images:
            assert base64.b64encode(source).decode() in output
        summary = self.book.start_summary()
        assert summary["part_ids"] == ["C01-P01", "C02-P01"]

    def test_summary_rejects_a_symlinked_part_directory(self) -> None:
        self.save_first()
        state = (self.root / "state.json").read_bytes()
        directory = self.root / "parts/C01-P01"
        outside = self.base / "outside-part"
        directory.rename(outside)
        directory.symlink_to(outside, target_is_directory=True)
        with pytest.raises(reader.ReaderError, match="Symlink"):
            self.book.start_summary()
        assert (self.root / "state.json").read_bytes() == state

    @pytest.mark.skipif(not (shutil.which("pandoc")), reason="Pandoc is optional")
    def test_renderer_handles_reference_images_without_parsing_code_as_figures(self) -> None:
        self.prepare()
        payload = self.part_payload()
        payload["body"] += (
            "\n\n![Triangle][shape]\n\n[shape]: C01-P01-figure.svg\n\n```markdown\n![Example](does-not-exist.svg)\n```"
        )
        self.book.save_part(payload, 1)
        (self.root / "parts/C01-P01/C01-P01-figure.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"/>'
        )
        self.book.render()
        output = (self.root / "reader.html").read_text()
        assert 'src="data:image/svg+xml;base64,' in output
        assert "does-not-exist.svg" in output
        assert "A small test book" in output

    @pytest.mark.skipif(not (shutil.which("pandoc")), reason="Pandoc is optional")
    def test_renderer_rejects_executable_hyperlink_schemes(self) -> None:
        self.prepare()
        payload = self.part_payload()
        payload["body"] += "\n\n[Unsafe link](javascript:alert%281%29)"
        self.book.save_part(payload, 1)
        with pytest.raises(reader.ReaderError, match="Unsupported hyperlink"):
            self.book.render()

    def test_cli_restores_state_in_new_process(self) -> None:
        self.save_first()
        command = [sys.executable, "reader.py", "context"]
        result = subprocess.run(command, cwd=self.root, text=True, capture_output=True, check=True)
        assert json.loads(result.stdout)["next_part"]["id"] == "C02-P01"

    def test_book_local_cli_prepares_from_an_ignored_input_file(self) -> None:
        payload = self.root / ".reader-prepare.json"
        payload.write_text(reader.json_text(self.payload()))
        result = subprocess.run(
            [sys.executable, "reader.py", "prepare", "--input", payload.name, "--revision", "0"],
            cwd=self.root,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        assert not payload.exists()
        assert self.book.context()["next_part"]["id"] == "C01-P01"

    def test_git_ignores_source_but_tracks_marker_parts_and_state(self) -> None:
        self.save_first()
        (self.root / "parts/C01-P01/.reader-tmp-test").write_text("Private staged content")
        (self.root / ".reader-part.json").write_text("{}")
        (self.root / "reader.pdf").write_bytes(b"%PDF-test")
        (self.root / "reader.html").write_text("<html></html>")
        subprocess.run(["git", "init", "-q", str(self.base)], check=True)
        subprocess.run(["git", "-C", str(self.base), "add", "test-book"], check=True)
        tracked = subprocess.run(
            ["git", "-C", str(self.base), "ls-files"], capture_output=True, text=True, check=True
        ).stdout
        assert "source.pdf" not in tracked
        assert "test-book/.reader-book" in tracked
        assert "test-book/state.json" in tracked
        assert "test-book/.reader-template.html" in tracked
        assert ".reader-assets" not in tracked
        assert "test-book/reader.pdf" in tracked
        assert "test-book/reader.html" in tracked
        assert "test-book/parts/C01-P01/part.md" in tracked
        assert ".reader-tmp-test" not in tracked
        assert ".reader-part.json" not in tracked

    def test_repository_ignore_rules_keep_required_book_templates(self) -> None:
        repository = self.base / "repository"
        repository.mkdir()
        shutil.copy2(ROOT / ".gitignore", repository / ".gitignore")
        shutil.copytree(ROOT / "templates", repository / "templates")
        (repository / "books/a/book").mkdir(parents=True)
        (repository / "books/a/book/source.pdf").write_bytes(b"original")
        (repository / "books/a/reader.pdf").write_bytes(b"%PDF-test")
        (repository / "books/a/reader.html").write_text("<html></html>")
        (repository / "books/a/state.json").write_text("{}")
        (repository / "books/a/parts/C01-P01").mkdir(parents=True)
        (repository / "books/a/parts/C01-P01/part.md").write_text("Private lesson")
        (repository / "books/a/parts/C01-P01/figure.png").write_bytes(b"private figure")
        (repository / "examples").mkdir()
        (repository / "examples/reader.html").write_text("Synthetic public example")
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
        tracked = subprocess.run(
            ["git", "-C", str(repository), "ls-files"], capture_output=True, text=True, check=True
        ).stdout
        assert "templates/book/AGENTS.md" in tracked
        assert "templates/book/.agents/skills/reader-next/SKILL.md" in tracked
        assert "source.pdf" not in tracked
        assert "books/" not in tracked
        assert "examples/reader.html" in tracked

    def test_checkpoint_skips_ignored_book_without_staging_private_files(self) -> None:
        repository = self.base / "repository"
        repository.mkdir()
        shutil.copy2(ROOT / ".gitignore", repository / ".gitignore")
        book = repository / "books/my-book"
        setup.new_book(book)
        subprocess.run(["git", "init", "-q", str(repository)], check=True)

        assert "ignored" in setup.checkpoint(book)
        assert not subprocess.check_output(["git", "-C", str(repository), "ls-files"])
        assert (
            subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "--verify", "HEAD"],
                capture_output=True,
            ).returncode
            != 0
        )

    def test_checkpoint_supports_a_separate_private_book_repository(self) -> None:
        self.save_first()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        for key, value in (
            ("user.name", "Test reader"),
            ("user.email", "reader@example.invalid"),
            ("commit.gpgsign", "false"),
        ):
            subprocess.run(["git", "-C", str(self.root), "config", key, value], check=True)
        (self.root / "reader.html").write_text("<html></html>")
        (self.root / "reader.pdf").write_bytes(b"%PDF-test")

        assert "Saved a local Git checkpoint" in setup.checkpoint(self.root)
        tracked = subprocess.check_output(
            ["git", "-C", str(self.root), "ls-files"], text=True
        ).splitlines()
        assert "state.json" in tracked
        assert "parts/C01-P01/part.md" in tracked
        assert "reader.html" in tracked
        assert "reader.pdf" in tracked
        assert "book/source.pdf" not in tracked
        assert "No new book changes" in setup.checkpoint(self.root)

    def test_checkpoint_refuses_tracked_source_in_a_separate_book_repository(self) -> None:
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("")
        subprocess.run(["git", "-C", str(self.root), "add", "book/source.pdf"], check=True)

        with pytest.raises(ValueError, match="original source is already tracked"):
            setup.checkpoint(self.root)
        assert subprocess.check_output(
            ["git", "-C", str(self.root), "ls-files"], text=True
        ).splitlines() == ["book/source.pdf"]

    def test_checkpoint_refuses_when_ignore_rules_cannot_be_verified(self) -> None:
        root = subprocess.CompletedProcess([], 0, str(self.base), "")
        failure = subprocess.CompletedProcess([], 128)
        with patch.object(setup.subprocess, "run", side_effect=[root, failure]) as run:
            with pytest.raises(ValueError, match="Cannot verify Git ignore rules"):
                setup.checkpoint(self.root)
        assert run.call_count == 2

    def test_strict_launcher_fails_closed_and_never_starts_codex_on_failed_probe(self) -> None:
        failed = subprocess.CompletedProcess([], 1, "", "sandbox unavailable")
        with (
            patch.object(setup, "reject_conflicting_settings"),
            patch.object(setup, "pandoc_runtime", return_value=("", [])),
            patch.object(setup.subprocess, "run", return_value=failed) as run,
        ):
            with pytest.raises(ValueError, match="Codex was not started"):
                setup.launch(self.root)
            assert run.call_count == 1
            assert run.call_args is not None
            assert "sandbox" in run.call_args.args[0]

    def test_conflicting_sandbox_settings_cannot_bypass_interactive_profile(self) -> None:
        config = self.base / "config.toml"
        config.write_text('sandbox_mode = "workspace-write"\n')
        with pytest.raises(ValueError, match="override the reading boundary"):
            setup.reject_conflicting_settings(self.root, [config])
        config.write_text('model = "example"\n')
        setup.reject_conflicting_settings(self.root, [config])

    def test_profile_has_exact_book_boundary_and_no_conflicting_sandbox_flag(self) -> None:
        args = setup.profile_overrides(self.root)
        text = " ".join(args)
        assert '":root"="deny"' in text
        assert str(self.root) in text
        settings = tomllib.loads("\n".join(args[1::2]))
        assert settings["project_root_markers"] == [reader.BOOK_MARKER]
        assert (
            settings["permissions"]["reader"]["filesystem"][str(self.root / reader.BOOK_MARKER)]
            == "read"
        )
        assert (
            settings["permissions"]["reader"]["filesystem"][str(setup.pdf_runtime_path())] == "read"
        )
        assert "sandbox_mode" not in text

    def test_profile_allows_venv_and_uv_interpreter_aliases_without_parent_access(self) -> None:
        installation = self.base / "uv/python/cpython-3.13.14"
        (installation / "bin").mkdir(parents=True)
        executable = installation / "bin/python3.13"
        executable.touch()
        alias = installation.with_name("cpython-3.13")
        alias.symlink_to(installation, target_is_directory=True)
        environment = self.base / "project/.venv"
        (environment / "bin").mkdir(parents=True)
        invoked = environment / "bin/python3"
        invoked.symlink_to(alias / "bin/python3.13")
        # Model a framework prefix that does not contain the actual executable.
        base_prefix = alias / "Python.framework/Versions/3.13"
        base_prefix.mkdir(parents=True)
        with (
            patch.object(sys, "prefix", str(environment)),
            patch.object(sys, "exec_prefix", str(environment)),
            patch.object(sys, "base_prefix", str(base_prefix)),
            patch.object(sys, "base_exec_prefix", str(base_prefix)),
            patch.object(sys, "executable", str(invoked)),
            patch.object(sys, "_base_executable", str(alias / "bin/python3.13")),
        ):
            args = setup.profile_overrides(self.root)
        table = next(value for value in args if value.startswith("permissions.reader.filesystem="))
        filesystem = tomllib.loads(table)["permissions"]["reader"]["filesystem"]
        for path in (
            environment,
            invoked,
            executable,
            base_prefix,
            base_prefix.resolve(),
            alias / "bin/python3.13",
        ):
            assert filesystem[str(path)] == "read"
        assert filesystem[str(self.root)] == "write"
        assert filesystem[str(self.root / "book")] == "read"
        assert filesystem[":root"] == "deny"
        for parent in (
            self.base,
            environment.parent,
            installation.parent,
            Path.home(),
            setup.REPOSITORY,
        ):
            assert str(parent) not in filesystem

    def test_profile_does_not_allow_a_broad_python_prefix(self) -> None:
        with (
            patch.object(sys, "prefix", str(setup.REPOSITORY)),
            patch.object(sys, "exec_prefix", str(Path.home())),
            patch.object(sys, "base_prefix", "/"),
            patch.object(sys, "base_exec_prefix", "/"),
        ):
            args = setup.profile_overrides(self.root)
        table = next(value for value in args if value.startswith("permissions.reader.filesystem="))
        filesystem = tomllib.loads(table)["permissions"]["reader"]["filesystem"]
        for path in ("/", str(Path.home().resolve()), str(setup.REPOSITORY)):
            assert path not in filesystem

    def test_probe_executes_the_binary_beyond_the_uv_version_alias(self) -> None:
        installation = self.base / "uv/python/cpython-3.13.14"
        (installation / "bin").mkdir(parents=True)
        binary = installation / "bin/python3.13"
        binary.touch()
        alias = installation.with_name("cpython-3.13")
        alias.symlink_to(installation, target_is_directory=True)
        environment = self.base / "project/.venv"
        (environment / "bin").mkdir(parents=True)
        invoked = environment / "bin/python3"
        invoked.symlink_to(alias / "bin/python3.13")
        with patch.object(sys, "executable", str(invoked)):
            command = setup.probe_command(self.root, self.base / "outside")
        assert command[0] == str(binary)

    def test_probe_and_tools_keep_only_the_launchers_package_paths(self) -> None:
        packages = {
            "purelib": str(self.base / "venv/lib/site-packages"),
            "platlib": str(self.base / "venv/lib/platform-packages"),
        }
        with (
            patch.object(setup.sysconfig, "get_path", side_effect=packages.__getitem__),
            patch.dict(os.environ, {"PYTHONPATH": "/unrelated/packages"}),
        ):
            environment = setup.reader_environment()
            overrides = setup.profile_overrides(self.root)
        assert environment["PYTHONPATH"] == os.pathsep.join(packages.values())
        assert environment["PYTHONNOUSERSITE"] == "1"
        settings = tomllib.loads("\n".join(overrides[1::2]))
        assert settings["shell_environment_policy"]["set"] == {
            "PYTHONPATH": environment["PYTHONPATH"],
            "PYTHONNOUSERSITE": "1",
            "READER_PANDOC": environment["READER_PANDOC"],
            "READER_PANDOC_LIBRARY_PATH": environment["READER_PANDOC_LIBRARY_PATH"],
            "READER_PDF_RUNTIME": environment["READER_PDF_RUNTIME"],
        }
        for path in packages.values():
            assert settings["permissions"]["reader"]["filesystem"][path] == "read"

    def test_pandoc_runtime_resolves_homebrew_binary_and_transitive_libraries(self) -> None:
        installation = self.base / "homebrew"
        binary = installation / "Cellar/pandoc/3/bin/pandoc"
        gmp = installation / "Cellar/gmp/6/lib/libgmp.dylib"
        helper = installation / "Cellar/helper/1/lib/libhelper.dylib"
        for path in (binary, gmp, helper):
            path.parent.mkdir(parents=True)
            path.touch()
        invoked = installation / "bin/pandoc"
        invoked.parent.mkdir()
        invoked.symlink_to(binary)
        alias = installation / "opt/gmp"
        alias.parent.mkdir()
        alias.symlink_to(gmp.parent.parent, target_is_directory=True)
        outputs = [
            f"{binary}:\n\t/usr/lib/libSystem.B.dylib (compatibility version 1)\n"
            f"\t{alias / 'lib/libgmp.dylib'} (compatibility version 1)\n",
            f"{gmp}:\n\t{helper} (compatibility version 1)\n",
            f"{helper}:\n\t{gmp} (compatibility version 1)\n",
        ]
        with (
            patch.object(setup.shutil, "which", return_value=str(invoked)),
            patch.object(sys, "platform", "darwin"),
            patch.object(
                setup.subprocess,
                "run",
                side_effect=[subprocess.CompletedProcess([], 0, output, "") for output in outputs],
            ),
        ):
            pandoc, libraries = setup.pandoc_runtime()
        assert pandoc == str(binary)
        assert set(libraries) == {gmp, helper}
        with patch.object(setup, "pandoc_runtime", return_value=(pandoc, libraries)):
            args = setup.profile_overrides(self.root)
            environment = setup.reader_environment()
        settings = tomllib.loads("\n".join(args[1::2]))
        filesystem = settings["permissions"]["reader"]["filesystem"]
        for path in (binary, gmp, helper):
            assert filesystem[str(path)] == "read"
        assert str(installation) not in filesystem
        assert str(installation / "Cellar") not in filesystem
        assert str(installation / "opt") not in filesystem
        assert str(alias) not in filesystem
        assert environment["READER_PANDOC"] == str(binary)
        assert set(environment["READER_PANDOC_LIBRARY_PATH"].split(os.pathsep)) == {
            str(gmp.parent),
            str(helper.parent),
        }
        with patch.dict(os.environ, environment):
            assert reader.pandoc_command() == str(binary)
            assert (
                reader.pandoc_environment()["DYLD_LIBRARY_PATH"]
                == environment["READER_PANDOC_LIBRARY_PATH"]
            )

    def test_pandoc_remains_optional_when_not_installed(self) -> None:
        with patch.object(setup.shutil, "which", return_value=None):
            assert setup.pandoc_runtime() == ("", [])
            environment = setup.reader_environment()
        assert environment["READER_PANDOC"] == ""
        assert environment["READER_PANDOC_LIBRARY_PATH"] == ""

    def test_pandoc_probe_failure_prevents_interactive_launch(self) -> None:
        failed = subprocess.CompletedProcess([], 1, "", "Pandoc could not parse the Markdown")
        with (
            patch.object(setup, "reject_conflicting_settings"),
            patch.object(setup, "pandoc_runtime", return_value=("/runtime/pandoc", [])),
            patch.object(setup.subprocess, "run", return_value=failed) as run,
        ):
            with pytest.raises(ValueError, match="Pandoc could not parse"):
                setup.launch(self.root)
        run.assert_called_once()

    def test_verify_only_uses_runtime_environment_and_does_not_launch_or_checkpoint(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "reader isolation verified\n", "")
        with (
            patch.object(setup, "reject_conflicting_settings"),
            patch.object(setup, "pandoc_runtime", return_value=("", [])),
            patch.object(setup, "checkpoint") as checkpoint,
            patch.object(setup.subprocess, "run", return_value=completed) as run,
        ):
            environment = setup.reader_environment()
            assert setup.launch(self.root, verify_only=True) == 0
        checkpoint.assert_not_called()
        run.assert_called_once()
        assert run.call_args is not None
        assert run.call_args.kwargs["env"] == environment
        assert "import pypdf" in run.call_args.args[0][-3]

    @pytest.mark.skipif(
        sys.platform != "darwin" or os.environ.get("READER_TEST_MACOS_SANDBOX") != "1",
        reason="Requires opt-in execution outside an enclosing sandbox on macOS",
    )
    def test_macos_real_sandbox_runs_the_book_local_runtime(self) -> None:
        # Only generated fixture data is used. No actual book is opened.
        state = (self.root / "state.json").read_bytes()
        setup.verify_isolation(self.root)
        result = subprocess.run(
            [
                "codex",
                "sandbox",
                *setup.profile_overrides(self.root),
                "-P",
                "reader",
                "-C",
                str(self.root),
                setup.reader_python(),
                "reader.py",
                "--help",
            ],
            env=setup.reader_environment(),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "usage:" in result.stdout
        assert (self.root / "state.json").read_bytes() == state

    @pytest.mark.skipif(
        sys.platform != "darwin"
        or os.environ.get("READER_TEST_MACOS_SANDBOX") != "1"
        or not shutil.which("pandoc"),
        reason="Requires Pandoc and opt-in macOS sandbox execution",
    )
    def test_macos_real_sandbox_renders_html_without_changing_reading_data(self) -> None:
        from pypdf import PdfReader

        self.prepare()
        payload = self.part_payload()
        payload["body"] += '\n\n```mermaid\nflowchart LR\n    A["Input"] --> B["Output"]\n```'
        self.book.save_part(payload, 1)
        self.book.save_answers(
            {"answers": [{"id": "C01-P01-Q01", "correct_answer": "A sampled location."}]}, 2
        )
        (self.root / "book/source.pdf").unlink()
        names = ("state.json", "plan.md", "answers.md", "parts/C01-P01/part.md")
        before = {name: (self.root / name).read_bytes() for name in names}
        setup.verify_isolation(self.root)
        result = subprocess.run(
            [
                "codex",
                "sandbox",
                *setup.profile_overrides(self.root),
                "-P",
                "reader",
                "-C",
                str(self.root),
                setup.reader_python(),
                "reader.py",
                "render",
            ],
            env=setup.reader_environment(),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["path"] == "reader.html"
        output = (self.root / "reader.html").read_text()
        assert "<math" in output
        assert 'class="sourceCode python"' in output
        assert '<details class="answer">' in output
        assert '<pre class="mermaid">' in output
        assert 'class="sourceCode mermaid"' not in output
        assert json.loads(result.stdout)["pdf_path"] == "reader.pdf"
        pdf = PdfReader(self.root / "reader.pdf")
        text = "\n".join(page.extract_text() for page in pdf.pages)
        assert "Contents" in text and "Input" in text and "Output" in text
        assert "flowchart LR" not in text
        assert pdf.outline
        assert before == {name: (self.root / name).read_bytes() for name in names}

    def test_new_with_source_reports_the_copied_pdf_and_matching_open_path(self) -> None:
        source = self.base / "input.pdf"
        make_pdf(source)
        destination = self.base / "another-book"
        with (
            patch.object(
                sys,
                "argv",
                ["book_reader.py", "new", str(destination), "--source", str(source)],
            ),
            patch("builtins.print") as output,
        ):
            assert setup.main() == 0
        messages = "\n".join(str(call.args[0]) for call in output.call_args_list)
        assert "Copied the source PDF" in messages
        assert f"open {destination}" in messages
        assert "Put the source PDF" not in messages
        assert (destination / "book/input.pdf").read_bytes() == source.read_bytes()

    def test_skill_frontmatter_and_command_contract(self) -> None:
        skills = list((self.root / ".agents/skills").glob("*/SKILL.md"))
        for skill in skills:
            text = skill.read_text()
            assert re.search(r"\A---\nname: [a-z0-9-]+\ndescription: .+\n---\n", text)
            assert len(text.splitlines()) < 500
        assert "simple English" in (self.root / "AGENTS.md").read_text()
        assert "`questions`" in (self.root / "AGENTS.md").read_text()

    def test_refresh_preserves_source_and_saved_reading_data(self) -> None:
        self.save_first()
        obsolete = self.root / ".reader-assets"
        obsolete.mkdir()
        (obsolete / "LICENSE").write_text("Generated vendor file")
        custom = self.root / ".agents/skills/custom/SKILL.md"
        custom.parent.mkdir()
        custom.write_text("Custom skill")
        (self.root / ".gitignore").write_text(
            "/custom-cache/\n!/.reader-assets/\n!/.reader-assets/**\n/reader.html\n/reader.pdf\n"
        )
        paths = ["state.json", "plan.md", "answers.md", "parts/C01-P01/part.md", "book/source.pdf"]
        before = {name: (self.root / name).read_bytes() for name in paths}
        setup.refresh_book(self.root)
        assert before == {name: (self.root / name).read_bytes() for name in paths}
        assert (self.root / "reader.py").read_bytes() == (ROOT / "runtime/reader.py").read_bytes()
        assert (self.root / ".reader-book").is_file()
        assert custom.read_text() == "Custom skill"
        assert not obsolete.exists()
        for action in ("prepare", "next", "answer", "summary"):
            assert (self.root / f".agents/skills/reader-{action}/SKILL.md").is_file()
        assert "/custom-cache/" in (self.root / ".gitignore").read_text()
        assert "!/.reader-assets" not in (self.root / ".gitignore").read_text()
        assert "/reader.html" not in (self.root / ".gitignore").read_text()
        assert "/reader.pdf" not in (self.root / ".gitignore").read_text()
        assert self.book.validate()["ok"]

    def test_launch_supplies_the_uv_environment_interpreter(self) -> None:
        completed = subprocess.CompletedProcess([], 0)
        with (
            patch.object(setup, "reject_conflicting_settings"),
            patch.object(setup, "verify_isolation"),
            patch.object(setup, "pandoc_runtime", return_value=("", [])),
            patch.object(setup, "checkpoint", return_value="Saved"),
            patch.object(setup.subprocess, "run", return_value=completed) as run,
        ):
            assert setup.launch(self.root) == 0
            call = run.call_args
            assert call is not None
            assert setup.reader_python() in call.args[0][-1]
            assert call.kwargs["env"] == setup.reader_environment()
            assert call.kwargs["cwd"] == self.root
