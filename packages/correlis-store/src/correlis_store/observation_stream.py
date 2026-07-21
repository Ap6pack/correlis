from __future__ import annotations

from dataclasses import dataclass

from .observation_sequence import SequencedObservation


@dataclass(frozen=True, slots=True)
class ScopedObservationStreamPage:
    observations: tuple[SequencedObservation, ...]
    starting_position: int
    next_position: int
    high_watermark: int
    scanned_count: int
    has_more: bool
