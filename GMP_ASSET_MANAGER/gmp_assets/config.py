"""Runtime settings, overridable through environment variables."""

import os
from dataclasses import dataclass, field
from typing import Tuple


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.environ.get("GMP_DB_PATH", "gmp_assets.db"))
    # Part 11 §11.300 / Annex 11 §12: session and credential controls
    session_idle_minutes: int = field(default_factory=lambda: _env_int("GMP_SESSION_IDLE_MINUTES", 15))
    max_failed_logins: int = field(default_factory=lambda: _env_int("GMP_MAX_FAILED_LOGINS", 5))
    password_max_age_days: int = field(default_factory=lambda: _env_int("GMP_PASSWORD_MAX_AGE_DAYS", 90))
    password_min_length: int = field(default_factory=lambda: _env_int("GMP_PASSWORD_MIN_LENGTH", 10))
    pbkdf2_iterations: int = field(default_factory=lambda: _env_int("GMP_PBKDF2_ITERATIONS", 310_000))
    # Keyboard-wedge scanners often cannot transmit ASCII 29 (GS / FNC1), so
    # they are configured to send a substitute. These are all treated as GS.
    gs1_separators: Tuple[str, ...] = ("\x1d", "<GS>", "{GS}", "|", "~")
    site_name: str = field(default_factory=lambda: os.environ.get("GMP_SITE_NAME", "GMP Site"))
