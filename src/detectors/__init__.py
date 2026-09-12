"""JITA-inspired diagnostic event detectors for FARS (P4).

Baseline-first, opt-in diagnostics: no detector alters trades or management.
Each detector emits diagnostic events with deterministic IDs and available_at.
"""
from src.detectors.events import DiagnosticEvent, EventKind
from src.detectors.crt import detect_crt
from src.detectors.fvg import detect_fvg
from src.detectors.cisd import detect_cisd
from src.detectors.sweeps import detect_sweeps

__all__ = [
    "DiagnosticEvent", "EventKind",
    "detect_crt", "detect_fvg", "detect_cisd", "detect_sweeps",
]
