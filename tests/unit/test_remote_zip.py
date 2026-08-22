"""Tests for the range-request ZIP reader.

No network: a real ZIP is built in memory and the transport is replaced with slicing,
so the ZIP *parsing* — which is where the bugs were — is exercised exactly as it would
be over HTTP.

Both real bugs found while building this are pinned here:
  * a central-directory struct that was 4 bytes off, silently misreading every field
  * nested archives, which is how RDD2022 is actually laid out
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ml" / "datasets"))

from remote_zip import RemoteZip, RemoteZipError  # noqa: E402


class LocalZip(RemoteZip):
    """A RemoteZip whose transport reads from a bytes buffer instead of HTTP."""

    def __init__(self, blob: bytes, *, window: tuple[int, int] | None = None) -> None:
        super().__init__("https://example.invalid/test.zip", window=window)
        self._blob = blob
        if window is None:
            self._size = len(blob)

    def _fetch(self, start: int, end: int) -> bytes:  # type: ignore[override]
        if self._window is not None:
            base, length = self._window
            if start < 0 or end >= length:
                raise RemoteZipError("range outside nested window")
            start += base
            end += base
        return self._blob[start : end + 1]

    def open_nested(self, entry):  # type: ignore[override]
        resolved = self._resolve(entry)
        if resolved.compress_type != 0:
            raise RemoteZipError("nested access requires a stored member")
        base = self.data_offset(resolved)
        if self._window is not None:
            base += self._window[0]
        return LocalZip(self._blob, window=(base, resolved.compressed_size))


def build_zip(files: dict[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


@pytest.fixture
def simple() -> bytes:
    return build_zip(
        {
            "a.txt": b"hello world",
            "dir/b.txt": b"second file" * 100,
            "dir/c.bin": bytes(range(256)) * 10,
        }
    )


class TestIndex:
    def test_lists_all_entries(self, simple: bytes):
        assert set(LocalZip(simple).namelist()) == {"a.txt", "dir/b.txt", "dir/c.bin"}

    def test_entry_sizes_are_correct(self, simple: bytes):
        """The bug that started this: a 4-byte struct offset error made every field
        wrong, which surfaced as 'listed 7 entries but 1 were parsed'."""
        entries = {e.name: e for e in LocalZip(simple).entries()}
        assert entries["a.txt"].uncompressed_size == 11
        assert entries["dir/b.txt"].uncompressed_size == len(b"second file" * 100)
        assert entries["dir/c.bin"].uncompressed_size == 2560

    def test_rejects_non_zip(self):
        with pytest.raises(RemoteZipError, match="not a ZIP"):
            LocalZip(b"definitely not a zip file" * 100).namelist()

    def test_empty_archive(self):
        assert LocalZip(build_zip({})).namelist() == []


class TestReading:
    def test_reads_deflated_member(self, simple: bytes):
        assert LocalZip(simple).read("a.txt") == b"hello world"

    def test_reads_large_member(self, simple: bytes):
        assert LocalZip(simple).read("dir/b.txt") == b"second file" * 100

    def test_reads_binary_member(self, simple: bytes):
        assert LocalZip(simple).read("dir/c.bin") == bytes(range(256)) * 10

    def test_reads_stored_member(self):
        blob = build_zip({"x.txt": b"uncompressed"}, compression=zipfile.ZIP_STORED)
        assert LocalZip(blob).read("x.txt") == b"uncompressed"

    def test_unknown_member(self, simple: bytes):
        with pytest.raises(KeyError):
            LocalZip(simple).read("nope.txt")

    def test_empty_member(self):
        assert LocalZip(build_zip({"empty.txt": b""})).read("empty.txt") == b""

    def test_matches_stdlib_exactly(self, simple: bytes):
        """Strongest check available: agree with zipfile on every member."""
        reference = zipfile.ZipFile(io.BytesIO(simple))
        remote = LocalZip(simple)
        for name in reference.namelist():
            assert remote.read(name) == reference.read(name), name


class TestFiltering:
    def test_prefix_and_suffix(self, simple: bytes):
        z = LocalZip(simple)
        assert [e.name for e in z.iter_matching(prefix="dir/")] == ["dir/b.txt", "dir/c.bin"]
        assert [e.name for e in z.iter_matching(suffix=".bin")] == ["dir/c.bin"]

    def test_skips_directory_entries(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("folder/", b"")
            zf.writestr("folder/file.txt", b"data")
        names = [e.name for e in LocalZip(buffer.getvalue()).iter_matching()]
        assert names == ["folder/file.txt"]


class TestBulkReading:
    def test_read_many_matches_individual_reads(self, simple: bytes):
        """Bulk fetching must be an optimisation, never a behaviour change."""
        z = LocalZip(simple)
        bulk = {e.name: data for e, data in z.read_many(z.entries())}
        assert bulk == {name: z.read(name) for name in z.namelist()}

    def test_read_many_empty(self, simple: bytes):
        assert list(LocalZip(simple).read_many([])) == []

    def test_read_many_subset(self, simple: bytes):
        z = LocalZip(simple)
        wanted = [e for e in z.entries() if e.name.endswith(".txt")]
        got = dict(z.read_many(wanted))
        assert {e.name for e in got} == {"a.txt", "dir/b.txt"}


class TestNestedArchives:
    """RDD2022 is a ZIP of per-country ZIPs, stored uncompressed."""

    @pytest.fixture
    def nested(self) -> bytes:
        inner = build_zip(
            {"Czech/train/img_0.jpg": b"\xff\xd8\xffJPEGDATA", "Czech/x.xml": b"<a/>"}
        )
        # Stored, exactly as RDD2022 does — already-compressed data does not deflate.
        return build_zip({"RDD2022/Czech.zip": inner}, compression=zipfile.ZIP_STORED)

    def test_opens_inner_archive_without_extracting(self, nested: bytes):
        outer = LocalZip(nested)
        inner = outer.open_nested("RDD2022/Czech.zip")
        assert set(inner.namelist()) == {"Czech/train/img_0.jpg", "Czech/x.xml"}

    def test_reads_through_two_levels(self, nested: bytes):
        inner = LocalZip(nested).open_nested("RDD2022/Czech.zip")
        assert inner.read("Czech/train/img_0.jpg") == b"\xff\xd8\xffJPEGDATA"
        assert inner.read("Czech/x.xml") == b"<a/>"

    def test_nested_bulk_read(self, nested: bytes):
        inner = LocalZip(nested).open_nested("RDD2022/Czech.zip")
        got = dict(inner.read_many(inner.entries()))
        assert len(got) == 2

    def test_refuses_compressed_nested_archive(self):
        """A deflated inner archive cannot be range-read; failing loudly beats
        silently downloading gigabytes."""
        inner = build_zip({"a.txt": b"x" * 5000})
        blob = build_zip({"inner.zip": inner}, compression=zipfile.ZIP_DEFLATED)
        with pytest.raises(RemoteZipError, match="stored member"):
            LocalZip(blob).open_nested("inner.zip")

    def test_window_bounds_are_enforced(self, nested: bytes):
        inner = LocalZip(nested).open_nested("RDD2022/Czech.zip")
        with pytest.raises(RemoteZipError, match="nested"):
            inner._fetch(0, inner.size + 10)
