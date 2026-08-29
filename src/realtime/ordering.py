"""Explicit per-source sequence classification (RT-0 contract).

Does not reorder events. High-water sequence is retained so a late print
after a gap is classified late rather than silently inserted.
"""

from __future__ import annotations

from dataclasses import astuple
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
    """Identity conflicts are halt-class. Late/gap are diagnostics, not halt."""
    return kind is OrderingClass.CONFLICT


class SequenceTracker:
    """Classify events relative to the last observed sequence per source."""

    def __init__(self) -> None:
        self._last_seq: dict[str, int] = {}
        self._ids: dict[str, set[str]] = {}
        self._seq_ids: dict[tuple[str, int], str] = {}
        self._id_seq: dict[tuple[str, str], int] = {}
        self._id_fp: dict[tuple[str, str], tuple] = {}

    def classify(self, event: CanonicalEvent, *, commit: bool = True) -> OrderingClass:
        source, event_id = identity_key(event)
        _, sequence = stream_key(event)
        fingerprint = astuple(event)
        seen_ids = self._ids.setdefault(source, set())
        id_key = (source, event_id)

        if event_id in seen_ids:
            previous_seq = self._id_seq.get(id_key)
            previous_fp = self._id_fp.get(id_key)
            if previous_seq != sequence or previous_fp != fingerprint:
                return OrderingClass.CONFLICT
            return OrderingClass.DUPLICATE

        seq_key = (source, sequence)
        prior_id = self._seq_ids.get(seq_key)
        if prior_id is not None and prior_id != event_id:
            return OrderingClass.CONFLICT

        last = self._last_seq.get(source)
        if last is None:
            kind = OrderingClass.ORDERED
        elif sequence == last + 1:
            kind = OrderingClass.ORDERED
        elif sequence > last + 1:
            kind = OrderingClass.SEQUENCE_GAP
        else:
            kind = OrderingClass.LATE

        if commit:
            if kind is OrderingClass.LATE:
                self._remember_identity(
                    source, sequence, event_id, fingerprint, update_high_water=False
                )
            else:
                self._remember_identity(
                    source, sequence, event_id, fingerprint, update_high_water=True
                )
        return kind

    def _remember_identity(
        self,
        source: str,
        sequence: int,
        event_id: str,
        fingerprint: tuple,
        *,
        update_high_water: bool,
    ) -> None:
        if update_high_water:
            self._last_seq[source] = sequence
        self._ids.setdefault(source, set()).add(event_id)
        seq_key = (source, sequence)
        if seq_key not in self._seq_ids:
            self._seq_ids[seq_key] = event_id
        self._id_seq[(source, event_id)] = sequence
        self._id_fp[(source, event_id)] = fingerprint
