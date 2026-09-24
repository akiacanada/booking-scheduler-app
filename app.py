import csv
import io
from datetime import date, datetime

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func

import analytics
from config import DOCK_DOORS, TARGET_DWELL_MINUTES, TIME_SLOTS
from models import LOAD_TYPES, STATUSES, Booking, db

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///bookings.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

with app.app_context():
    db.create_all()

PAGE_SIZE = 50


@app.template_filter("pct")
def pct(value, digits=0):
    return "—" if value is None else f"{value * 100:.{digits}f}%"


@app.template_filter("slot")
def slot_label(value):
    return TIME_SLOTS.get(value, value)


def taken_doors(booking_date, booking_time):
    rows = db.session.query(Booking.dock_door).filter(
        Booking.booking_date == booking_date,
        Booking.booking_time == booking_time,
        Booking.status != "Cancelled",
    )
    return {r.dock_door for r in rows}


def known_carriers():
    return [c for (c,) in db.session.query(Booking.carrier).distinct().order_by(Booking.carrier)]


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/book", methods=["GET", "POST"])
def book():
    form = request.form
    error = None

    if request.method == "POST":
        try:
            booking_date = date.fromisoformat(form.get("booking_date", ""))
            pallets = int(form.get("pallet_count", ""))
        except ValueError:
            booking_date, pallets = None, None
        booking_time = form.get("booking_time")

        if booking_date is None or pallets is None:
            error = "Please enter a valid date and pallet count."
        elif booking_date < date.today():
            error = "Appointments can't be booked in the past."
        elif booking_date.weekday() >= 5:
            error = "The dock is closed on weekends. Please choose a weekday."
        elif booking_time not in TIME_SLOTS:
            error = "Please choose a time slot."
        elif not 1 <= pallets <= 30:
            error = "Pallet count must be between 1 and 30."
        elif form.get("load_type") not in LOAD_TYPES:
            error = "Please choose a load type."
        else:
            free = sorted(set(range(1, DOCK_DOORS + 1)) - taken_doors(booking_date, booking_time))
            if not free:
                error = (f"All {DOCK_DOORS} dock doors are booked for {TIME_SLOTS[booking_time]} "
                         f"on {booking_date:%b %d}. Please choose another slot.")
            else:
                booking = Booking(
                    name=form["name"].strip(),
                    email=form["email"].strip(),
                    carrier=form["carrier"].strip(),
                    load_type=form["load_type"],
                    pallet_count=pallets,
                    dock_door=free[0],
                    booking_date=booking_date,
                    booking_time=booking_time,
                )
                db.session.add(booking)
                db.session.commit()
                return redirect(url_for("booking_detail", booking_id=booking.id))

    return render_template("book.html", time_slots=TIME_SLOTS, load_types=LOAD_TYPES,
                           carriers=known_carriers(), error=error, form=form,
                           today=date.today().isoformat())


@app.route("/api/availability")
def availability():
    try:
        booking_date = date.fromisoformat(request.args.get("date", ""))
    except ValueError:
        return jsonify({"error": "invalid date"}), 400
    rows = db.session.query(Booking.booking_time, func.count()).filter(
        Booking.booking_date == booking_date, Booking.status != "Cancelled"
    ).group_by(Booking.booking_time)
    used = dict(rows)
    return jsonify({slot: DOCK_DOORS - used.get(slot, 0) for slot in TIME_SLOTS})


@app.route("/booking/<int:booking_id>")
def booking_detail(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    return render_template("success.html", booking=booking)


def filtered_bookings():
    args = request.args
    query = Booking.query
    if args.get("status") in STATUSES:
        query = query.filter(Booking.status == args["status"])
    if args.get("carrier"):
        query = query.filter(Booking.carrier == args["carrier"])
    for key, op in (("date_from", "__ge__"), ("date_to", "__le__")):
        try:
            query = query.filter(getattr(Booking.booking_date, op)(date.fromisoformat(args.get(key, ""))))
        except ValueError:
            pass
    return query.order_by(Booking.booking_date.desc(), Booking.booking_time, Booking.dock_door)


@app.route("/admin")
def admin():
    page = request.args.get("page", 1, type=int)
    pagination = filtered_bookings().paginate(page=page, per_page=PAGE_SIZE, error_out=False)
    filters = {k: v for k, v in request.args.items() if k != "page" and v}
    return render_template("admin.html", pagination=pagination, statuses=STATUSES,
                           carriers=known_carriers(), filters=filters)


@app.route("/admin/<int:booking_id>/status", methods=["POST"])
def update_status(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    action = request.form.get("action")
    now = datetime.now().replace(second=0, microsecond=0)

    if action == "check_in" and booking.status == "Scheduled" and not booking.arrived_at:
        booking.arrived_at = now
    elif action == "check_out" and booking.status == "Scheduled" and booking.arrived_at:
        booking.departed_at = now
        booking.status = "Completed"
    elif action == "no_show" and booking.status == "Scheduled" and not booking.arrived_at:
        booking.status = "No-Show"
    elif action == "cancel" and booking.status == "Scheduled" and not booking.arrived_at:
        booking.status = "Cancelled"
    else:
        abort(400)

    db.session.commit()
    return redirect(request.form.get("next") or url_for("admin"))


@app.route("/admin/export.csv")
def export_csv():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "booking_date", "booking_time", "dock_door", "carrier", "load_type",
                     "pallet_count", "status", "contact_name", "contact_email", "created_at",
                     "arrived_at", "departed_at", "minutes_late", "dwell_minutes"])
    for b in filtered_bookings():
        writer.writerow([b.id, b.booking_date, b.booking_time, b.dock_door, b.carrier, b.load_type,
                         b.pallet_count, b.status, b.name, b.email, b.created_at, b.arrived_at,
                         b.departed_at, b.minutes_late, b.dwell_minutes])
    return Response(buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=dock_bookings.csv"})


@app.route("/dashboard")
def dashboard():
    period = request.args.get("period", 90, type=int)
    if period not in (30, 90, 180):
        period = 90
    data = analytics.dashboard_data(period)
    return render_template("dashboard.html", period=period, time_slots=TIME_SLOTS,
                           target_dwell=TARGET_DWELL_MINUTES, **data)


if __name__ == "__main__":
    app.run(debug=True)
