"""Fix audit script to skip non-commissioned accounts."""
PATH_COMMON = '/opt/cc-portal/backend/scripts/ops/cutover_ls_common.py'
PATH_AUDIT = '/opt/cc-portal/backend/scripts/ops/audit_ls_balances.py'

# 1. Add is_account_commissioned to cutover_ls_common.py
with open(PATH_COMMON) as f:
    common = f.read()

new_func = '''

def is_account_commissioned(conn, account_number: str) -> bool:
    """Check if the account's customer record shows it as commissioned."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT c.customer_commissioned
            FROM customers c
            JOIN accounts a ON a.customer_id = c.id
            WHERE a.account_number = %s
            """,
            (account_number,),
        )
        row = cur.fetchone()
        return bool(row and row[0])
    except Exception:
        return False
    finally:
        cur.close()
'''

common = common.replace('\ndef cutover_tag_for', new_func + '\ndef cutover_tag_for', 1)

with open(PATH_COMMON, 'w') as f:
    f.write(common)
print('1. Added is_account_commissioned to cutover_ls_common.py')

# 2. Update audit_ls_balances.py
with open(PATH_AUDIT) as f:
    audit = f.read()

# Update import
old_import = 'from cutover_ls_common import is_bulk_excluded_account  # noqa: E402'
new_import = 'from cutover_ls_common import is_bulk_excluded_account, is_account_commissioned  # noqa: E402'
audit = audit.replace(old_import, new_import, 1)
print('2a. Updated import')

# Add commission check in apply_seeds
old_apply = '''        if is_bulk_excluded_account(account):
            log.warning("Skipping invalid account code: %s", account)
            skipped += 1
            continue
        rate = float(get_tariff_rate_for_site(_site_code(account)) or 0)'''

new_apply = '''        if is_bulk_excluded_account(account):
            log.warning("Skipping invalid account code: %s", account)
            skipped += 1
            continue
        if not is_account_commissioned(conn, account):
            log.info("Skipping non-commissioned account: %s", account)
            skipped += 1
            continue
        rate = float(get_tariff_rate_for_site(_site_code(account)) or 0)'''

audit = audit.replace(old_apply, new_apply, 1)
print('2b. Added commission check to apply_seeds')

# Add commission check in --check reporting
old_check = 'abs(row[3]) >= threshold and not is_bulk_excluded_account(row[0])'
new_check = 'abs(row[3]) >= threshold and not is_bulk_excluded_account(row[0]) and is_account_commissioned(conn, row[0])'
audit = audit.replace(old_check, new_check, 1)
print('2c. Added commission check to --check reporting')

with open(PATH_AUDIT, 'w') as f:
    f.write(audit)
print('3. Done.')
