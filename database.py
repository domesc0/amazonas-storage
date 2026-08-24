import sqlite3

DB_NAME = "inventory.db"

def get_db():
    conn = sqlite3.connect("inventory.db")
    conn.row_factory = sqlite3.Row
    # WAL mód bekapcsolása a gyorsabb párhuzamos elérésért
    conn.execute("PRAGMA journal_mode=WAL;")
    # Indexek használatának kikényszerítése külső kulcsoknál
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn