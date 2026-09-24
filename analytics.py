"""KPI and analysis queries for the dock operations dashboard.

Every metric is computed in SQL against the booking table. Definitions:

- Utilization      = non-cancelled appointments / (business days x slots x doors)
- No-show rate     = no-shows / (completed + no-shows)
- Cancellation rate = cancelled / all appointments
- On-time rate     = completed arrivals within ON_TIME_GRACE_MINUTES of the slot start
- Dwell time       = minutes from arrival to departure (completed appointments)
- Lead time        = days between when the appointment was made and the appointment date
"""
from datetime import date, timedelta

from sqlalchemy import text

from config import DOCK_DOORS, ON_TIME_GRACE_MINUTES, TARGET_DWELL_MINUTES, TIME_SLOTS
from models import db

# Reusable SQL fragments
SCHEDULED_AT = "(booking_date || ' ' || booking_time)"
MINUTES_LATE = f"((julianday(arrived_at) - julianday({SCHEDULED_AT})) * 1440)"
DWELL = "((julianday(departed_at) - julianday(arrived_at)) * 1440)"
LEAD_DAYS = "(julianday(booking_date) - julianday(date(created_at)))"

DOW_LABELS = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri"}


def _rows(sql, **params):
    params = {k: v.isoformat() if isinstance(v, date) else v for k, v in params.items()}
    return [dict(r._mapping) for r in db.session.execute(text(sql), params)]


def _weekday_counts(start, end):
    """How many of each weekday (SQLite %w numbering, Mon=1) fall in the range."""
    counts = {d: 0 for d in DOW_LABELS}
    day = start
    while day <= end:
        if day.weekday() < 5:
            counts[day.weekday() + 1] += 1
        day += timedelta(days=1)
    return counts


def _rate(num, den):
    return (num / den) if den else None


def kpis(start, end):
    row = _rows(f"""
        SELECT
            COUNT(*)                                         AS total,
            SUM(status != 'Cancelled')                       AS active,
            SUM(status = 'Completed')                        AS completed,
            SUM(status = 'No-Show')                          AS no_show,
            SUM(status = 'Cancelled')                        AS cancelled,
            AVG(CASE WHEN status = 'Completed'
                     THEN {MINUTES_LATE} <= :grace END)      AS on_time_rate,
            AVG(CASE WHEN status = 'Completed' THEN {DWELL} END) AS avg_dwell,
            SUM(CASE WHEN status = 'Completed' THEN pallet_count END) AS pallets
        FROM booking
        WHERE booking_date BETWEEN :start AND :end
    """, start=start, end=end, grace=ON_TIME_GRACE_MINUTES)[0]

    capacity = sum(_weekday_counts(start, end).values()) * len(TIME_SLOTS) * DOCK_DOORS
    active = row["active"] or 0
    return {
        "total": row["total"] or 0,
        "active": active,
        "completed": row["completed"] or 0,
        "utilization": _rate(active, capacity),
        "no_show_rate": _rate(row["no_show"] or 0, (row["completed"] or 0) + (row["no_show"] or 0)),
        "cancel_rate": _rate(row["cancelled"] or 0, row["total"] or 0),
        "on_time_rate": row["on_time_rate"],
        "avg_dwell": row["avg_dwell"],
        "pallets": row["pallets"] or 0,
    }


def weekly_volume(start, end):
    """Weekly outcomes, keeping only full Mon-Fri weeks so edge weeks don't look like drops."""
    rows = _rows("""
        SELECT
            date(booking_date, '-6 days', 'weekday 1') AS week_start,
            SUM(status = 'Completed')                  AS completed,
            SUM(status = 'No-Show')                    AS no_show,
            SUM(status = 'Cancelled')                  AS cancelled
        FROM booking
        WHERE booking_date BETWEEN :start AND :end
        GROUP BY week_start
        ORDER BY week_start
    """, start=start, end=end)
    return [r for r in rows
            if date.fromisoformat(r["week_start"]) >= start
            and date.fromisoformat(r["week_start"]) + timedelta(days=4) <= end]


def utilization_heatmap(start, end):
    """Utilization for each weekday x slot cell."""
    rows = _rows("""
        SELECT CAST(strftime('%w', booking_date) AS INTEGER) AS dow,
               booking_time,
               SUM(status != 'Cancelled') AS booked
        FROM booking
        WHERE booking_date BETWEEN :start AND :end
        GROUP BY dow, booking_time
    """, start=start, end=end)
    weekdays = _weekday_counts(start, end)
    booked = {(r["dow"], r["booking_time"]): r["booked"] for r in rows}
    grid = []
    for dow, label in DOW_LABELS.items():
        cells = []
        for slot in TIME_SLOTS:
            cap = weekdays[dow] * DOCK_DOORS
            cells.append(_rate(booked.get((dow, slot), 0), cap))
        grid.append({"day": label, "cells": cells})
    return grid


def carrier_scorecard(start, end):
    rows = _rows(f"""
        SELECT
            carrier,
            COUNT(*)                                          AS booked,
            SUM(status = 'Completed')                         AS completed,
            SUM(status = 'No-Show')                           AS no_show,
            SUM(status = 'Cancelled')                         AS cancelled,
            AVG(CASE WHEN status = 'Completed'
                     THEN {MINUTES_LATE} <= :grace END)       AS on_time_rate,
            AVG(CASE WHEN status = 'Completed' AND {MINUTES_LATE} > :grace
                     THEN {MINUTES_LATE} END)                 AS avg_minutes_late,
            AVG(CASE WHEN status = 'Completed' THEN {DWELL} END) AS avg_dwell
        FROM booking
        WHERE booking_date BETWEEN :start AND :end
        GROUP BY carrier
        ORDER BY booked DESC
    """, start=start, end=end, grace=ON_TIME_GRACE_MINUTES)
    for r in rows:
        r["no_show_rate"] = _rate(r["no_show"], r["completed"] + r["no_show"])
    return rows


