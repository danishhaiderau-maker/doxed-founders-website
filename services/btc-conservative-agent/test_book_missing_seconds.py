"""missing_book_seconds: seconds without a book row between an exit trigger and its pricing (HM 18 Oct)."""
import json

import paper_fill


def _tail(tmp_path, seconds):
    p = tmp_path / "book.jsonl"
    p.write_text("".join(json.dumps({"s": s, "b": [[1, 1]], "a": [[2, 1]]}) + "\n" for s in seconds))
    return paper_fill.BookTail(str(p))


def test_missing_seconds_counts_the_gap(tmp_path):
    t = _tail(tmp_path, [100, 101, 102, 107, 108])  # 103-106 absent
    assert t.missing_seconds(100.5, 108.9) == 4
    assert t.missing_seconds(100, 102) == 0
    assert t.missing_seconds(109, 105) == 0


def test_missing_seconds_unavailable_source_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_fill, "book10l_at", lambda t: (_ for _ in ()).throw(RuntimeError("no book")))
    assert paper_fill.book10l_missing_seconds(1.0, 2.0) is None
