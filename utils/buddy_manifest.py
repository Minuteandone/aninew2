import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple


@dataclass
class BuddySample:
    sample_id: int
    relative_path: str


@dataclass
class BuddyTrack:
    sample_ref: int
    color_block_size: int
    name: str
    color: Tuple[float, float, float, float]


class BuddyManifest:
    """
    Lightweight parser for the game's 001_*.bin buddy audio manifests.

    These files store tables of audio samples plus track bindings that map
    animation names to sample ids.
    """

    def __init__(
        self,
        source_path: Path,
        signature: str,
        version: int,
        labels: List[str],
        samples: List[BuddySample],
        tracks: List[BuddyTrack]
    ) -> None:
        self.source_path = source_path
        self.signature = signature
        self.version = version
        self.labels = labels
        self.samples = samples
        self.tracks = tracks

    @classmethod
    def from_file(cls, path: str) -> "BuddyManifest":
        data = Path(path).read_bytes()
        view = memoryview(data)
        total_len = len(view)
        offset = 0

        def align() -> None:
            nonlocal offset
            offset = (offset + 3) & ~0x03

        def ensure_available(size: int) -> None:
            if offset + size > total_len:
                raise struct.error(
                    f"Unexpected EOF reading {size} bytes at offset {offset} "
                    f"(size={total_len})"
                )

        def read_u32() -> int:
            nonlocal offset
            ensure_available(4)
            value = struct.unpack_from("<I", view, offset)[0]
            offset += 4
            return value

        def read_f32() -> float:
            nonlocal offset
            ensure_available(4)
            value = struct.unpack_from("<f", view, offset)[0]
            offset += 4
            return value

        def read_string() -> str:
            nonlocal offset
            length = read_u32()
            if length <= 0:
                align()
                return ""
            ensure_available(length)
            raw = bytes(view[offset:offset + max(length - 1, 0)])
            offset += length
            align()
            return raw.decode("utf-8", errors="ignore")

        signature = read_string()
        version = read_u32()

        labels: List[str] = [read_string(), read_string(), read_string()]
        _header_size = read_u32()
        _bank_count = read_u32()
        sample_count = read_u32()

        samples: List[BuddySample] = []
        for _ in range(sample_count):
            sample_id = read_u32()
            path_length = read_u32()
            if path_length < 0:
                raise struct.error(f"Invalid path length {path_length} at offset {offset}")
            ensure_available(path_length)
            raw = bytes(view[offset:offset + max(path_length - 1, 0)])
            offset += path_length
            align()
            rel_path = raw.decode("utf-8", errors="ignore")
            samples.append(BuddySample(sample_id, rel_path))
            if offset + 4 <= total_len and struct.unpack_from("<I", view, offset)[0] == 0:
                offset += 4

        # Some manifests insert padding before the track table. Skip zeros.
        def peek_u32() -> Optional[int]:
            if offset + 4 > total_len:
                return None
            return struct.unpack_from("<I", view, offset)[0]

        while True:
            peek = peek_u32()
            if peek is None or peek != 0:
                break
            offset += 4

        track_count = read_u32() if offset + 4 <= total_len else 0

        tracks: List[BuddyTrack] = []
        for _ in range(track_count):
            sample_ref = read_u32()
            color_block_size = read_u32()
            name = read_string()
            color = (
                read_f32(),
                read_f32(),
                read_f32(),
                read_f32(),
            )
            tracks.append(BuddyTrack(sample_ref, color_block_size, name, color))

        return cls(Path(path), signature, version, labels, samples, tracks)

    def iter_audio_links(self) -> Iterator[Tuple[str, Optional[str]]]:
        sample_lookup: Dict[int, str] = {}
        for sample in self.samples:
            sample_lookup[sample.sample_id] = sample.relative_path
            sample_lookup[sample.sample_id & 0xFFFF] = sample.relative_path
            sample_lookup[sample.sample_id & 0xFF] = sample.relative_path
            sample_lookup[sample.sample_id | 0xFF00] = sample.relative_path

        for track in self.tracks:
            rel_path = sample_lookup.get(track.sample_ref)
            yield track.name, rel_path
