import sqlite3
import shutil
from pathlib import Path
from datetime import datetime


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "marketplace.db"

if not DB_PATH.exists():
    raise FileNotFoundError(
        f"Database not found: {DB_PATH}"
    )

# Create a separate backup without overwriting earlier backups.
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP_PATH = BASE_DIR / f"marketplace_backup_before_breakdown_{timestamp}.db"

shutil.copy2(DB_PATH, BACKUP_PATH)
print(f"Backup created: {BACKUP_PATH.name}")

new_columns = {
    "shipping_estimated_original_cost": "REAL",
    "shipping_estimated_currency": "VARCHAR(3)",
    "other_costs_original": "REAL",
    "other_costs_currency": "VARCHAR(3)",
    "product_cost_lsl": "REAL",
    "sourcing_fee_lsl": "REAL",
    "shipping_cost_lsl": "REAL",
    "other_costs_lsl": "REAL",
    "profit_amount_lsl": "REAL",
    "discount_amount_lsl": "REAL",
    "selling_price_lsl": "REAL",
}

connection = sqlite3.connect(DB_PATH)

try:
    cursor = connection.cursor()

    cursor.execute("PRAGMA table_info(quotation)")
    existing_columns = {
        row[1] for row in cursor.fetchall()
    }

    if not existing_columns:
        raise RuntimeError(
            "The quotation table was not found or has no columns."
        )

    added = []

    for column_name, column_type in new_columns.items():
        if column_name not in existing_columns:
            cursor.execute(
                f"ALTER TABLE quotation "
                f"ADD COLUMN {column_name} {column_type}"
            )
            added.append(column_name)

    connection.commit()

    print("\nMigration completed.")

    if added:
        print("New columns added:")
        for column in added:
            print(f"- {column}")
    else:
        print("All required columns already exist.")

    cursor.execute("SELECT COUNT(*) FROM quotation")
    quotation_count = cursor.fetchone()[0]

    print(f"\nExisting quotations preserved: {quotation_count}")

except Exception:
    connection.rollback()
    raise

finally:
    connection.close()