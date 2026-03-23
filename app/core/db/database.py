import sqlite3


class Database:

    def __init__(self, path="robot.db"):

        self.conn = sqlite3.connect(path)

        self._create()

    def _create(self):

        c = self.conn.cursor()

        c.execute("""
        CREATE TABLE IF NOT EXISTS trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT,
            price REAL,
            strategy TEXT,
            time TEXT
        )
        """)

        self.conn.commit()

    def insert_trade(self, pair, price, strategy, time):

        c = self.conn.cursor()

        c.execute(
            "INSERT INTO trades(pair,price,strategy,time) VALUES(?,?,?,?)",
            (pair, price, strategy, time)
        )

        self.conn.commit()

    def trades(self):

        c = self.conn.cursor()

        return c.execute("SELECT * FROM trades").fetchall()