def dwell_by_slot(start, end):
    rows = _rows(f"""
        SELECT booking_time, AVG({DWELL}) AS avg_dwell, COUNT(*) AS n
        FROM booking
        WHERE status = 'Completed' AND booking_date BETWEEN :start AND :end
        GROUP BY booking_time
        ORDER BY booking_time
    """, start=start, end=end)
    return [{"slot": TIME_SLOTS[r["booking_time"]], "avg_dwell": r["avg_dwell"], "n": r["n"]} for r in rows]


def no_show_by_lead_time(start, end):
    return _rows(f"""
        SELECT
            CASE WHEN {LEAD_DAYS} <= 1 THEN '0-1 days'
                 WHEN {LEAD_DAYS} <= 3 THEN '2-3 days'
                 WHEN {LEAD_DAYS} <= 7 THEN '4-7 days'
                 ELSE '8+ days' END                   AS bucket,
            MIN({LEAD_DAYS})                          AS sort_key,
            COUNT(*)                                  AS n,
            AVG(status = 'No-Show')                   AS no_show_rate
        FROM booking
        WHERE status IN ('Completed', 'No-Show') AND booking_date BETWEEN :start AND :end
        GROUP BY bucket
        ORDER BY sort_key
    """, start=start, end=end)


def upcoming(today, days=14):
    end = today + timedelta(days=days - 1)
    row = _rows("""
        SELECT COUNT(*) AS booked, SUM(pallet_count) AS pallets
        FROM booking
        WHERE status = 'Scheduled' AND booking_date BETWEEN :start AND :end
    """, start=today, end=end)[0]
    capacity = sum(_weekday_counts(today, end).values()) * len(TIME_SLOTS) * DOCK_DOORS
    return {"booked": row["booked"] or 0, "pallets": row["pallets"] or 0,
            "utilization": _rate(row["booked"] or 0, capacity), "days": days}


def insights(heatmap, carriers, dwell, lead, kpi):
    """Turn the numbers into plain-language findings and recommendations."""
    findings = []

    peak = max(((row["day"], label, u) for row in heatmap
                for label, u in zip(TIME_SLOTS.values(), row["cells"]) if u is not None),
               key=lambda x: x[2], default=None)
    low = min(((row["day"], label, u) for row in heatmap
               for label, u in zip(TIME_SLOTS.values(), row["cells"]) if u is not None),
              key=lambda x: x[2], default=None)
    if peak and low:
        findings.append({
            "title": "Demand is concentrated in a few windows",
            "body": f"{peak[0]} {peak[1]} runs at {peak[2]:.0%} door utilization while "
                    f"{low[0]} {low[1]} sits at {low[2]:.0%}. Offering incentives or default "
                    f"suggestions for off-peak slots would smooth the load.",
        })

    scored = [c for c in carriers if c["completed"] >= 20 and c["on_time_rate"] is not None]
    if scored:
        worst = min(scored, key=lambda c: c["on_time_rate"])
        best = max(scored, key=lambda c: c["on_time_rate"])
        findings.append({
            "title": f"{worst['carrier']} is the least reliable carrier",
            "body": f"On time for {worst['on_time_rate']:.0%} of arrivals (vs {best['on_time_rate']:.0%} "
                    f"for {best['carrier']}) with a {worst['no_show_rate']:.1%} no-show rate. "
                    f"When late, they arrive {worst['avg_minutes_late'] or 0:.0f} min behind schedule on average. "
                    f"Candidate for a carrier performance review.",
        })

    if len(lead) >= 2 and lead[0]["no_show_rate"] and lead[-1]["no_show_rate"]:
        ratio = lead[0]["no_show_rate"] / lead[-1]["no_show_rate"]
        findings.append({
            "title": "Last-minute bookings no-show more often",
            "body": f"Appointments made {lead[0]['bucket']} ahead no-show at {lead[0]['no_show_rate']:.1%}, "
                    f"{ratio:.1f}x the rate for bookings made {lead[-1]['bucket']} ahead. "
                    f"A confirmation reminder for short-notice bookings could recover those slots.",
        })

    am = [d["avg_dwell"] for d in dwell if d["slot"].endswith("AM")]
    pm = [d["avg_dwell"] for d in dwell if d["slot"].endswith("PM")]
    if am and pm:
        am_avg, pm_avg = sum(am) / len(am), sum(pm) / len(pm)
        findings.append({
            "title": "Afternoon turns are slower",
            "body": f"Average dwell is {pm_avg:.0f} min in the afternoon vs {am_avg:.0f} min in the "
                    f"morning (target: {TARGET_DWELL_MINUTES} min). Review afternoon staffing "
                    f"and forklift coverage.",
        })

    return findings


def dashboard_data(period_days):
    today = date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=period_days - 1)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=period_days - 1)

    heatmap = utilization_heatmap(start, end)
    carriers = carrier_scorecard(start, end)
    dwell = dwell_by_slot(start, end)
    lead = no_show_by_lead_time(start, end)
    current = kpis(start, end)
    return {
        "start": start,
        "end": end,
        "kpis": current,
        "prev_kpis": kpis(prev_start, prev_end),
        "weekly": weekly_volume(start, end),
        "heatmap": heatmap,
        "carriers": carriers,
        "dwell": dwell,
        "lead": lead,
        "upcoming": upcoming(today),
        "insights": insights(heatmap, carriers, dwell, lead, current),
    }
