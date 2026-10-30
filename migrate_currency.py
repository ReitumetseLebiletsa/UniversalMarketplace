import sqlite3

conn = sqlite3.connect("marketplace.db")
cursor = conn.cursor()

migrations = {
    "user": [
        ("country", "VARCHAR(100) NOT NULL DEFAULT 'Lesotho'"),
        ("preferred_currency", "VARCHAR(3) NOT NULL DEFAULT 'LSL'")
    ],
    "supplier": [
        ("default_currency", "VARCHAR(3) NOT NULL DEFAULT 'USD'")
    ],
    "quotation": [
        ("supplier_original_cost", "FLOAT"),
        ("supplier_original_currency", "VARCHAR(3)"),
        ("accounting_cost_lsl", "FLOAT"),
        ("exchange_rate_to_lsl", "FLOAT"),
        ("exchange_rate_date", "DATE"),
        ("exchange_rate_provider", "VARCHAR(100)")
    ],
    "pricing_setting": [
        ("accounting_currency", "VARCHAR(3) NOT NULL DEFAULT 'LSL'")
    ]
}

for table, columns in migrations.items():
    existing = [
        row[1] for row in cursor.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]

    for column, definition in columns:
        if column not in existing:
            cursor.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )
            print("Added:", table, column)
        else:
            print("Already exists:", table, column)

conn.commit()

print(
    "Accounting currency:",
    cursor.execute(
        "SELECT accounting_currency FROM pricing_setting"
    ).fetchone()
)

conn.close()
print("Migration completed.")