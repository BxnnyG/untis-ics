from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class LessonEvent:
    uid: str
    start: datetime
    end: datetime
    subject: str  # abbreviation, e.g. "EVP"
    room: str | None  # room number, e.g. "R102"
    teachers: list[str]  # abbreviations, e.g. ["KL"]
    groups: list[str]
    status: str  # scheduled|cancelled|substitution|moved
    notes: str | None
    color_key: str | None
    source_id: str
    source_school: str
    account_key: str

    # Full names from Untis ("longname"). Untis almost always sends them;
    # they simply were never read.
    subject_long: str | None = None  # "Netzwerktechnik"
    teachers_long: list[str] = field(default_factory=list)  # ["Beispiel"]
    room_long: str | None = None  # "PC-Raum"

    # Online lessons (only available through the REST view)
    online: bool = False
    meeting_url: str | None = None

    # Reschedules and substitutions (REST view as well)
    moved_from: datetime | None = None  # this lesson came from there
    moved_to: datetime | None = None  # this lesson takes place there
    substitutions: list[tuple] = field(default_factory=list)

    # Subject colour from Untis as hex ("#80ffff")
    color: str | None = None
    # Period names from the school's timegrid ("1", "2")
    periods: list[str] = field(default_factory=list)

    def subject_display(self, style: str = "long") -> str:
        """Subject as it should appear in the event title."""
        if style == "short" or not self.subject_long:
            return self.subject
        if style == "both" and self.subject_long != self.subject:
            return f"{self.subject} - {self.subject_long}"
        return self.subject_long

    def period_display(self) -> str | None:
        """ "1. Stunde", or "1.-2. Stunde" for merged double periods."""
        if not self.periods:
            return None
        if len(self.periods) == 1:
            return f"{self.periods[0]}. Stunde"
        return f"{self.periods[0]}.-{self.periods[-1]}. Stunde"

    def teacher_display(self) -> list[str]:
        """Teachers by full name, falling back to the abbreviation."""
        if self.teachers_long:
            return self.teachers_long
        return self.teachers
