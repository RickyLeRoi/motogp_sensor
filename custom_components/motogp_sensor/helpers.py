"""Utility helpers for the MotoGP Sensor integration."""
from __future__ import annotations

from contextlib import suppress
from datetime import date, datetime, timedelta, timezone
from typing import Any


# ── Event helpers ─────────────────────────────────────────────────────────────


def find_current_season(seasons: list[dict]) -> dict | None:
    """Return the current season dict from a list of seasons."""
    if not seasons:
        return None
    current = next((s for s in seasons if s.get("current") is True), None)
    if current:
        return current
    with suppress(Exception):
        return max(seasons, key=lambda s: int(s.get("year", 0) or 0))
    return seasons[-1]


def find_last_completed_event(events: list[dict]) -> dict | None:
    """Return the most recently completed event."""
    if not events:
        return None
    today = date.today()
    completed: list[dict] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        status = str(ev.get("status") or "").strip().lower()
        date_end_str = str(ev.get("date_end") or ev.get("dateEnd") or "").strip()
        if status in ("finished", "completed", "done"):
            completed.append(ev)
        elif date_end_str:
            with suppress(Exception):
                end = date.fromisoformat(date_end_str[:10])
                if end < today:
                    completed.append(ev)
    if not completed:
        return events[-1] if events else None

    def _sort_key(ev: dict) -> str:
        return str(ev.get("date_end") or ev.get("dateEnd") or ev.get("date_start") or "")

    return max(completed, key=_sort_key)


