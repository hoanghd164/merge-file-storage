import sqlite3

db_path = "/var/tmp/merge_hash_cache.sqlite"

with sqlite3.connect(db_path) as conn:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM filehash LIMIT 10")
    for row in cur.fetchall():
        print(dict(row))