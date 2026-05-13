"""Backfill phone numbers from spreadsheet CSV into 1PDB customers table.

Rules:
  - If 1PDB has no valid phone (<8 digits): set cell_phone_1 = sheet phone
  - If 1PDB has a valid phone that matches sheet phone: skip (no change)
  - If 1PDB has a valid phone that DIFFERS from sheet: add sheet phone as
    alternate (cell_phone_2, then phone — whichever is free)
  - Flag suspicious sheet phones (concatenated/malformed) for review.
"""
import os
import sys

import psycopg2

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://cc_api@localhost:5432/onepower_cc",
)

CSV_PATH = "/tmp/spreadsheet_phones.csv"


def load_sheet_phones(path):
    """Return {account_number: phone_digits}."""
    phones = {}
    with open(path) as f:
        next(f)  # header
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) >= 2:
                acct = parts[0].strip()
                digits = "".join(c for c in parts[1] if c.isdigit())
                if acct and len(digits) >= 8:
                    phones[acct] = digits
    return phones


def is_suspicious(phone_digits):
    """Flag clearly corrupted numbers like concatenated pairs."""
    # Lesotho numbers are typically 11-12 digits (266 + 8 digits)
    # Benin numbers are typically 12-13 digits (229 + 8-9 digits)
    # Anything > 14 digits is likely concatenated
    if len(phone_digits) > 14:
        return True
    # Check for repeated patterns that look concatenated
    # e.g., "2666391084926663910849" — same number repeated
    half = len(phone_digits) // 2
    if phone_digits[:half] == phone_digits[half:]:
        return True
    return False


def main():
    do_apply = "--apply" in sys.argv
    sheet_phones = load_sheet_phones(CSV_PATH)
    print(f"Loaded {len(sheet_phones)} phones from spreadsheet")

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # Get all active-meter accounts with current phone state
    cur.execute("""
        SELECT a.account_number, c.id AS cust_id,
               NULLIF(TRIM(c.cell_phone_1), '') AS cp1,
               NULLIF(TRIM(c.phone), '') AS cp,
               NULLIF(TRIM(c.cell_phone_2), '') AS cp2,
               regexp_replace(
                   COALESCE(NULLIF(TRIM(c.cell_phone_1), ''),
                            NULLIF(TRIM(c.phone), ''),
                            NULLIF(TRIM(c.cell_phone_2), '')),
                   '[^0-9]', '', 'g'
               ) AS cur_phone_digits
        FROM accounts a
        JOIN customers c ON c.id = a.customer_id
        WHERE EXISTS (
            SELECT 1 FROM meters m
            WHERE m.account_number = a.account_number AND m.status = 'active'
        )
    """)
    rows = cur.fetchall()

    stats = {
        "set_cell_phone_1": 0,       # no phone → set primary
        "add_alternate": 0,          # has phone but different → add alt
        "already_matches": 0,       # phone same → skip
        "no_sheet_match": 0,        # not in spreadsheet
        "suspicious_flagged": 0,    # sheet phone looks corrupted
    }
    suspicious_list = []
    updates = []  # (cust_id, field, phone_value)
    alternates = []  # (cust_id, field, sheet_phone, cur_phone)

    for acct, cust_id, cp1, cp, cp2, cur_digits in rows:
        sheet_phone = sheet_phones.get(acct)

        if sheet_phone is None:
            stats["no_sheet_match"] += 1
            continue

        has_valid = len(cur_digits or "") >= 8

        # Check if sheet phone looks corrupted — skip entirely for safety
        if is_suspicious(sheet_phone):
            stats["suspicious_flagged"] += 1
            suspicious_list.append((acct, cust_id, cur_digits, sheet_phone))
            continue  # don't apply corrupted numbers

        if not has_valid:
            # Case A: No phone → set cell_phone_1 from sheet
            updates.append((cust_id, "cell_phone_1", sheet_phone))
            stats["set_cell_phone_1"] += 1
        elif cur_digits == sheet_phone:
            # Case B: Already matches → skip
            stats["already_matches"] += 1
        else:
            # Case C: Has phone but different → add as alternate
            # Find the next empty field: cell_phone_2, then phone
            if not cp2 or len("".join(c for c in cp2 if c.isdigit())) < 8:
                updates.append((cust_id, "cell_phone_2", sheet_phone))
                alternates.append((acct, "cell_phone_2", sheet_phone, cur_digits))
            elif not cp or len("".join(c for c in cp if c.isdigit())) < 8:
                updates.append((cust_id, "phone", sheet_phone))
                alternates.append((acct, "phone", sheet_phone, cur_digits))
            else:
                # All three fields occupied — overwrite cell_phone_2
                updates.append((cust_id, "cell_phone_2", sheet_phone))
                alternates.append((acct, "cell_phone_2 (overwrite)", sheet_phone, cur_digits))
            stats["add_alternate"] += 1

    # Print summary
    print(f"\n{'='*60}")
    print(f"BACKFILL PLAN")
    print(f"{'='*60}")
    print(f"  Set cell_phone_1 (no phone → sheet):  {stats['set_cell_phone_1']:>5}")
    print(f"  Add as alternate (different number):   {stats['add_alternate']:>5}")
    print(f"  Already matches (skip):                {stats['already_matches']:>5}")
    print(f"  Not in spreadsheet (no action):        {stats['no_sheet_match']:>5}")
    print(f"  Suspicious phone flagged for review:   {stats['suspicious_flagged']:>5}")
    print(f"  {'─' * 40}")
    print(f"  TOTAL actions:                         {len(updates):>5}")

    if alternates:
        print(f"\n{'='*60}")
        print(f"ALTERNATE ADDITIONS (has phone, sheet differs)")
        print(f"{'='*60}")
        for acct, field, sphone, cphone in alternates:
            print(f"  {acct}: 1PDB={cphone} → sheet={sphone} → {field}")

    if suspicious_list:
        print(f"\n{'='*60}")
        print(f"SUSPICIOUS PHONE NUMBERS (review recommended)")
        print(f"{'='*60}")
        for acct, cid, cur, sphone in suspicious_list:
            print(f"  {acct}: sheet={sphone} (len={len(sphone)}) cur={cur}")

    if do_apply:
        print(f"\n{'='*60}")
        print(f"APPLYING {len(updates)} UPDATES")
        print(f"{'='*60}")
        cur2 = conn.cursor()
        for cust_id, field, phone in updates:
            cur2.execute(
                f"UPDATE customers SET {field} = %s WHERE id = %s",
                (phone, cust_id),
            )
        conn.commit()
        cur2.close()
        print("Done. Updates committed.")
    else:
        print(f"\nRun with --apply to execute {len(updates)} updates.")

    if hasattr(cur, "close"):
        cur.close()
    conn.close()


if __name__ == "__main__":
    main()