def find_next_event(events: list[dict]) -> dict | None:
    """Return the next upcoming event (first with date_start >= today)."""
    if not events:
        return None
    today = date.today()
    upcoming: list[tuple[date, dict]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        date_start_str = str(ev.get("date_start") or ev.get("dateStart") or "").strip()
        with suppress(Exception):
            start = date.fromisoformat(date_start_str[:10])
            if start >= today:
                upcoming.append((start, ev))
    if upcoming:
        return min(upcoming, key=lambda x: x[0])[1]
    return events[-1] if events else None


def find_current_event(
    events: list[dict],
    start_day: str = "monday",
) -> dict | None:
    """Return the event whose extended window contains today, or None.

    The window starts on `start_day` of the event week (mon/sat/sun) and ends on
    the official date_end.  This mirrors the F1 race_week logic.
    """
    if not events:
        return None
    today = date.today()
    for ev in events:
        if not isinstance(ev, dict):
            continue
        start_str = str(ev.get("date_start") or ev.get("dateStart") or "").strip()
        end_str = str(ev.get("date_end") or ev.get("dateEnd") or "").strip()
        if not start_str or not end_str:
            continue
        with suppress(Exception):
            official_start = date.fromisoformat(start_str[:10])
            official_end = date.fromisoformat(end_str[:10])
            window_start = _race_week_window_start(official_start, start_day)
            if window_start <= today <= official_end:
                return ev
    return None


def _race_week_window_start(official_start: date, start_day: str) -> date:
    """Return the start of the race-week window based on configuration."""
    if start_day == "saturday":
        # Saturday of that week
        saturday = official_start - timedelta(days=(official_start.weekday() - 5) % 7)
        return min(saturday, official_start)
    if start_day == "sunday":
        # Sunday of previous week
        sunday = official_start - timedelta(days=(official_start.weekday() + 1) % 7)
        return min(sunday, official_start)
    # Default: Monday of that week
    return official_start - timedelta(days=official_start.weekday())


def circuit_name(event: dict) -> str | None:
    """Extract circuit name from an event dict."""
    circuit = event.get("circuit") or {}
    if isinstance(circuit, dict):
        return circuit.get("name") or circuit.get("shortname") or None
    return str(circuit) or None


def event_country(event: dict) -> str | None:
    """Extract country name from an event dict."""
    circuit = event.get("circuit") or {}
    if isinstance(circuit, dict):
        country = circuit.get("country") or {}
        if isinstance(country, dict):
            return country.get("name") or country.get("iso") or None
        return str(country) if country else None
    return None


def get_event_sessions(event: dict) -> list[dict]:
    """Return the sessions list embedded in an event dict, if present."""
    sessions = event.get("sessions") or event.get("sessions_list") or []
    return [s for s in sessions if isinstance(s, dict)]


# Session type → canonical key prefix used for schedule attributes
_SESSION_TYPE_MAP: dict[str, str] = {
    "FP1": "first_practice",
    "FP2": "second_practice",
    "FP3": "third_practice",
    "Q1": "qualifying",
    "Q":  "qualifying",
    "RAC": "race",
    "RACE": "race",
}


def _parse_dt(raw: str | None) -> datetime | None:
    """Parse an ISO 8601 datetime string (with or without timezone offset)."""
    if not raw:
        return None
    with suppress(Exception):
        return datetime.fromisoformat(str(raw).strip())
    return None


def _dt_to_local_str(dt: datetime) -> str:
    """Return the datetime as an ISO string preserving the original offset."""
    return dt.isoformat()


def _dt_to_utc_str(dt: datetime) -> str:
    """Return the datetime converted to UTC as an ISO string."""
    if dt.tzinfo is None:
        # Assume UTC if no timezone info
        return dt.replace(tzinfo=timezone.utc).isoformat()
    return dt.astimezone(timezone.utc).isoformat()


def extract_session_schedule(sessions: list[dict]) -> dict[str, str | None]:
    """Return a flat dict of session schedule attributes.

    Keys produced (per session type resolved via _SESSION_TYPE_MAP):
    - ``<prefix>_start_local``: ISO 8601 string preserving the circuit-local offset
    - ``<prefix>_start_utc``:   ISO 8601 string in UTC

    Only the *first* occurrence of each mapped type is used (so Q1 wins over Q2
    for the ``qualifying`` times).
    """
    result: dict[str, str | None] = {}
    seen_prefixes: set[str] = set()

    for s in sessions:
        if not isinstance(s, dict):
            continue
        stype = str(s.get("type") or s.get("session_type") or "").strip().upper()
        prefix = _SESSION_TYPE_MAP.get(stype)
        if not prefix or prefix in seen_prefixes:
            continue
        seen_prefixes.add(prefix)

        raw_date = s.get("date") or s.get("date_start") or s.get("dateStart")
        dt = _parse_dt(raw_date)
        if dt is not None:
            result[f"{prefix}_start_local"] = _dt_to_local_str(dt)
            result[f"{prefix}_start_utc"] = _dt_to_utc_str(dt)
        else:
            result[f"{prefix}_start_local"] = None
            result[f"{prefix}_start_utc"] = None

    # Ensure all expected keys are present even when sessions are absent
    for prefix in ("first_practice", "second_practice", "third_practice", "qualifying", "race"):
        result.setdefault(f"{prefix}_start_local", None)
        result.setdefault(f"{prefix}_start_utc", None)

    return result


# ── Lap time helpers ──────────────────────────────────────────────────────────


def parse_lap_time(time_str: str | None) -> float | None:
    """Convert a lap time string to seconds.

    Supports: ``"1'19.459"``, ``"79.459"``, ``"1:19.459"``
    Returns None on failure.
    """
    if not time_str:
        return None
    t = str(time_str).strip().replace(",", ".")
    with suppress(Exception):
        if "'" in t:
            minutes_s, seconds_s = t.split("'", 1)
            return float(minutes_s) * 60 + float(seconds_s)
        if ":" in t:
            minutes_s, seconds_s = t.split(":", 1)
            return float(minutes_s) * 60 + float(seconds_s)
        return float(t.rstrip("s"))
    return None


def format_lap_time(seconds: float) -> str:
    """Convert a float number of seconds into ``"M'SS.mmm"`` format."""
    minutes = int(seconds // 60)
    remaining = seconds - minutes * 60
    return f"{minutes}'{remaining:06.3f}"


def compute_fastest_lap(riders: dict) -> tuple[str | None, str | None]:
    """Return (lap_time_str, racing_number) of the fastest last lap.

    Note: livetiming-lite only exposes the most recently completed lap, not the
    session best.  This is therefore an approximation.
    """
    best_secs: float | None = None
    best_time: str | None = None
    best_rn: str | None = None
    for rn, info in riders.items():
        if not isinstance(info, dict):
            continue
        timing = info.get("timing") or {}
        lap_str = str(timing.get("last_lap") or "").strip()
        if not lap_str or lap_str in ("-", "--", "n/a"):
            continue
        secs = parse_lap_time(lap_str)
        if secs is None:
            continue
        if best_secs is None or secs < best_secs:
            best_secs = secs
            best_time = lap_str
            best_rn = rn
    return best_time, best_rn


# ── Standings helpers ─────────────────────────────────────────────────────────


def standings_leader(standings: list[dict]) -> dict | None:
    """Return the standings entry with position == 1, or the first entry."""
    if not standings:
        return None
    for entry in standings:
        if isinstance(entry, dict) and entry.get("position") == 1:
            return entry
    return standings[0] if standings else None


# ── Circuit coordinates (lat, lon) for Open-Meteo weather lookups ─────────────
# Keyed by MotoGP event short_name (as returned by the Pulselive API)

CIRCUIT_COORDINATES: dict[str, tuple[float, float]] = {
    "THA": (14.9499, 103.0785),   # Chang International Circuit, Buriram
    "BRA": (-16.6829, -49.4054),  # Autódromo Internacional de Goiânia
    "USA": (30.1328, -97.6411),   # Circuit Of The Americas, Austin
    "SPA": (36.7083, -6.0319),    # Circuito de Jerez – Ángel Nieto
    "FRA": (47.9561, 0.2072),     # Le Mans
    "CAT": (41.5700, 2.2610),     # Circuit de Barcelona-Catalunya
    "ITA": (43.9977, 11.3719),    # Autodromo Internazionale del Mugello
    "HUN": (46.9562, 18.0832),    # Balaton Park Circuit
    "CZE": (49.1976, 16.4297),    # Automotodrom Brno
    "NED": (52.9625, 6.5250),     # TT Circuit Assen
    "GER": (50.7916, 12.6875),    # Sachsenring
    "GBR": (52.0786, -1.0169),    # Silverstone Circuit
    "ARA": (41.1237, -0.1672),    # MotorLand Aragón, Alcañiz
    "RSM": (43.9601, 12.6851),    # Misano World Circuit Marco Simoncelli
    "AUT": (47.2197, 14.7647),    # Red Bull Ring, Spielberg
    "JPN": (36.5368, 140.2138),   # Mobility Resort Motegi
    "INA": (-8.8986, 116.2921),   # Pertamina Mandalika Circuit, Lombok
    "AUS": (-38.5022, 145.2302),  # Phillip Island
    "MAL": (2.7609, 101.7382),    # Petronas Sepang International Circuit
    "QAT": (25.4897, 51.4531),    # Lusail International Circuit, Doha
    "POR": (37.2007, -8.5972),    # Autódromo Internacional do Algarve, Portimao
    "VAL": (39.4877, -0.6305),    # Circuit Ricardo Tormo, Cheste
    "ARG": (-38.7072, -62.2724),  # Autódromo de la Ciudad de Buenos Aires (historic)
    "AME": (30.1328, -97.6411),   # COTA alias
}


def circuit_coords(event: dict) -> tuple[float, float] | None:
    """Return (lat, lon) for a race event short_name, or None if unknown."""
    short = str(event.get("short_name") or event.get("shortName") or "").strip().upper()
    return CIRCUIT_COORDINATES.get(short)
