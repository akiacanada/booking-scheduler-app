"""Generate realistic dock appointment history for analysis.

Rebuilds the database with ~6 months of past appointments plus two weeks of
upcoming ones. The data follows business rules an operations team would recognize:

- Carriers differ in reliability (on-time rate, no-show rate, how late they run).
- Mondays and Fridays are busiest; mornings fill before afternoons.
- The last three business days of each month see a volume surge.
- Volume grows ~15% over the period.
- Bookings made a day or less ahead are more likely to no-show.
- Turn time grows with pallet count, outbound loading, full slots, and afternoons.

Usage:  python seed_data.py
"""
import random
from datetime import date, datetime, timedelta

from app import app
from config import DOCK_DOORS, TIME_SLOTS
from models import Booking, db

HISTORY_DAYS = 180
FUTURE_DAYS = 14
BASE_UTILIZATION = 0.55

# name: (on_time_rate, no_show_rate, avg_minutes_late_when_late, share_of_volume)
CARRIERS = {
    "Great Lakes Freight": (0.92, 0.02, 20, 18),
    "Motor City Logistics": (0.85, 0.03, 25, 16),
    "Midwest Express": (0.78, 0.05, 30, 14),
    "Keystone Cartage": (0.88, 0.03, 20, 12),
    "Rapid Route Trucking": (0.62, 0.09, 45, 12),
    "Northstar Carriers": (0.90, 0.02, 20, 10),
    "Summit Transport": (0.80, 0.04, 30, 10),
    "Blue Water Hauling": (0.70, 0.07, 35, 8),
}

WEEKDAY_FACTOR = {0: 1.25, 1: 0.95, 2: 0.90, 3: 1.00, 4: 1.20}
SLOT_FACTOR = {
    "08:00": 1.20, "09:00": 1.25, "10:00": 1.10, "11:00": 0.95,
    "13:00": 0.85, "14:00": 0.90, "15:00": 0.80, "16:00": 0.60,
}

FIRST_NAMES = ["Jordan", "Maria", "DeShawn", "Emily", "Carlos", "Aisha", "Tom", "Priya",
               "Marcus", "Linda", "Kevin", "Tasha", "Omar", "Rachel", "Andre", "Nicole"]
LAST_NAMES = ["Johnson", "Garcia", "Williams", "Brown", "Lee", "Patel", "Davis", "Robinson",
              "Walker", "Harris", "Clark", "Lewis", "Young", "King", "Wright", "Scott"]


def business_days(start, end):
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def is_month_end_rush(day):
    """True for the last three business days of the month."""
    remaining, probe = 0, day + timedelta(days=1)
    while probe.month == day.month:
        if probe.weekday() < 5:
            remaining += 1
        probe += timedelta(days=1)
    return remaining < 3


def build_booking(rng, day, slot, door, doors_in_use, today):
    carrier = rng.choices(list(CARRIERS), weights=[c[3] for c in CARRIERS.values()])[0]
    on_time_rate, no_show_rate, late_mean, _ = CARRIERS[carrier]

    load_type = "Inbound" if rng.random() < 0.6 else "Outbound"
    pallets = rng.randint(8, 26) if load_type == "Inbound" else rng.randint(4, 20)

    lead_days = min(int(rng.expovariate(1 / 4)), 21)
    created_at = datetime.combine(day - timedelta(days=lead_days), datetime.min.time()) + timedelta(
        hours=rng.randint(7, 17), minutes=rng.randint(0, 59))

    first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
    domain = carrier.lower().replace(" ", "") + ".com"
    booking = Booking(
        name=f"{first} {last}",
        email=f"{first[0].lower()}{last.lower()}@{domain}",
        carrier=carrier,
        load_type=load_type,
        pallet_count=pallets,
        dock_door=door,
        booking_date=day,
        booking_time=slot,
        created_at=created_at,
        status="Scheduled",
    )

    if day >= today:
        if rng.random() < 0.04:
            booking.status = "Cancelled"
        return booking

    if rng.random() < (0.09 if lead_days >= 7 else 0.05):
        booking.status = "Cancelled"
        return booking

    if rng.random() < no_show_rate * (1.8 if lead_days <= 1 else 1.0):
        booking.status = "No-Show"
        return booking

    if rng.random() < on_time_rate:
        offset = rng.uniform(-20, 14)
    else:
        offset = 15 + rng.expovariate(1 / late_mean)
    arrived = booking.scheduled_start + timedelta(minutes=offset)

    dwell = 25 + pallets * 1.6 + (10 if load_type == "Outbound" else 0)
    if doors_in_use == DOCK_DOORS:
        dwell += 15  # every door busy: yard congestion and shared forklifts
    if slot >= "13:00":
        dwell += 12  # afternoon shift is thinner staffed
    dwell = max(15, dwell + rng.gauss(0, 8))

    booking.status = "Completed"
    booking.arrived_at = arrived.replace(second=0, microsecond=0)
    booking.departed_at = (arrived + timedelta(minutes=dwell)).replace(second=0, microsecond=0)
    return booking


def generate(seed=42):
    rng = random.Random(seed)
    today = date.today()
    start = today - timedelta(days=HISTORY_DAYS)
    end = today + timedelta(days=FUTURE_DAYS)
    bookings = []

    for day in business_days(start, end):
        growth = 1 + 0.15 * (day - start).days / HISTORY_DAYS
        rush = 1.3 if is_month_end_rush(day) else 1.0
        # Upcoming days are only partly booked so far
        fill = 1.0 if day < today else max(0.25, 1 - (day - today).days / FUTURE_DAYS)
        for slot in TIME_SLOTS:
            p = BASE_UTILIZATION * WEEKDAY_FACTOR[day.weekday()] * SLOT_FACTOR[slot] * growth * rush * fill
            p = min(p, 0.97)
            n = sum(rng.random() < p for _ in range(DOCK_DOORS))
            for door in range(1, n + 1):
                bookings.append(build_booking(rng, day, slot, door, n, today))
    return bookings


if __name__ == "__main__":
    with app.app_context():
        db.drop_all()
        db.create_all()
        rows = generate()
        db.session.add_all(rows)
        db.session.commit()
        print(f"Seeded {len(rows):,} bookings")
