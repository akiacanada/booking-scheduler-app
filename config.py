"""Business rules shared by the app, the analytics layer, and the data generator."""

# Appointment slots offered each weekday (stored as 24h "HH:MM", shown as labels)
TIME_SLOTS = {
    "08:00": "8:00 AM",
    "09:00": "9:00 AM",
    "10:00": "10:00 AM",
    "11:00": "11:00 AM",
    "13:00": "1:00 PM",
    "14:00": "2:00 PM",
    "15:00": "3:00 PM",
    "16:00": "4:00 PM",
}

# Number of dock doors that can each take one appointment per slot
DOCK_DOORS = 4

# A truck counts as on time if it arrives no later than this many minutes after its slot
ON_TIME_GRACE_MINUTES = 15

# Target turn time (arrival to departure) used to flag slow unloads
TARGET_DWELL_MINUTES = 60
