import sqlite3
from pathlib import Path

DB = Path('bot.sqlite3')

def connect(path=DB):
    conn=sqlite3.connect(path)
    conn.execute('''CREATE TABLE IF NOT EXISTS signals (
      id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
      fixture_id INTEGER, match_name TEXT, market TEXT, minute INTEGER,
      probability REAL, odds REAL, edge REAL, data_source TEXT,
      model_version TEXT, alerted INTEGER DEFAULT 0,
      outcome INTEGER, settled_at TEXT,
      UNIQUE(fixture_id,market,minute,data_source))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS snapshots (
      timestamp TEXT, fixture_id INTEGER, match_name TEXT, minute INTEGER,
      goals INTEGER, corners INTEGER, total_shots INTEGER, source TEXT)''')
    conn.commit()
    return conn

def record_signal(conn, values):
    cur=conn.execute('''INSERT OR IGNORE INTO signals
    (timestamp, fixture_id, match_name, market, minute, probability, odds, edge,
    data_source, model_version) VALUES (?,?,?,?,?,?,?,?,?,?)''',values)
    conn.commit()
    return cur.rowcount == 1
