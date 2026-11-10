"""Opening hours of the business in scope, in its timezone. Local times here are naive datetimes in that timezone;
the database stores naive UTC."""
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from .tenancy import cfg

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def tz():
    return ZoneInfo(cfg().get("timezone", "UTC"))


def local_now() -> datetime:
    return datetime.now(tz()).replace(tzinfo=None, second=0, microsecond=0)


def to_utc(local: datetime) -> datetime:
    return local.replace(tzinfo=tz()).astimezone(timezone.utc).replace(tzinfo=None)


def to_local(utc: datetime) -> datetime:
    return utc.replace(tzinfo=timezone.utc).astimezone(tz()).replace(tzinfo=None)


def spec(day: date) -> str:
    return str(cfg()["hours"].get(DAYS[day.weekday()]) or "closed")


def periods(day: date) -> list[tuple[datetime, datetime]]:
    """Opening periods on a day: "07:00-15:00", "07:00-11:00, 17:00-21:00", or closed."""
    if spec(day).strip().lower() == "closed":
        return []
    out = []
    for part in spec(day).split(","):
        start, end = (time.fromisoformat(t.strip()) for t in part.split("-"))
        out.append((datetime.combine(day, start), datetime.combine(day, end)))
    return out


def is_open(at: datetime) -> bool:
    return any(start <= at <= end for start, end in periods(at.date()))


def label(at: datetime) -> str:
    """"Sat 12 Oct, 13:30": the weekday spelled out so nobody has to work it out."""
    return f"{at:%a} {at.day} {at:%b}, {at:%H:%M}"


def weekly() -> dict:
    return {d: spec(date(2024, 1, 1 + i)) for i, d in enumerate(DAYS)}  # 1 Jan 2024 was a Monday
