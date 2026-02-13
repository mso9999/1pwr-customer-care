"""
One-time migration script: Download existing customer contracts from Dropbox
into the EC2 contracts/ directory.

This is NOT a runtime dependency — run it once manually to populate historical
contracts so they appear in the customer detail page.

Usage:
    pip install dropbox
    DROPBOX_TOKEN=<your_token> python ingest_contracts.py

The script iterates over all known concessions, lists PDF files in their
Dropbox Service Agreements folder, and downloads them to contracts/{SITE_CODE}/.
"""

import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("ingest-contracts")

# ---------------------------------------------------------------------------
# Concession → Dropbox folder mapping (from existing contract generator)
# ---------------------------------------------------------------------------

CONCESSIONS = {
    "MAK": "0_0",
    "LEB": "0_1",
    "MAT": "0_2",
    "SEB": "0_3A",
    "TOS": "0_3B",
    "SEH": "0_4",
    "TLH": "0_5",
    "MAS": "0_6A",
    "SHG": "0_6B",
    "RIB": "0_7",
    "RAL": "0_9",
    "KET": "0_10",
    "SUA": "0_11",
    "LSB": "0_12",
}

CONTRACTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contracts")


def get_dropbox_folder(conc: str) -> str:
    """Build the Dropbox path for a concession's Service Agreements folder."""
    conc_number = CONCESSIONS[conc]
    base = f"/{conc_number} 1PWR {conc}/(0) 1PWR {conc} WBS/(1) Community Survey, Outreach, Recruitment/1.5. {conc}"
    return f"{base}/Service Agreements"


def main():
    token = os.environ.get("DROPBOX_TOKEN")
    if not token:
        logger.error("DROPBOX_TOKEN environment variable is required")
        sys.exit(1)

    try:
        import dropbox
    except ImportError:
        logger.error("dropbox package not installed. Run: pip install dropbox")
        sys.exit(1)

    dbx = dropbox.Dropbox(token)

    # Verify connection
    try:
        account = dbx.users_get_current_account()
        logger.info("Connected to Dropbox as: %s", account.name.display_name)
    except Exception as exc:
        logger.error("Failed to connect to Dropbox: %s", exc)
        sys.exit(1)

    total_downloaded = 0
    total_skipped = 0
    total_errors = 0

    for conc, conc_number in CONCESSIONS.items():
        folder_path = get_dropbox_folder(conc)
        local_dir = os.path.join(CONTRACTS_DIR, conc)
        os.makedirs(local_dir, exist_ok=True)

        logger.info("--- %s: %s ---", conc, folder_path)

        try:
            result = dbx.files_list_folder(folder_path)
        except dropbox.exceptions.ApiError as exc:
            logger.warning("  Could not list %s: %s", folder_path, exc)
            total_errors += 1
            continue

        entries = list(result.entries)
        # Handle pagination
        while result.has_more:
            result = dbx.files_list_folder_continue(result.cursor)
            entries.extend(result.entries)

        pdf_entries = [
            e for e in entries
            if isinstance(e, dropbox.files.FileMetadata)
            and e.name.lower().endswith(".pdf")
        ]

        logger.info("  Found %d PDF files", len(pdf_entries))

        for entry in pdf_entries:
            local_path = os.path.join(local_dir, entry.name)

            # Skip if already downloaded
            if os.path.isfile(local_path) and os.path.getsize(local_path) > 0:
                total_skipped += 1
                continue

            try:
                dbx.files_download_to_file(local_path, entry.path_lower)
                logger.info("  Downloaded: %s (%d KB)", entry.name, entry.size // 1024)
                total_downloaded += 1
            except Exception as exc:
                logger.error("  Failed to download %s: %s", entry.name, exc)
                total_errors += 1

    logger.info("=" * 60)
    logger.info("Ingestion complete:")
    logger.info("  Downloaded: %d", total_downloaded)
    logger.info("  Skipped (already exists): %d", total_skipped)
    logger.info("  Errors: %d", total_errors)
    logger.info("  Contracts directory: %s", CONTRACTS_DIR)


if __name__ == "__main__":
    main()
