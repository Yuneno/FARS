"""Explicit per-source sequence classification (RT-0 contract).

Does not reorder events. High-water sequence is retained so a late print
after a gap is classified late rather than silently inserted.
"""

from __future__ import annotations

from enum import Enum

from src.realtime.events import CanonicalEvent, identity_key, stream_key


class OrderingClass(str, Enum):
    ORDERED = "ordered"
    DUPLICATE = "duplicate"
    LATE = "late"
    SEQUENCE_GAP = "sequence_gap"
    CONFLICT = "conflicting_identity"


def is_out_of_order(kind: OrderingClass) -> bool:
    """Late arrivals and gaps are out-of-order. Duplicates/conflicts are not."""
    return kind in {OrderingClass.LATE, OrderingClass.SEQUENCE_GAP}


def requires_halt(kind: OrderingClass) -> bool:
    """Critical ordering/identity ambiguity. Identical retries do not halt."""
    return kind in {
        OrderingClass.LATE,
        OrderingClass.SEQUENCE_GAP,
        OrderingClass.CONFLICT,
    }


class SequenceTracker:
    """Classify events relative to the last observed sequence per source."""

    def __init__(self) -> None:
        self._last_seq: dict[str, int] = {}
        self._ids: dict[str, set[str]] = {}
        self._seq_ids: dict[tuple[str, int], str] = {}
        self._id_seq: dict[tuple[str, str], int] = {}

    def classify(self, event: CanonicalEvent) -> OrderingClass:
        source, event_id = identity_key(event)
        _, sequence = stream_key(event)
        seen_ids = self._ids.setdefault(source, set())

        if event_id in seen_ids:
            previous_seq = self._id_seq.get((source, event_id))
            if previous_seq is not None and previous_seq != sequence:
                return OrderingClass.CONFLICT
            return OrderingClass.DUPLICATE

        seq_key = (source, sequence)
        prior_id = self._seq_ids.get(seq_key)
        if prior_id is not None and prior_id != event_id:
            return OrderingClass.CONFLICT

        last = self._last_seq.get(source)
        if last is None:
            self._remember(source, sequence, event_id)
            return OrderingClass.ORDERED

        if sequence == last + 1:
            self._remember(source, sequence, event_id)
            return OrderingClass.ORDERED

        if sequence > last + 1:
            self._remember(source, sequence, event_id)
            return OrderingClass.SEQUENCE_GAP

        # Late: keep high-water sequence, but record identity so retries
        # are duplicates instead of repeated late classifications.
        self._ids.setdefault(source, set()).add(event_id)
        if seq_key not in self._seq_ids:
            self._seq_ids[seq_key] = event_id
        self._id_seq[(source, event_id)] = sequence
        return OrderingClass.LATE

    def _remember(self, source: str, sequence: int, event_id: str) -> None:
        self._last_seq[source] = sequence
        self._ids.setdefault(source, set()).add(event_id)
        self._seq_ids[(source, sequence)] = event_id
        self._id_seq[(source, event_id)] = sequence
