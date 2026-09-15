"""Canonical trading session calendar for FARS.

Provides a unified session_date() function across backtest execution,
account simulations, and funded rules engines.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NY_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")


def session_date(
    ts: str | datetime | None,
    *,
    tz: str = "America/New_York",
    reset_hour: int | time = 17,
    skip_weekends: bool = True,
) -> date | None:
    """Determine the trading session date based on timezone, rollover hour, and weekend skipping.

    Parameters
    ----------
    ts : str | datetime | None
        Input timestamp. Can be an ISO-formatted string or datetime. If tz-naive,
        it is assumed to be UTC.
    tz : str
        IANA timezone name (default: "America/New_York").
    reset_hour : int | time
        Session rollover threshold (default: 17, i.e. 17:00 NY / 5:00 PM close).
        Can be an integer hour (0..23) or a datetime.time instance. Trades occurring
        at or after this boundary belong to the NEXT trading session.
    skip_weekends : bool
        If True (default), Saturday and Sunday sessions roll forward to Monday
        (canonical CME futures convention).

    Holiday Policy & Limitations:
    ----------------------------
    Official exchange holidays (e.g. New Year's Day, Martin Luther King Jr. Day,
    Presidents' Day, Good Friday, Memorial Day, Juneteenth, Independence Day,
    Labor Day, Thanksgiving, Christmas) often involve shortened trading hours
    or early halts (e.g. 13:00 ET).
    This function implements the standard causal CME session boundary rollover
    rule (17:00 NY + weekend shift). Full exchange holiday calendars
    (e.g., pandas_market_calendars / CME special schedules) are not hardcoded
    to avoid external dependency coupling and calendar drift. If trades occur on
    holidays, they map causally to that day's session (or next session after reset_hour)
    without lookahead bias.

    Returns
    -------
    date | None
        Calculated trading session date, or None if ts is None or empty.
    """
    if ts is None or ts == "":
        return None

    if isinstance(ts, str):
        cleaned = ts.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(cleaned)
        except ValueError:
            return date.fromisoformat(ts[:10])
    else:
        dt = ts

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC_TZ)

    try:
        target_tz = ZoneInfo(tz) if isinstance(tz, str) else tz
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {tz!r}") from exc

    dt_local = dt.astimezone(target_tz)

    if isinstance(reset_hour, time):
        boundary = reset_hour
        if dt_local.timetz().replace(tzinfo=None) >= boundary:
            s_date = dt_local.date() + timedelta(days=1)
        else:
            s_date = dt_local.date()
    else:
        if dt_local.hour >= reset_hour:
            s_date = dt_local.date() + timedelta(days=1)
        else:
            s_date = dt_local.date()

    if skip_weekends:
        if s_date.weekday() == 5:  # Saturday -> Monday
            s_date += timedelta(days=2)
        elif s_date.weekday() == 6:  # Sunday -> Monday
            s_date += timedelta(days=1)

    return s_date


__all__ = ["session_date"]
