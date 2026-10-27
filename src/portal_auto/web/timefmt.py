"""Time display: stored datetimes are UTC, but SQLite hands them back naive."""
from datetime import datetime, timezone


def as_utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def format_utc(moment: datetime | None, empty: str = "") -> str:
    """The one timestamp format: 'YYYY-MM-DD HH:MM:SS UTC' (Jinja filter `utc`)."""
    return empty if moment is None else as_utc(moment).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_duration(started: datetime | None, finished: datetime | None) -> str:
    """Blank only when the Run never started; 'running…' while it has no finish time."""
    if started is None:
        return ""
    if finished is None:
        return "running…"
    seconds = max(0, int((as_utc(finished) - as_utc(started)).total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m {seconds}s" if minutes else f"{seconds}s"


def time_ago(moment: datetime, now: datetime | None = None) -> str:
    seconds = max(0, int(((now or datetime.now(timezone.utc)) - as_utc(moment)).total_seconds()))
    if seconds < 60:
        return "just now"
    for unit, size in (("day", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'s' if unit == 'day' and count != 1 else ''} ago"
    raise AssertionError("unreachable")
