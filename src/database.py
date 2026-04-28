import sqlite3
import datetime

class InventoryDatabase:
    def __init__(self, db_path):
        # check_same_thread=False е важно, ако четем от различни нишки, макар че тук работим главно в една.
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        c = self.conn.cursor()
        
        # -------------------------------------------------------------
        # ХАРДУЕРНА ОПТИМИЗАЦИЯ ЗА RASPBERRY PI (SD CARD WEAR LEVELING)
        # -------------------------------------------------------------
        # Активира Write-Ahead Logging (WAL). Изключително важно!
        # Вместо да презаписва целия файл при всяка промяна (като TinyDB),
        # SQLite добавя малки логове накрая и ги синхронизира асинхронно.
        # Това удължава живота на SD картата десетки пъти и е много бързо.
        c.execute('PRAGMA journal_mode=WAL;')
        c.execute('PRAGMA synchronous=NORMAL;')

        c.execute('''
            CREATE TABLE IF NOT EXISTS items (
                uid TEXT PRIMARY KEY,
                item_class TEXT,
                in_stock INTEGER
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT,
                action TEXT,
                timestamp DATETIME
            )
        ''')
        self.conn.commit()

    def get_inventory_state(self):
        """Връща речник с наличностите (напр. {'class_name': 5})."""
        c = self.conn.cursor()
        c.execute("SELECT item_class, COUNT(*) FROM items WHERE in_stock = 1 GROUP BY item_class")
        rows = c.fetchall()
        return {row[0]: row[1] for row in rows}

    def get_recent_logs(self, limit=5):
        """Връща последните N лога като форматирани низове за UI."""
        c = self.conn.cursor()
        c.execute("SELECT uid, action, timestamp FROM logs ORDER BY id DESC LIMIT ?", (limit,))
        rows = c.fetchall()
        recent = []
        
        # Обръщаме списъка, за да е в правилен хронологичен ред за UI-а
        for uid, action, ts in reversed(rows):
            # Парсване на датата (поддържа и 'T' и интервал като разделител)
            time_str = ts.split('.')[0].split('T')[1] if 'T' in ts else ts.split('.')[0].split(' ')[1]
            action_bg = "ВЛЕЗЕ" if action == "ADDED" else "ИЗЛЕЗЕ"
            recent.append({
                "text": f"[{time_str}] {action_bg}: {uid}",
                "action": action
            })
        return recent

    def check_item_status(self, uid):
        """Връща True ако предметът е вътре (in_stock=1), False ако е вън (0) и None ако не съществува."""
        c = self.conn.cursor()
        c.execute("SELECT in_stock FROM items WHERE uid = ?", (uid,))
        row = c.fetchone()
        if row is None:
            return None
        return row[0] == 1

    def log_action(self, uid, item_class, action):
        """Записва събитие и обновява наличността (UPSERT)."""
        c = self.conn.cursor()
        stock_status = 1 if action == 'ADDED' else 0
        
        # UPSERT логика (Вмъква ново или обновява съществуващо)
        c.execute('''
            INSERT INTO items (uid, item_class, in_stock) 
            VALUES (?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET in_stock = ?
        ''', (uid, item_class, stock_status, stock_status))
            
        c.execute('INSERT INTO logs (uid, action, timestamp) VALUES (?, ?, ?)', 
                  (uid, action, datetime.datetime.now().isoformat()))
        
        self.conn.commit()
        print(f"[{action}] UID: {uid} | Class: {item_class}")
        
    def close(self):
        self.conn.close()
