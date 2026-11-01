from pathlib import Path
from types import SimpleNamespace

import pytest

from localmedia.lightroom import CatalogPair, lock_belongs_to_process, switch_catalog


@pytest.mark.parametrize("content,expected", [
    ("C:\\Apps\\Lightroom.exe\r\n123\r\n", True),
    ("\ufeffc:/apps/Lightroom.exe\n123\n", True),
    ("C:\\Apps\\Lightroom.exe\n124\n", False),
    ("C:\\Other\\Lightroom.exe\n123\n", False),
    ("C:\\Apps\\Lightroom.exe\nnot-a-pid\n", False),
    ("C:\\Apps\\Lightroom.exe\n123\nextra", False),
    ("123", False),
])
def test_lock_requires_matching_executable_and_pid(content, expected):
    assert lock_belongs_to_process(content, r"C:\Apps\Lightroom.exe", 123) is expected


class Session:
    pair = SimpleNamespace(normal=Path("A.lrcat"), alternate=Path("B.lrcat"))

    def __init__(self, current=Path("B.lrcat"), failure=None):
        self.active, self.failure, self.events = current, failure, []

    def current(self):
        if self.failure == "unknown":
            raise RuntimeError("unknown catalog")
        return self.active

    def close(self, catalog):
        self.events.append(("close", catalog))
        if self.failure == "close":
            raise RuntimeError("backup dialog")
        self.active = None

    def open(self, catalog):
        self.events.append(("open", catalog))
        if self.failure == "open":
            raise RuntimeError("startup failed")
        self.active = catalog


def test_close_and_verify_precede_open():
    session = Session()
    result = switch_catalog(session, session.pair.normal,
                            lambda: session.events.append("verify"))
    assert result["ok"] and not result["already_open"]
    assert session.events == [("close", session.pair.alternate), "verify", ("open", session.pair.normal)]


def test_already_active_is_noop():
    session = Session()
    assert switch_catalog(session, session.pair.alternate)["already_open"]
    assert session.events == []


@pytest.mark.parametrize("failure,events", [("unknown", []), ("close", [("close", Path("B.lrcat"))])])
def test_failed_identity_or_close_never_opens_catalog(failure, events):
    session = Session(failure=failure)
    with pytest.raises(RuntimeError):
        switch_catalog(session, session.pair.normal)
    assert session.events == events


def test_integrity_failure_does_not_open_or_reopen():
    session = Session()

    def fail():
        raise ValueError("integrity mismatch")

    with pytest.raises(ValueError, match="integrity"):
        switch_catalog(session, session.pair.normal, fail)
    assert session.events == [("close", session.pair.alternate)]


def test_unknown_destination_is_rejected_before_close():
    session = Session()
    with pytest.raises(ValueError):
        switch_catalog(session, Path("other.lrcat"))
    assert session.events == []


def test_startup_failure_is_not_reported_as_success():
    session = Session(current=None, failure="open")
    with pytest.raises(RuntimeError, match="startup failed"):
        switch_catalog(session, session.pair.normal)
    assert session.active is None


def test_catalog_identity_uses_full_path_not_display_name(tmp_path):
    executable = tmp_path / "Lightroom.exe"
    executable.touch()
    first, second = tmp_path / "a" / "Library.lrcat", tmp_path / "b" / "Library.lrcat"
    for path in (first, second):
        path.parent.mkdir()
        path.touch()
    CatalogPair(executable, first, second).validate()
    with pytest.raises(ValueError, match="不同路径"):
        CatalogPair(executable, first, first).validate()
