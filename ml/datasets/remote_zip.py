"""Read selected files out of a remote ZIP archive using HTTP range requests.

RDD2022 is published as a single **13.3 GB** archive. Downloading it whole needs
~27 GB with extraction, which many development machines do not have spare — and you
usually want one country, not six.

A ZIP's index (the "central directory") lives at the *end* of the file, so with a
server that supports range requests you can:

1. fetch the last few KB to find the index,
2. fetch the index (a few MB) to list every entry and its byte offset,
3. fetch only the entries you want, and inflate them locally.

Figshare supports ranges (verified: HTTP 206), so pulling the ~6,400-file Czech subset
costs a few hundred MB instead of 13.3 GB.

**Zip64 is mandatory here, not optional.** The classic ZIP header stores offsets in 32
bits, which caps at 4 GB. A 13.3 GB archive therefore *must* use Zip64 extensions, and
a reader that ignores them silently reads garbage offsets. That is handled below.

No third-party dependencies: ``urllib`` and ``zlib`` from the standard library.
"""

from __future__ import annotations

import struct
import time
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from urllib.request import Request, urlopen

# ZIP structure signatures.
_EOCD_SIG = b"PK\x05\x06"  # End of central directory
_EOCD64_LOCATOR_SIG = b"PK\x06\x07"  # Zip64 EOCD locator
_EOCD64_SIG = b"PK\x06\x06"  # Zip64 EOCD
_CENTRAL_SIG = b"PK\x01\x02"  # Central directory file header
_LOCAL_SIG = b"PK\x03\x04"  # Local file header

#: 32-bit sentinels meaning "the real value is in the Zip64 extra field".
_U32_MAX = 0xFFFFFFFF
_U16_MAX = 0xFFFF

_USER_AGENT = (
    "RoadEye-dataset-fetcher/0.1 (+https://github.com/AvagHovhannisyann/aispeedradararmenia)"
)

#: Transport retries. A multi-GB ingest is hundreds of requests; resets are routine.
_MAX_RETRIES = 5
_RETRY_BACKOFF_S = 1.0

#: When bulk-reading, entries separated by less than this are fetched in one request.
#: Tuned to trade a little wasted bandwidth for far fewer round trips — a per-file
#: request pattern is both slow and the thing that provokes connection resets.
_BULK_GAP_BYTES = 4 * 1024 * 1024

#: Slack added when guessing where a member's data ends, to cover the local header's
#: name and extra fields. Anything larger falls back to an individual fetch.
_LOCAL_HEADER_SLACK = 4096


class RemoteZipError(RuntimeError):
    """The remote archive could not be read."""


@dataclass(frozen=True, slots=True)
class ZipEntry:
    """One file inside the archive, with enough info to fetch it alone."""

    name: str
    compress_type: int
    compressed_size: int
    uncompressed_size: int
    header_offset: int
    crc: int

    @property
    def is_dir(self) -> bool:
        return self.name.endswith("/")


