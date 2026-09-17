from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

# Root-only secrets file; systemd passes the values on via EnvironmentFile.
SECRETS_FILE = "/etc/untis-sync.env"

# Prefix for environment variables that override values from config.yaml.
# UNTIS_APP_TIMEZONE -> app.timezone, UNTIS_SERVER_DOCS_ENABLED -> server.docs_enabled.
# Meant for containers, where mounting a file for every little setting is a chore.
ENV_PREFIX = "UNTIS_"


def _apply_env_overrides(data: dict) -> list[str]:
    """Override app.* and server.* from the environment.

    Returns the keys that were applied so the caller can log them - silently
    altered configuration is miserable to debug.
    """
    applied: list[str] = []
    for section, model in (("app", AppConfig), ("server", ServerConfig)):
        for field in model.model_fields:
            env_name = f"{ENV_PREFIX}{section.upper()}_{field.upper()}"
            raw = os.getenv(env_name)
            if raw is None:
                continue
            data.setdefault(section, {})
            # pydantic handles the type conversion during validation; for
            # bools a bare "false" would be truthy, which pydantic catches.
            data[section][field] = raw
            applied.append(f"{env_name} -> {section}.{field}")
    return applied


class ElementConfig(BaseModel):
    type: str | None = Field(None, description="student|teacher|class|subject|room")
    name: str | None = None
    id: int | None = None


class CalendarConfig(BaseModel):
    file_name: str
    display_name: str | None = None  # calendar name in Google/Apple
    web_feed: bool = True
    token: str | None = None  # straight from YAML
    token_env: str | None = None  # fallback: from the environment

    @property
    def feed_token(self) -> str | None:
        """Feed auth token: YAML first, then the environment."""
        return self.token or (os.getenv(self.token_env) if self.token_env else None)


class FiltersConfig(BaseModel):
    include_subjects: list[str] = Field(default_factory=list)
    exclude_subjects: list[str] = Field(default_factory=list)


class AccountConfig(BaseModel):
    key: str
    school: str
    username: str
    password: str | None = None  # straight from YAML
    password_env: str | None = None  # fallback: from the environment
    # Optional: leave empty and the server is resolved automatically
    server: str | None = None
    verify_ssl: bool = True
    enabled: bool = True
    include_cancelled: bool = True  # keep cancelled lessons in the feed
    element: ElementConfig | None = None
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    color_map: dict[str, str] = Field(default_factory=dict)
    calendar: CalendarConfig

    def get_password(self) -> str:
        """Password: YAML first, then the environment, otherwise an error."""
        if self.password:
            return self.password
        if self.password_env:
            val = os.getenv(self.password_env)
            if val:
                return val
            raise ValueError(f"Environment variable {self.password_env} is not set")
        raise ValueError(
            f"No password for account '{self.key}' (neither 'password' nor 'password_env')"
        )


class AppConfig(BaseModel):
    timezone: str = "Europe/Berlin"
    window_days_before: int = 3
    window_days_after: int = 28
    cache_ttl_seconds: int = 600
    output_dir: str = "./out"
    mode: str = "cli"
    merge_consecutive: bool = True  # collapse double periods into one event
    subject_style: str = "long"  # long | short | both - subject in the title
    fetch_online_info: bool = True  # load online lessons/meeting links via REST
    use_untis_colors: bool = True  # carry the school's subject colours over
    show_period_numbers: bool = True  # add "3. Stunde" to the description
    cancelled_style: str = "mark"  # mark | status | hide - see README
    # Background refresh in server mode (0 = off). Often during active hours,
    # rarely outside them - a timetable does not change overnight.
    refresh_interval_minutes: int = 15
    refresh_idle_minutes: int = 120
    active_hours_start: int = 6  # local hour when frequent checks start
    active_hours_end: int = 22  # local hour when they drop back to rare

    @field_validator("active_hours_start", "active_hours_end")
    @classmethod
    def valid_hour(cls, v: int) -> int:
        if not 0 <= v <= 23:
            raise ValueError("Hour must be between 0 and 23")
        return v

    @field_validator("cancelled_style")
    @classmethod
    def valid_cancelled_style(cls, v: str) -> str:
        if v not in ("mark", "status", "hide"):
            raise ValueError("cancelled_style must be 'mark', 'status' or 'hide'")
        return v

    @field_validator("subject_style")
    @classmethod
    def valid_subject_style(cls, v: str) -> str:
        if v not in ("long", "short", "both"):
            raise ValueError("subject_style must be 'long', 'short' or 'both'")
        return v

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, v: str) -> str:
        if v not in ("cli", "server"):
            raise ValueError("mode must be 'cli' or 'server'")
        return v


