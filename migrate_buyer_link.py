import sqlite3
from pathlib import Path

database_path = Path(__file__).resolve().parent / "marketplace.db"

connection = sqlite3.connect(database_path)

try:
    connection.execute("PRAGMA foreign_keys = ON")

    columns = [
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(sourcing_request)"
        )
    ]

    if "buyer_user_id" not in columns:
        connection.execute("""
            ALTER TABLE sourcing_request
            ADD COLUMN buyer_user_id INTEGER
            REFERENCES user(id)
        """)
        print("Added buyer_user_id column.")
    else:
        print("buyer_user_id column already exists.")

    connection.commit()

    print("\nSourcing request columns:")
    for row in connection.execute(
        "PRAGMA table_info(sourcing_request)"
    ):
        print(row[1])

    print("\nExisting sourcing requests:")
    for row in connection.execute("""
        SELECT id, product_name, buyer_name, buyer_user_id
        FROM sourcing_request
    """):
        print(row)

    print("\nMigration completed successfully.")

except Exception:
    connection.rollback()
    raise

finally:
    connection.close()