import sqlite3

DB_NAME = "inventory.db"

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Tárolók táblája (CB, DW, SE, OT)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS containers (
            id TEXT PRIMARY KEY,
            type_code TEXT NOT NULL,
            main_num INTEGER NOT NULL,
            sub_num INTEGER,
            description TEXT
        )
    ''')
    
    # Alkatrészek táblája
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            quantity INTEGER DEFAULT 1,
            container_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (container_id) REFERENCES containers (id)
        )
    ''')
    
    # Alapértelmezett minta tárolók, ha üres
    cursor.execute("SELECT COUNT(*) FROM containers")
    if cursor.fetchone()[0] == 0:
        default_containers = [
            ('DW1-1', 'DW', 1, 1, '1. Drawer - 1. Fiók'),
            ('DW1-2', 'DW', 1, 2, '1. Drawer - 2. Fiók'),
            ('DW1-3', 'DW', 1, 3, '1. Drawer - 3. Fiók'),
            ('CB1', 'CB', 1, None, '1. Kartondoboz'),
            ('SE1-1', 'SE', 1, 1, '1. Polcrendszer - Legfelső polc'),
            ('OT1', 'OT', 1, None, 'Asztal alatti rekesz')
        ]
        cursor.executemany("INSERT INTO containers VALUES (?, ?, ?, ?, ?)", default_containers)
        
    conn.commit()
    conn.close()