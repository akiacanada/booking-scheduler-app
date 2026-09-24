from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

STATUSES = ["Scheduled", "Completed", "No-Show", "Cancelled"]
LOAD_TYPES = ["Inbound", "Outbound"]


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    carrier = db.Column(db.String(120), nullable=False)
    load_type = db.Column(db.String(20), nullable=False, default="Inbound")
    pallet_count = db.Column(db.Integer, nullable=False, default=1)
    dock_door = db.Column(db.Integer, nullable=False)
    booking_date = db.Column(db.Date, nullable=False, index=True)
    booking_time = db.Column(db.String(5), nullable=False)  # "HH:MM", 24h
    status = db.Column(db.String(20), nullable=False, default="Scheduled", index=True)
    arrived_at = db.Column(db.DateTime, nullable=True)
    departed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    @property
    def scheduled_start(self):
        hour, minute = map(int, self.booking_time.split(":"))
        return datetime.combine(self.booking_date, datetime.min.time()).replace(hour=hour, minute=minute)

    @property
    def minutes_late(self):
        if not self.arrived_at:
            return None
        return round((self.arrived_at - self.scheduled_start).total_seconds() / 60)

    @property
    def dwell_minutes(self):
        if not (self.arrived_at and self.departed_at):
            return None
        return round((self.departed_at - self.arrived_at).total_seconds() / 60)

    def __repr__(self):
        return f"<Booking {self.carrier} - {self.booking_date} {self.booking_time} door {self.dock_door}>"
