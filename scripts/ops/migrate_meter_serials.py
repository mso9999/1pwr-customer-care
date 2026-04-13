#!/usr/bin/env python3
"""
Migrate remaining numeric meter_ids in hourly_consumption and other tables
to full ThunderCloud serial format. Handles meter reassignments within accounts.
"""
import os, sys, psycopg2

with open("/opt/1pdb/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k] = v

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

cur.execute(
    "SELECT account_number, meter_id FROM meters "
    "WHERE community = 'MAK' AND meter_id LIKE 'SMRS%%' AND role = 'primary'"
)
acct_to_serial = dict(cur.fetchall())
print("Account-serial map: %d entries" % len(acct_to_serial))

# ============== hourly_consumption ==============
print("\n=== hourly_consumption ===")
cur.execute(
    "SELECT DISTINCT account_number FROM hourly_consumption "
    "WHERE community = 'MAK' AND meter_id ~ '^[0-9]+$'"
)
hc_accounts = [r[0] for r in cur.fetchall()]
print("Accounts with numeric meter_ids: %d" % len(hc_accounts))

total_deleted = 0
total_updated = 0
total_skipped = 0

for i, acct in enumerate(hc_accounts):
    serial = acct_to_serial.get(acct)
    if not serial:
        cur.execute(
            "DELETE FROM hourly_consumption "
            "WHERE community = 'MAK' AND meter_id ~ '^[0-9]+$' AND account_number = %s",
            (acct,),
        )
        total_skipped += cur.rowcount
        continue

    # Step 1: Delete numeric rows where (serial, reading_hour) already exists
    cur.execute(
        "DELETE FROM hourly_consumption hc "
        "WHERE hc.community = 'MAK' AND hc.meter_id ~ '^[0-9]+$' "
        "AND hc.account_number = %s "
        "AND EXISTS ("
        "  SELECT 1 FROM hourly_consumption hc2 "
        "  WHERE hc2.meter_id = %s AND hc2.reading_hour = hc.reading_hour"
        ")",
        (acct, serial),
    )
    total_deleted += cur.rowcount

    # Step 2: Among remaining numeric rows, keep only one per reading_hour
    # (handles multiple old meters on same account with overlapping hours)
    cur.execute(
        "DELETE FROM hourly_consumption "
        "WHERE id IN ("
        "  SELECT id FROM ("
        "    SELECT id, ROW_NUMBER() OVER (PARTITION BY reading_hour ORDER BY id DESC) rn "
        "    FROM hourly_consumption "
        "    WHERE community = 'MAK' AND meter_id ~ '^[0-9]+$' AND account_number = %s"
        "  ) t WHERE rn > 1"
        ")",
        (acct,),
    )
    total_deleted += cur.rowcount

    # Step 3: Update remaining numeric rows to serial
    cur.execute(
        "UPDATE hourly_consumption "
        "SET meter_id = %s "
        "WHERE community = 'MAK' AND meter_id ~ '^[0-9]+$' AND account_number = %s",
        (serial, acct),
    )
    total_updated += cur.rowcount

    if (i + 1) % 50 == 0:
        conn.commit()
        print("  ... processed %d/%d accounts" % (i + 1, len(hc_accounts)))

conn.commit()
print("  Deleted (duplicates): %d" % total_deleted)
print("  Updated: %d" % total_updated)
print("  Skipped/cleaned (no serial): %d" % total_skipped)

# ============== monthly_consumption ==============
print("\n=== monthly_consumption ===")
cur.execute(
    "SELECT DISTINCT account_number FROM monthly_consumption "
    "WHERE account_number LIKE '%%MAK' AND meter_id ~ '^[0-9]+$'"
)
mc_accounts = [r[0] for r in cur.fetchall()]

mc_d = mc_u = 0
for acct in mc_accounts:
    serial = acct_to_serial.get(acct)
    if not serial:
        continue
    cur.execute(
        "DELETE FROM monthly_consumption mc "
        "WHERE mc.account_number = %s AND mc.meter_id ~ '^[0-9]+$' "
        "AND EXISTS ("
        "  SELECT 1 FROM monthly_consumption mc2 "
        "  WHERE mc2.meter_id = %s AND mc2.account_number = mc.account_number "
        "  AND mc2.year_month = mc.year_month AND mc2.source = mc.source"
        ")",
        (acct, serial),
    )
    mc_d += cur.rowcount

    cur.execute(
        "DELETE FROM monthly_consumption WHERE id IN ("
        "  SELECT id FROM ("
        "    SELECT id, ROW_NUMBER() OVER ("
        "      PARTITION BY account_number, year_month, source ORDER BY id DESC"
        "    ) rn FROM monthly_consumption "
        "    WHERE account_number = %s AND meter_id ~ '^[0-9]+$'"
        "  ) t WHERE rn > 1"
        ")",
        (acct,),
    )
    mc_d += cur.rowcount

    cur.execute(
        "UPDATE monthly_consumption SET meter_id = %s "
        "WHERE account_number = %s AND meter_id ~ '^[0-9]+$'",
        (serial, acct),
    )
    mc_u += cur.rowcount
conn.commit()
print("  Deleted: %d, Updated: %d" % (mc_d, mc_u))

# ============== meter_assignments ==============
print("\n=== meter_assignments ===")
cur.execute(
    "SELECT DISTINCT account_number FROM meter_assignments "
    "WHERE community = 'MAK' AND meter_id ~ '^[0-9]+$'"
)
ma_accounts = [r[0] for r in cur.fetchall()]

ma_d = ma_u = 0
for acct in ma_accounts:
    serial = acct_to_serial.get(acct)
    if not serial:
        continue
    cur.execute(
        "DELETE FROM meter_assignments "
        "WHERE community = 'MAK' AND account_number = %s AND meter_id ~ '^[0-9]+$' "
        "AND EXISTS ("
        "  SELECT 1 FROM meter_assignments ma2 "
        "  WHERE ma2.meter_id = %s AND ma2.account_number = %s"
        ")",
        (acct, serial, acct),
    )
    ma_d += cur.rowcount
    cur.execute(
        "UPDATE meter_assignments SET meter_id = %s "
        "WHERE community = 'MAK' AND account_number = %s AND meter_id ~ '^[0-9]+$'",
        (serial, acct),
    )
    ma_u += cur.rowcount
conn.commit()
print("  Deleted: %d, Updated: %d" % (ma_d, ma_u))

# ============== meter_readings ==============
print("\n=== meter_readings_2026 ===")
cur.execute(
    "SELECT DISTINCT account_number FROM meter_readings_2026 "
    "WHERE account_number LIKE '%%MAK' AND meter_id ~ '^[0-9]+$'"
)
for (acct,) in cur.fetchall():
    serial = acct_to_serial.get(acct)
    if serial:
        cur.execute(
            "UPDATE meter_readings_2026 SET meter_id = %s "
            "WHERE account_number = %s AND meter_id ~ '^[0-9]+$'",
            (serial, acct),
        )
conn.commit()
print("  Done")

# ============== VERIFICATION ==============
print("\n=== VERIFICATION ===")
for table, where in [
    ("hourly_consumption", "community = 'MAK' AND meter_id ~ '^[0-9]+$'"),
    ("monthly_consumption", "account_number LIKE '%%MAK' AND meter_id ~ '^[0-9]+$'"),
    ("meter_assignments", "community = 'MAK' AND meter_id ~ '^[0-9]+$'"),
    ("meters", "community = 'MAK' AND meter_id ~ '^[0-9]+$'"),
]:
    cur.execute("SELECT COUNT(*) FROM %s WHERE %s" % (table, where))
    print("  %s: %d numeric remaining" % (table, cur.fetchone()[0]))

cur.close()
conn.close()
print("\nDone!")
