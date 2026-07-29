from __future__ import annotations
from datetime import date, timedelta
from db.client import get_db


def get_savings_goal(user_id: str) -> dict | None:
    r = get_db().table("savings_goals").select("*").eq("user_id", user_id).execute()
    return r.data[0] if r.data else None


def set_savings_goal(user_id: str, daily_target: float) -> dict:
    """Upsert the user's daily savings target. Preserves started_at on update."""
    db = get_db()
    existing = get_savings_goal(user_id)
    if existing:
        r = (
            db.table("savings_goals")
            .update({"daily_target": daily_target, "updated_at": "now()"})
            .eq("user_id", user_id)
            .execute()
        )
        return r.data[0]
    r = db.table("savings_goals").insert({
        "user_id": user_id,
        "daily_target": daily_target,
        "started_at": date.today().isoformat(),
    }).execute()
    return r.data[0]


def log_saving(user_id: str, amount: float, log_date: date | None = None) -> dict:
    today = (log_date or date.today()).isoformat()
    r = get_db().table("savings_logs").insert({
        "user_id": user_id,
        "amount": amount,
        "log_date": today,
    }).execute()
    return r.data[0]


def _get_all_logs(user_id: str, start: date, end: date) -> dict[str, float]:
    """Return a dict of {date_str: total_amount} for each day in [start, end]."""
    r = (
        get_db().table("savings_logs")
        .select("amount, log_date")
        .eq("user_id", user_id)
        .gte("log_date", start.isoformat())
        .lte("log_date", end.isoformat())
        .execute()
    )
    totals: dict[str, float] = {}
    for row in r.data:
        d = row["log_date"]
        totals[d] = totals.get(d, 0.0) + float(row["amount"])
    return totals


def get_today_savings(user_id: str) -> float:
    logs = _get_all_logs(user_id, date.today(), date.today())
    return logs.get(date.today().isoformat(), 0.0)


def get_savings_stats(
    user_id: str,
    period_start: date | None = None,
    period_end: date | None = None,
) -> dict | None:
    """
    Compute savings stats for a date range. If no range given, uses full history
    since goal was set. Returns None if the user has no savings goal.
    """
    goal = get_savings_goal(user_id)
    if not goal:
        return None

    goal_started = date.fromisoformat(str(goal["started_at"]))
    today = date.today()

    # Bound the range by both the requested period and the goal start
    start = max(period_start or goal_started, goal_started)
    end = min(period_end or today, today)

    logs = _get_all_logs(user_id, start, end)

    # Generate every calendar date in range
    all_dates: list[str] = []
    d = start
    while d <= end:
        all_dates.append(d.isoformat())
        d += timedelta(days=1)

    days_saved = sum(1 for ds in all_dates if logs.get(ds, 0) > 0)
    days_missed = len(all_dates) - days_saved
    total_saved = sum(logs.get(ds, 0) for ds in all_dates)
    today_total = logs.get(today.isoformat(), 0.0)

    # Current streak: consecutive saved days ending at today
    current_streak = 0
    for ds in reversed(all_dates):
        if ds > today.isoformat():
            continue
        if logs.get(ds, 0) > 0:
            current_streak += 1
        else:
            break

    # Best streak with its date range
    best_streak = 0
    best_start: str | None = None
    best_end: str | None = None
    run = 0
    run_start: str | None = None
    for ds in all_dates:
        if logs.get(ds, 0) > 0:
            if run == 0:
                run_start = ds
            run += 1
            if run > best_streak:
                best_streak = run
                best_start = run_start
                best_end = ds
        else:
            run = 0
            run_start = None

    return {
        "goal": goal,
        "total_days": len(all_dates),
        "days_saved": days_saved,
        "days_missed": days_missed,
        "total_saved": total_saved,
        "today_total": today_total,
        "current_streak": current_streak,
        "best_streak": best_streak,
        "best_streak_start": best_start,
        "best_streak_end": best_end,
    }
