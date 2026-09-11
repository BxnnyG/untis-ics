from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class LessonEvent:
    uid: str
    start: datetime
    end: datetime
    subject: str                 # Kuerzel, z. B. "EVP"
    room: Optional[str]          # Raumnummer, z. B. "1012"
    teachers: List[str]          # Kuerzel, z. B. ["KL"]
    groups: List[str]
    status: str                  # scheduled|cancelled|substitution|moved
    notes: Optional[str]
    color_key: Optional[str]
    source_id: str
    source_school: str
    account_key: str

    # Klarnamen aus Untis ("longname"). Untis liefert die praktisch immer mit,
    # nur abgefragt hat sie vorher niemand.
    subject_long: Optional[str] = None    # "Entwicklung vernetzter Prozesse"
    teachers_long: List[str] = field(default_factory=list)  # ["Beispiel"]
    room_long: Optional[str] = None       # "TG-PC-RAUM"

    def subject_display(self, style: str = "long") -> str:
        """Fach fuer die Terminueberschrift."""
        if style == "short" or not self.subject_long:
            return self.subject
        if style == "both" and self.subject_long != self.subject:
            return f"{self.subject} - {self.subject_long}"
        return self.subject_long

    def teacher_display(self) -> List[str]:
        """Lehrer mit Klarnamen, Kuerzel nur als Rueckfall."""
        if self.teachers_long:
            return self.teachers_long
        return self.teachers