class RemoteZip:
    """Random access to a ZIP over HTTP.

    The index is fetched once on first use and cached, so listing is cheap and each
    subsequent extraction costs exactly one range request.
    """

    def __init__(
        self,
        url: str,
        *,
        timeout: float = 120.0,
        window: tuple[int, int] | None = None,
    ) -> None:
        """``window`` restricts this reader to ``(offset, length)`` of the remote file.

        That is what makes nested archives work. RDD2022 is a ZIP whose members are
        themselves ZIPs, one per country, and — because already-compressed data does
        not compress again — they are *stored* rather than deflated. A stored member is
        byte-for-byte identical to a standalone file at a known offset, so pointing a
        second reader at that window reads the inner archive directly. No intermediate
        download, no temporary 10 GB file.
        """
        self.url = url
        self.timeout = timeout
        self._window = window
        self._size: int | None = window[1] if window else None
        self._entries: dict[str, ZipEntry] | None = None

    # ------------------------------------------------------------------ transport

    def _fetch(self, start: int, end: int) -> bytes:
        """Fetch bytes [start, end] inclusive, as HTTP ranges are defined.

        Offsets are relative to the window when one is set, so callers (and the ZIP
        parsing above) never need to know they are inside a nested archive.
        """
        if self._window is not None:
            base, length = self._window
            if start < 0 or end >= length:
                raise RemoteZipError(
                    f"range {start}-{end} falls outside the nested archive window of {length} bytes"
                )
            start += base
            end += base

        request = Request(
            self.url,
            headers={"Range": f"bytes={start}-{end}", "User-Agent": _USER_AGENT},
        )

        # Fetching a dataset means hundreds of sequential range requests, and a reset
        # partway through is normal rather than exceptional — servers and proxies drop
        # long request streams. Retrying is what makes an unattended multi-GB ingest
        # survive; without it a single reset discards everything fetched so far.
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                    if response.status == 200:
                        # The server ignored the range and is sending the whole file.
                        # Refuse rather than silently pulling 13 GB.
                        raise RemoteZipError(
                            "server ignored the Range header and would send the entire "
                            "archive; partial extraction is unavailable from this host"
                        )
                    if response.status != 206:
                        raise RemoteZipError(
                            f"unexpected HTTP {response.status} for a range request"
                        )
                    return response.read()
            except RemoteZipError:
                raise  # a protocol problem will not fix itself by retrying
            except Exception as exc:  # noqa: BLE001 - transport errors are varied
                last_error = exc
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_BACKOFF_S * (2**attempt))

        raise RemoteZipError(
            f"range {start}-{end} failed after {_MAX_RETRIES} attempts: {last_error}"
        ) from last_error

    @property
    def size(self) -> int:
        """Archive size — the window length when nested, else from Content-Range."""
        if self._size is not None:
            return self._size
        if self._window is not None:  # pragma: no cover - set in __init__
            return self._window[1]
        request = Request(self.url, headers={"Range": "bytes=0-0", "User-Agent": _USER_AGENT})
        with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
            content_range = response.headers.get("Content-Range", "")
        if "/" not in content_range:
            raise RemoteZipError(
                "server did not return a Content-Range header; it likely does not "
                "support range requests, so partial extraction is unavailable"
            )
        total = content_range.rsplit("/", 1)[-1].strip()
        if not total.isdigit():
            raise RemoteZipError(f"could not parse archive size from {content_range!r}")
        self._size = int(total)
        return self._size

    # --------------------------------------------------------------- index parsing

    def _load_index(self) -> dict[str, ZipEntry]:
        if self._entries is not None:
            return self._entries

        size = self.size
        # The EOCD is at the very end unless a ZIP comment follows it; the comment is
        # capped at 64 KB, so this window always contains it.
        tail_len = min(size, 65536 + 22)
        tail = self._fetch(size - tail_len, size - 1)

        eocd_at = tail.rfind(_EOCD_SIG)
        if eocd_at < 0:
            raise RemoteZipError("not a ZIP archive: end-of-central-directory not found")

        # EOCD layout: sig(4) disk(2) cd_disk(2) disk_entries(2) total_entries(2)
        # cd_size(4) cd_offset(4) comment_len(2)
        entry_count = struct.unpack_from("<H", tail, eocd_at + 10)[0]
        cd_size = struct.unpack_from("<I", tail, eocd_at + 12)[0]
        cd_offset = struct.unpack_from("<I", tail, eocd_at + 16)[0]

        # Zip64: any saturated field means the true values live in the Zip64 records.
        if entry_count == _U16_MAX or cd_size == _U32_MAX or cd_offset == _U32_MAX:
            entry_count, cd_size, cd_offset = self._read_zip64(tail, eocd_at, size)

        raw_cd = self._fetch(cd_offset, cd_offset + cd_size - 1)
        entries = {e.name: e for e in _parse_central_directory(raw_cd, entry_count)}
        self._entries = entries
        return entries

    def _read_zip64(self, tail: bytes, eocd_at: int, size: int) -> tuple[int, int, int]:
        """Resolve the true index location from the Zip64 records."""
        locator_at = tail.rfind(_EOCD64_LOCATOR_SIG, 0, eocd_at)
        if locator_at < 0:
            raise RemoteZipError("archive needs Zip64 (over 4 GB) but the Zip64 locator is missing")
        eocd64_offset = struct.unpack_from("<Q", tail, locator_at + 8)[0]
        if eocd64_offset >= size:
            raise RemoteZipError("Zip64 end-of-central-directory offset is out of range")

        raw = self._fetch(eocd64_offset, min(eocd64_offset + 63, size - 1))
        if not raw.startswith(_EOCD64_SIG):
            raise RemoteZipError("Zip64 end-of-central-directory signature not found")
        entry_count = struct.unpack_from("<Q", raw, 32)[0]
        cd_size = struct.unpack_from("<Q", raw, 40)[0]
        cd_offset = struct.unpack_from("<Q", raw, 48)[0]
        return entry_count, cd_size, cd_offset

    # ------------------------------------------------------------------- public API

    def namelist(self) -> list[str]:
        return list(self._load_index())

    def entries(self) -> list[ZipEntry]:
        return list(self._load_index().values())

    def iter_matching(self, prefix: str = "", suffix: str = "") -> Iterator[ZipEntry]:
        """Entries whose name starts with ``prefix`` and ends with ``suffix``.

        Names inside the archive use forward slashes regardless of the platform that
        created it.
        """
        for entry in self._load_index().values():
            if entry.is_dir:
                continue
            if entry.name.startswith(prefix) and entry.name.endswith(suffix):
                yield entry

    def _resolve(self, entry: ZipEntry | str) -> ZipEntry:
        if isinstance(entry, str):
            index = self._load_index()
            if entry not in index:
                raise KeyError(f"not in archive: {entry}")
            return index[entry]
        return entry

    def data_offset(self, entry: ZipEntry | str) -> int:
        """Byte offset (window-relative) where this member's data begins.

        Read from the *local* header rather than the central directory, because the
        two are allowed to carry different name and extra-field lengths and only the
        local one describes the actual data position.
        """
        entry = self._resolve(entry)
        header = self._fetch(entry.header_offset, entry.header_offset + 29)
        if not header.startswith(_LOCAL_SIG):
            raise RemoteZipError(f"local header not found for {entry.name}")
        name_len = struct.unpack_from("<H", header, 26)[0]
        extra_len = struct.unpack_from("<H", header, 28)[0]
        return entry.header_offset + 30 + name_len + extra_len

    def open_nested(self, entry: ZipEntry | str) -> RemoteZip:
        """Open a ZIP stored inside this one, without downloading it.

        Only works for *stored* members (compression method 0). A deflated inner
        archive would have to be decompressed in full first, which defeats the purpose
        — so that case raises rather than silently downloading gigabytes.
        """
        entry = self._resolve(entry)
        if entry.compress_type != 0:
            raise RemoteZipError(
                f"{entry.name} is compressed (method {entry.compress_type}); nested "
                "random access requires a stored member"
            )
        base = self.data_offset(entry)
        if self._window is not None:
            base += self._window[0]
        return RemoteZip(self.url, timeout=self.timeout, window=(base, entry.compressed_size))

    def read_many(self, entries: Iterable[ZipEntry]) -> Iterator[tuple[ZipEntry, bytes]]:
        """Read many members with far fewer requests than one-at-a-time.

        ZIP members are laid out sequentially, so a set of nearby entries can be pulled
        in a single range request and split apart locally. For a few hundred small
        files that turns ~2 requests per file into a handful overall — the difference
        between an ingest that finishes and one that trips connection resets partway.

        Yields in archive order. Any entry that cannot be satisfied from its bulk block
        falls back to an individual fetch, so correctness never depends on the guess.
        """
        ordered = sorted(entries, key=lambda e: e.header_offset)
        if not ordered:
            return

        # Group into runs of entries that sit close together in the archive.
        runs: list[list[ZipEntry]] = [[ordered[0]]]
        for entry in ordered[1:]:
            previous = runs[-1][-1]
            previous_end = (
                previous.header_offset + 30 + _LOCAL_HEADER_SLACK + previous.compressed_size
            )
            if entry.header_offset - previous_end <= _BULK_GAP_BYTES:
                runs[-1].append(entry)
            else:
                runs.append([entry])

        for run in runs:
            start = run[0].header_offset
            last = run[-1]
            end = min(
                last.header_offset + 30 + _LOCAL_HEADER_SLACK + last.compressed_size,
                self.size - 1,
            )
            try:
                blob = self._fetch(start, end)
            except RemoteZipError:
                blob = b""

            for entry in run:
                rel = entry.header_offset - start
                data = None
                if blob and rel + 30 <= len(blob) and blob[rel : rel + 4] == _LOCAL_SIG:
                    name_len = struct.unpack_from("<H", blob, rel + 26)[0]
                    extra_len = struct.unpack_from("<H", blob, rel + 28)[0]
                    begin = rel + 30 + name_len + extra_len
                    if begin + entry.compressed_size <= len(blob):
                        data = self._decode(entry, blob[begin : begin + entry.compressed_size])
                if data is None:
                    data = self.read(entry)
                yield entry, data

    def read(self, entry: ZipEntry | str) -> bytes:
        """Fetch and decompress a single member."""
        entry = self._resolve(entry)
        data_start = self.data_offset(entry)

        if entry.compressed_size == 0:
            return b""
        raw = self._fetch(data_start, data_start + entry.compressed_size - 1)
        return self._decode(entry, raw)

    @staticmethod
    def _decode(entry: ZipEntry, raw: bytes) -> bytes:
        """Decompress and verify one member's raw bytes."""
        if entry.compress_type == 0:  # stored
            data = raw
        elif entry.compress_type == 8:  # deflate
            data = zlib.decompress(raw, -zlib.MAX_WBITS)
        else:
            raise RemoteZipError(
                f"unsupported compression method {entry.compress_type} for {entry.name}"
            )

        if len(data) != entry.uncompressed_size:
            raise RemoteZipError(
                f"size mismatch for {entry.name}: got {len(data)}, expected "
                f"{entry.uncompressed_size}"
            )
        if entry.crc and zlib.crc32(data) & 0xFFFFFFFF != entry.crc:
            raise RemoteZipError(f"CRC mismatch for {entry.name}; the download is corrupt")
        return data