class ServerConfig(BaseModel):
    etag: bool = True
    last_modified: bool = True

    # Interactive API docs (/docs, /redoc, /openapi.json). Off by default: the
    # service is publicly reachable and the docs only map the attack surface.
    docs_enabled: bool = False

    # /status exposes account keys, schools and error text, so it needs a
    # token. Without one the endpoint answers 404.
    status_token: str | None = None
    status_token_env: str | None = None

    # Attach X-Content-Type-Options, Referrer-Policy etc. to every response
    security_headers: bool = True

    # Strip tokens from access logs (they end up in the journal otherwise)
    redact_tokens_in_logs: bool = True

    # Liveness ping to an external monitor (Healthchecks.io, Uptime Kuma and
    # the like). Only sent when every enabled account has fresh data - if it
    # stops, the monitor raises the alarm.
    heartbeat_url: str | None = None
    heartbeat_url_env: str | None = None
    # Age at which data counts as stale. 0 = three times the refresh interval.
    stale_after_minutes: int = 0

    @property
    def heartbeat(self) -> str | None:
        return self.heartbeat_url or (
            os.getenv(self.heartbeat_url_env) if self.heartbeat_url_env else None
        )

    @property
    def status_secret(self) -> str | None:
        return self.status_token or (
            os.getenv(self.status_token_env) if self.status_token_env else None
        )


class Config(BaseModel):
    app: AppConfig
    server: ServerConfig = Field(default_factory=ServerConfig)
    accounts: list[AccountConfig]

    @model_validator(mode="after")
    def check_accounts(self) -> Config:
        keys = [a.key for a in self.accounts]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"Duplicate account keys in the config: {sorted(dupes)}")

        files = [a.calendar.file_name for a in self.accounts]
        dupe_files = {f for f in files if files.count(f) > 1}
        if dupe_files:
            raise ValueError(f"Several accounts write to the same file: {sorted(dupe_files)}")

        # Identical tokens are not a hard error but they are a security
        # problem: whoever knows one feed can read all the others.
        tokens = [
            a.calendar.feed_token
            for a in self.accounts
            if a.calendar.web_feed and a.calendar.feed_token
        ]
        shared = {t for t in tokens if tokens.count(t) > 1}
        if shared:
            logger.warning(
                "Several accounts share the same feed token - please give each "
                "account its own."
            )
        return self

    @property
    def active_accounts(self) -> list[AccountConfig]:
        return [a for a in self.accounts if a.enabled]

    @classmethod
    def load(cls, path: str | Path) -> Config:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: {p}")

        # Load secrets so password_env/token_env also work for manual CLI
        # runs. Under systemd the values already come from EnvironmentFile, so
        # existing variables are never overwritten.
        for env_file in (Path(SECRETS_FILE), p.parent / ".env"):
            try:
                if not env_file.exists():
                    continue
                from dotenv import load_dotenv

                load_dotenv(env_file, override=False)
                logger.debug("Secrets loaded from %s", env_file)
            except PermissionError:
                # The secrets file is root-only; unreadable as the service user
                logger.debug("No read permission for %s", env_file)
            except ImportError:
                break

        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Config is empty or not a YAML mapping: {p}")

        for note in _apply_env_overrides(data):
            logger.info("Configuration from environment: %s", note)

        cfg = cls.model_validate(data)
        Path(cfg.app.output_dir).mkdir(parents=True, exist_ok=True)
        return cfg
