from flask import Flask, render_template, request
from models import db, Booking

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///bookings.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

TIME_SLOTS = [
    "8:00 AM",
    "9:00 AM",
    "10:00 AM",
    "11:00 AM",
    "1:00 PM",
    "2:00 PM",
    "3:00 PM",
    "4:00 PM"
]

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/book", methods=["GET", "POST"])
def book():
    error = None

    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        company = request.form.get("company")
        booking_date = request.form.get("booking_date")
        booking_time = request.form.get("booking_time")

        existing_booking = Booking.query.filter_by(
            booking_date=booking_date,
            booking_time=booking_time
        ).first()

        if existing_booking:
            error = "That date and time slot is already booked. Please choose another slot."
            return render_template("book.html", time_slots=TIME_SLOTS, error=error)

        new_booking = Booking(
            name=name,
            email=email,
            company=company,
            booking_date=booking_date,
            booking_time=booking_time
        )

        db.session.add(new_booking)
        db.session.commit()

        return render_template(
            "success.html",
            name=name,
            email=email,
            company=company,
            booking_date=booking_date,
            booking_time=booking_time
        )

    return render_template("book.html", time_slots=TIME_SLOTS, error=error)

@app.route("/admin")
def admin():
    bookings = Booking.query.order_by(Booking.created_at.desc()).all()
    return render_template("admin.html", bookings=bookings)

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)