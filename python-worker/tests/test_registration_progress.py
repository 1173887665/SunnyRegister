from __future__ import annotations

from sunny_core.worker import (
    CODEX_PHONE_BIND,
    IMPORT_REVERSE_PROXY,
    REGISTER_ONLY,
    _emit_registration_progress,
    _registration_stage_total,
)


class EventRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def event(self, message, level="info", typ="log", detail=None) -> None:
        self.events.append({"message": message, "level": level, "type": typ, "detail": detail or {}})


def test_registration_stage_totals_include_previous_stages() -> None:
    assert _registration_stage_total(REGISTER_ONLY) == 7
    assert _registration_stage_total(CODEX_PHONE_BIND) == 10
    assert _registration_stage_total(IMPORT_REVERSE_PROXY) == 12


def test_progress_event_is_structured_and_clamped_to_selected_stage() -> None:
    recorder = EventRecorder()
    _emit_registration_progress(recorder, "user@example.com", REGISTER_ONLY, "reverse_imported")

    event = recorder.events[0]
    assert event["type"] == "registration_progress"
    assert event["detail"]["email"] == "user@example.com"
    assert event["detail"]["current"] == 7
    assert event["detail"]["total"] == 7
    assert event["detail"]["state"] == "running"


def test_abnormal_progress_exposes_only_bounded_error_summary() -> None:
    recorder = EventRecorder()
    _emit_registration_progress(
        recorder,
        "user@example.com",
        IMPORT_REVERSE_PROXY,
        "failed",
        state="abnormal",
        error="x" * 800,
    )

    event = recorder.events[0]
    assert event["level"] == "error"
    assert event["detail"]["state"] == "abnormal"
    assert len(event["detail"]["error"]) == 500
