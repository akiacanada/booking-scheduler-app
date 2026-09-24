-- Dock Operations Analysis
-- Run against instance/bookings.db after `python seed_data.py`:
--   sqlite3 -header -column instance/bookings.db < sql/analysis_queries.sql
--
-- Each query answers one business question. The analysis window is the last
-- 90 days of completed history (yesterday and earlier).
-- Business rules: 8 slots/day, 4 dock doors, on time = arrived <= 15 min after slot start.


-- Q1. How are we doing overall? (headline KPIs)
SELECT
    COUNT(*)                                                         AS appointments,
    ROUND(100.0 * SUM(status != 'Cancelled') / (
        -- capacity: weekdays in window x 8 slots x 4 doors
        (SELECT COUNT(*) FROM (
            WITH RECURSIVE d(day) AS (
                SELECT date('now', '-90 days') UNION ALL
                SELECT date(day, '+1 day') FROM d WHERE day < date('now', '-1 day'))
            SELECT day FROM d WHERE strftime('%w', day) BETWEEN '1' AND '5')) * 8 * 4), 1)
                                                                     AS utilization_pct,
    ROUND(100.0 * SUM(status = 'No-Show')
          / SUM(status IN ('Completed', 'No-Show')), 1)              AS no_show_pct,
    ROUND(100.0 * AVG(status = 'Cancelled'), 1)                      AS cancel_pct,
    ROUND(100.0 * AVG(CASE WHEN status = 'Completed' THEN
          (julianday(arrived_at) - julianday(booking_date || ' ' || booking_time)) * 1440 <= 15
          END), 1)                                                   AS on_time_pct,
    ROUND(AVG(CASE WHEN status = 'Completed' THEN
          (julianday(departed_at) - julianday(arrived_at)) * 1440 END), 1) AS avg_dwell_min
FROM booking
WHERE booking_date BETWEEN date('now', '-90 days') AND date('now', '-1 day');


-- Q2. Which carriers are hurting dock performance?
-- Ranks carriers by on-time rate and flags anyone under 80% on time or over 5% no-show.
WITH carrier_stats AS (
    SELECT
        carrier,
        COUNT(*)                                            AS booked,
        SUM(status = 'Completed')                           AS completed,
        SUM(status = 'No-Show')                             AS no_shows,
        AVG(CASE WHEN status = 'Completed' THEN
            (julianday(arrived_at) - julianday(booking_date || ' ' || booking_time)) * 1440 <= 15
            END)                                            AS on_time_rate
    FROM booking
    WHERE booking_date BETWEEN date('now', '-90 days') AND date('now', '-1 day')
    GROUP BY carrier
)
SELECT
    carrier,
    booked,
    ROUND(100.0 * on_time_rate, 1)                          AS on_time_pct,
    ROUND(100.0 * no_shows / (completed + no_shows), 1)     AS no_show_pct,
    RANK() OVER (ORDER BY on_time_rate DESC)                AS on_time_rank,
    CASE WHEN on_time_rate < 0.80 OR 1.0 * no_shows / (completed + no_shows) > 0.05
         THEN 'Review' ELSE 'OK' END                        AS action
FROM carrier_stats
ORDER BY on_time_rank;


-- Q3. Where are the capacity peaks and gaps? (utilization by weekday x slot)
WITH weekday_counts AS (
    WITH RECURSIVE d(day) AS (
        SELECT date('now', '-90 days') UNION ALL
        SELECT date(day, '+1 day') FROM d WHERE day < date('now', '-1 day'))
    SELECT strftime('%w', day) AS dow, COUNT(*) AS n_days
    FROM d GROUP BY dow
)
SELECT
    CASE b.dow WHEN '1' THEN 'Mon' WHEN '2' THEN 'Tue' WHEN '3' THEN 'Wed'
               WHEN '4' THEN 'Thu' WHEN '5' THEN 'Fri' END AS weekday,
    b.booking_time                                          AS slot,
    b.booked,
    ROUND(100.0 * b.booked / (w.n_days * 4), 1)             AS utilization_pct
FROM (
    SELECT strftime('%w', booking_date) AS dow, booking_time, SUM(status != 'Cancelled') AS booked
    FROM booking
    WHERE booking_date BETWEEN date('now', '-90 days') AND date('now', '-1 day')
    GROUP BY dow, booking_time
) b
JOIN weekday_counts w ON w.dow = b.dow
ORDER BY utilization_pct DESC;


