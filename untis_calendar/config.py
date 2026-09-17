from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

# Root-only Secrets-Datei; systemd reicht die Werte per EnvironmentFile weiter.
SECRETS_FILE = "/etc/untis-sync.env"


class ElementConfig(BaseModel):
    type: Optional[str] = Field(None, description="student|teacher|class|subject|room")
    name: Optional[str] = None
    id: Optional[int] = None


class CalendarConfig(BaseModel):
    file_name: str
    display_name: Optional[str] = None  # Kalendername in Google/Apple
    web_feed: bool = True
    token: Optional[str] = None  # Direkt in YAML
    token_env: Optional[str] = None  # Fallback: aus ENV

    @property
    def feed_token(self) -> Optional[str]:
        """Token für Feed-Auth: erst YAML, dann ENV."""
        return self.token or (os.getenv(self.token_env) if self.token_env else None)


class FiltersConfig(BaseModel):
    include_subjects: List[str] = Field(default_factory=list)
    exclude_subjects: List[str] = Field(default_factory=list)


class AccountConfig(BaseModel):
    key: str
    school: str
    username: str
    password: Optional[str] = None  # Direkt in YAML
    password_env: Optional[str] = None  # Fallback: aus ENV
    # Optional: leer lassen, dann wird der Server automatisch aufgelöst
    server: Optional[str] = None
    verify_ssl: bool = True
    enabled: bool = True
    include_cancelled: bool = True  # Entfallene Stunden als CANCELLED mitliefern
    element: Optional[ElementConfig] = None
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    color_map: Dict[str, str] = Field(default_factory=dict)
    calendar: CalendarConfig

    def get_password(self) -> str:
        """Passwort: erst YAML, dann ENV, sonst Error."""
        if self.password:
            return self.password
        if self.password_env:
            val = os.getenv(self.password_env)
            if val:
                return val
            raise ValueError(f"ENV {self.password_env} nicht gesetzt")
        raise ValueError(
            f"Kein Passwort für Account '{self.key}' (weder 'password' noch 'password_env')"
        )


class AppConfig(BaseModel):
    timezone: str = "Europe/Berlin"
    window_days_before: int = 3
    window_days_after: int = 28
    cache_ttl_seconds: int = 600
    output_dir: str = "./out"
    mode: str = "cli"
    merge_consecutive: bool = True  # Doppelstunden zu einem Termin zusammenfassen
    subject_style: str = "long"     # long | short | both - Fach in der Terminueberschrift
    fetch_online_info: bool = True  # Online-Unterricht/Meeting-Links per REST nachladen
    cancelled_style: str = "mark"   # mark | status | hide - siehe README
    # Hintergrund-Aktualisierung im Server-Modus (0 = aus).
    # Waehrend der aktiven Stunden wird haeufig, sonst selten abgerufen -
    # ein Stundenplan aendert sich nachts nicht.
    refresh_interval_minutes: int = 15
    refresh_idle_minutes: int = 120
    active_hours_start: int = 6   # lokale Stunde, ab der haeufig geprueft wird
    active_hours_end: int = 22    # lokale Stunde, ab der wieder selten geprueft wird

    @field_validator("active_hours_start", "active_hours_end")
    @classmethod
    def valid_hour(cls, v: int) -> int:
        if not 0 <= v <= 23:
            raise ValueError("Stunde muss zwischen 0 und 23 liegen")
        return v

    @field_validator("cancelled_style")
    @classmethod
    def valid_cancelled_style(cls, v: str) -> str:
        if v not in ("mark", "status", "hide"):
            raise ValueError("cancelled_style muss 'mark', 'status' oder 'hide' sein")
        return v

    @field_validator("subject_style")
    @classmethod
    def valid_subject_style(cls, v: str) -> str:
        if v not in ("long", "short", "both"):
            raise ValueError("subject_style muss 'long', 'short' oder 'both' sein")
        return v

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, v: str) -> str:
        if v not in ("cli", "server"):
            raise ValueError("mode muss 'cli' oder 'server' sein")
        return v


class ServerConfig(BaseModel):
    etag: bool = True
    last_modified: bool = True

    # Interaktive API-Doku (/docs, /redoc, /openapi.json). Standardmaessig aus:
    # der Dienst steht oeffentlich und die Doku verraet nur die Angriffsflaeche.
    docs_enabled: bool = False

    # /status verraet Account-Keys, Schulen und Fehlertexte und ist deshalb
    # tokenpflichtig. Ohne gesetzten Token antwortet der Endpunkt mit 404.
    status_token: Optional[str] = None
    status_token_env: Optional[str] = None

    # X-Content-Type-Options, Referrer-Policy usw. an jede Antwort haengen
    security_headers: bool = True

    # Token aus den Zugriffslogs entfernen (sie landen sonst im Journal)
    redact_tokens_in_logs: bool = True

    @property
    def status_secret(self) -> Optional[str]:
        return self.status_token or (
            os.getenv(self.status_token_env) if self.status_token_env else None
        )


class Config(BaseModel):
    app: AppConfig
    server: ServerConfig = Field(default_factory=ServerConfig)
    accounts: List[AccountConfig]

    @model_validator(mode="after")
    def check_accounts(self) -> "Config":
        keys = [a.key for a in self.accounts]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"Doppelte Account-Keys in der Config: {sorted(dupes)}")

        files = [a.calendar.file_name for a in self.accounts]
        dupe_files = {f for f in files if files.count(f) > 1}
        if dupe_files:
            raise ValueError(
                f"Mehrere Accounts schreiben in dieselbe Datei: {sorted(dupe_files)}"
            )

        # Gleiche Tokens sind kein harter Fehler, aber ein Sicherheitsproblem:
        # wer einen Feed kennt, kann alle anderen mitlesen.
        tokens = [
            a.calendar.feed_token
            for a in self.accounts
            if a.calendar.web_feed and a.calendar.feed_token
        ]
        shared = {t for t in tokens if tokens.count(t) > 1}
        if shared:
            logger.warning(
                "Mehrere Accounts nutzen denselben Feed-Token - bitte pro Account "
                "einen eigenen Token vergeben."
            )
        return self

    @property
    def active_accounts(self) -> List[AccountConfig]:
        return [a for a in self.accounts if a.enabled]

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Config-Datei nicht gefunden: {p}")

        # Secrets laden, damit password_env/token_env auch bei manuellen
        # CLI-Aufrufen funktionieren. Im Dienst-Betrieb liefert systemd die
        # Werte bereits per EnvironmentFile - bestehende Variablen werden
        # deshalb nicht ueberschrieben.
        for env_file in (Path(SECRETS_FILE), p.parent / ".env"):
            try:
                if not env_file.exists():
                    continue
                from dotenv import load_dotenv
                load_dotenv(env_file, override=False)
                logger.debug("Secrets geladen aus %s", env_file)
            except PermissionError:
                # /etc/untis-sync.env ist root-only - als www-data erwartbar
                logger.debug("Keine Leseberechtigung fuer %s", env_file)
            except ImportError:
                break

        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Config ist leer oder kein YAML-Mapping: {p}")
        cfg = cls.model_validate(data)
        Path(cfg.app.output_dir).mkdir(parents=True, exist_ok=True)
        return cfg
