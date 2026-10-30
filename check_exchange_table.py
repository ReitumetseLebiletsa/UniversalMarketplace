import sqlite3

connection = sqlite3.connect("marketplace.db")

result = connection.execute(
    "SELECT name FROM sqlite_master "
    "WHERE type='table' "
    "AND name='quotation_exchange_rate'"
).fetchone()

print("Table found:", result)

connection.close()