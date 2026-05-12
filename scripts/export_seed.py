import sqlite3
import json
import os
import argparse

def export_seed(db_path, seed_path):
    print(f"Exporting registered codes from {db_path} to {seed_path}...")
    if not os.path.exists(db_path):
        print(f"Database {db_path} does not exist.")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("SELECT public_uid, payload, item_class, active, created_at FROM registered_codes")
    rows = c.fetchall()

    data = []
    for row in rows:
        data.append({
            "public_uid": row["public_uid"],
            "payload": row["payload"],
            "item_class": row["item_class"],
            "active": row["active"],
            "created_at": row["created_at"]
        })

    conn.close()

    os.makedirs(os.path.dirname(seed_path), exist_ok=True)
    with open(seed_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    
    print(f"Successfully exported {len(data)} codes to {seed_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export datamatrix codes to a seed JSON file.")
    parser.add_argument("--db", type=str, default="data/inventory.db", help="Path to SQLite database")
    parser.add_argument("--out", type=str, default="data/inventory_seed.json", help="Path to output JSON seed file")
    
    args = parser.parse_args()
    export_seed(args.db, args.out)