def _parse_central_directory(raw: bytes, expected: int) -> Iterator[ZipEntry]:
    """Walk the central directory, resolving Zip64 extras where present."""
    offset = 0
    seen = 0
    while offset + 46 <= len(raw):
        if raw[offset : offset + 4] != _CENTRAL_SIG:
            break
        # Central directory header, byte by byte:
        #   0 sig(4) | 4 ver_made(2) | 6 ver_need(2) | 8 flags(2) | 10 method(2)
        #  12 time(2) | 14 date(2)   | 16 crc(4)     | 20 csize(4) | 24 usize(4)
        #  28 name_len(2) | 30 extra_len(2) | 32 comment_len(2) | 34 disk(2)
        #  36 int_attr(2) | 38 ext_attr(4)  | 42 local_header_offset(4) | 46 name...
        (
            compress_type,
            crc,
            compressed_size,
            uncompressed_size,
            name_len,
            extra_len,
            comment_len,
            header_offset,
        ) = struct.unpack_from("<10xH4xIIIHHH8xI", raw, offset)

        name_at = offset + 46
        name = raw[name_at : name_at + name_len].decode("utf-8", errors="replace")
        extra = raw[name_at + name_len : name_at + name_len + extra_len]

        if _U32_MAX in (uncompressed_size, compressed_size, header_offset):
            uncompressed_size, compressed_size, header_offset = _apply_zip64_extra(
                extra, uncompressed_size, compressed_size, header_offset
            )

        yield ZipEntry(
            name=name.replace("\\", "/"),
            compress_type=compress_type,
            compressed_size=compressed_size,
            uncompressed_size=uncompressed_size,
            header_offset=header_offset,
            crc=crc,
        )
        seen += 1
        offset = name_at + name_len + extra_len + comment_len

    if expected and seen != expected:
        # Not fatal — a truncated index still yields usable entries — but the caller
        # deserves to know the listing may be incomplete.
        raise RemoteZipError(f"central directory listed {expected} entries but {seen} were parsed")


def _apply_zip64_extra(
    extra: bytes, uncompressed: int, compressed: int, header_offset: int
) -> tuple[int, int, int]:
    """Replace saturated 32-bit fields with their 64-bit values.

    The Zip64 extra field packs only the fields that actually overflowed, in a fixed
    order, so which values are present depends on which were saturated.
    """
    pos = 0
    while pos + 4 <= len(extra):
        field_id, field_len = struct.unpack_from("<HH", extra, pos)
        body = extra[pos + 4 : pos + 4 + field_len]
        if field_id == 0x0001:
            cursor = 0
            if uncompressed == _U32_MAX and cursor + 8 <= len(body):
                uncompressed = struct.unpack_from("<Q", body, cursor)[0]
                cursor += 8
            if compressed == _U32_MAX and cursor + 8 <= len(body):
                compressed = struct.unpack_from("<Q", body, cursor)[0]
                cursor += 8
            if header_offset == _U32_MAX and cursor + 8 <= len(body):
                header_offset = struct.unpack_from("<Q", body, cursor)[0]
                cursor += 8
            break
        pos += 4 + field_len
    return uncompressed, compressed, header_offset
