"""
Lightweight proctoring logic — intentionally simple for a hackathon-grade
build. Tracks tab-switch events per attempt and immediately disqualifies on
the first switch (zero-tolerance, per product requirement). Camera snapshots
are just stored as files (see TestAttemptSnapshot); this module doesn't
attempt real image analysis on them, matching the "keep it simple,
hackathon-ready" scope.
"""

from sqlalchemy.orm import Session

from app.models.models import TestAttempt, TestAttemptEvent


def record_event_and_check_disqualification(
    db: Session, attempt: TestAttempt, event_type: str, event_data: dict | None
) -> tuple[bool, int]:
    """
    Logs a proctoring event, and — for tab_switch events specifically —
    immediately disqualifies the attempt on the very first occurrence.
    Returns (disqualified, tab_switch_count).
    """
    event = TestAttemptEvent(attempt_id=attempt.id, event_type=event_type, event_data=event_data)
    db.add(event)
    db.commit()

    tab_switch_count = (
        db.query(TestAttemptEvent)
        .filter(TestAttemptEvent.attempt_id == attempt.id)
        .filter(TestAttemptEvent.event_type == "tab_switch")
        .count()
    )

    disqualified = False
    if event_type == "tab_switch" and attempt.status == "in_progress":
        attempt.status = "disqualified"
        attempt.disqualification_reason = "Left the test tab/window during the assessment."
        db.commit()
        disqualified = True

    return disqualified, tab_switch_count