-- Q4. Do short-notice bookings no-show more?
SELECT
    CASE WHEN lead_days <= 1 THEN '0-1 days'
         WHEN lead_days <= 3 THEN '2-3 days'
         WHEN lead_days <= 7 THEN '4-7 days'
         ELSE '8+ days' END                                 AS booked_ahead,
    COUNT(*)                                                AS appointments,
    ROUND(100.0 * AVG(status = 'No-Show'), 1)               AS no_show_pct
FROM (
    SELECT status, julianday(booking_date) - julianday(date(created_at)) AS lead_days
    FROM booking
    WHERE status IN ('Completed', 'No-Show')
      AND booking_date BETWEEN date('now', '-90 days') AND date('now', '-1 day')
)
GROUP BY booked_ahead
ORDER BY MIN(lead_days);


-- Q5. What drives slow turn times? (dwell by shift, load type, and slot congestion)
WITH completed AS (
    SELECT
        b.*,
        (julianday(departed_at) - julianday(arrived_at)) * 1440 AS dwell,
        CASE WHEN booking_time < '12:00' THEN 'AM' ELSE 'PM' END AS shift,
        (SELECT COUNT(*) FROM booking o
          WHERE o.booking_date = b.booking_date AND o.booking_time = b.booking_time
            AND o.status != 'Cancelled')                        AS doors_in_use
    FROM booking b
    WHERE status = 'Completed'
      AND booking_date BETWEEN date('now', '-90 days') AND date('now', '-1 day')
)
SELECT
    shift,
    load_type,
    CASE WHEN doors_in_use = 4 THEN 'Full slot' ELSE 'Open doors' END AS congestion,
    COUNT(*)                                                AS appointments,
    ROUND(AVG(pallet_count), 1)                             AS avg_pallets,
    ROUND(AVG(dwell), 1)                                    AS avg_dwell_min,
    ROUND(100.0 * AVG(dwell > 60), 1)                       AS pct_over_target
FROM completed
GROUP BY shift, load_type, congestion
ORDER BY avg_dwell_min DESC;


-- Q6. Is volume growing? Month over month, per business day (months differ in length).
-- Only complete months are included.
SELECT
    month,
    appointments,
    business_days,
    ROUND(per_day, 1)                                       AS per_day,
    ROUND(100.0 * (per_day - LAG(per_day) OVER (ORDER BY month))
          / LAG(per_day) OVER (ORDER BY month), 1)          AS mom_change_pct
FROM (
    SELECT strftime('%Y-%m', booking_date)                  AS month,
           SUM(status != 'Cancelled')                       AS appointments,
           COUNT(DISTINCT booking_date)                     AS business_days,
           1.0 * SUM(status != 'Cancelled') / COUNT(DISTINCT booking_date) AS per_day
    FROM booking
    WHERE booking_date >= date((SELECT MIN(booking_date) FROM booking), 'start of month', '+1 month')
      AND booking_date < date('now', 'start of month')
    GROUP BY month
)
ORDER BY month;


-- Q7. Does the month-end rush exist? Average daily appointments, last 3 business days vs rest.
WITH daily AS (
    SELECT booking_date, SUM(status != 'Cancelled') AS appointments,
           -- count of business days left in the month after this one
           (WITH RECURSIVE d(day) AS (
                SELECT date(booking_date, '+1 day') UNION ALL
                SELECT date(day, '+1 day') FROM d
                WHERE day < date(booking_date, 'start of month', '+1 month', '-1 day'))
            SELECT COUNT(*) FROM d
            WHERE strftime('%w', day) BETWEEN '1' AND '5'
              AND day <= date(booking_date, 'start of month', '+1 month', '-1 day')) AS days_left
    FROM booking
    WHERE booking_date < date('now')
    GROUP BY booking_date
)
SELECT
    CASE WHEN days_left < 3 THEN 'Month-end (last 3 business days)' ELSE 'Rest of month' END AS period,
    COUNT(*)                                                AS days,
    ROUND(AVG(appointments), 1)                             AS avg_daily_appointments
FROM daily
GROUP BY period;
