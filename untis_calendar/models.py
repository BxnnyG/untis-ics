from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class LessonEvent:
    uid: str
    start: datetime
    end: datetime
    subject: str                 # Kuerzel, z. B. "EVP"
    room: str | None          # Raumnummer, z. B. "R102"
    teachers: list[str]          # Kuerzel, z. B. ["KL"]
    groups: list[str]
    status: str                  # scheduled|cancelled|substitution|moved
    notes: str | None
    color_key: str | None
    source_id: str
    source_school: str
    account_key: str

    # Klarnamen aus Untis ("longname"). Untis liefert die praktisch immer mit,
    # nur abgefragt hat sie vorher niemand.
    subject_long: str | None = None    # "Netzwerktechnik"
    teachers_long: list[str] = field(default_factory=list)  # ["Beispiel"]
    room_long: str | None = None       # "PC-Raum"

    # Online-Unterricht (nur ueber die REST-Ansicht verfuegbar)
    online: bool = False
    meeting_url: str | None = None

    # Verlegung und Vertretung (ebenfalls nur ueber die REST-Ansicht)
    moved_from: datetime | None = None   # diese Stunde kommt von dort
    moved_to: datetime | None = None     # diese Stunde findet dort statt
    substitutions: list[tuple] = field(default_factory=list)

    def subject_display(self, style: str = "long") -> str:
        """Fach fuer die Terminueberschrift."""
        if style == "short" or not self.subject_long:
            return self.subject
        if style == "both" and self.subject_long != self.subject:
            return f"{self.subject} - {self.subject_long}"
        return self.subject_long

    def teacher_display(self) -> list[str]:
        """Lehrer mit Klarnamen, Kuerzel nur als Rueckfall."""
        if self.teachers_long:
            return self.teachers_long
        return self.teachers
