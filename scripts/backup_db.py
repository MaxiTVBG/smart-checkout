#!/usr/bin/env python3
import shutil
import datetime
from pathlib import Path

def main():
    db_path = Path("data/inventory.db")
    if not db_path.exists():
        print(f"Database {db_path} does not exist.")
        return

    backup_dir = Path("data/backups")
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"inventory_{timestamp}.db"
    
    # We can use sqlite3 backup api to safely backup while WAL is active
    import sqlite3
    try:
        source = sqlite3.connect(db_path)
        dest = sqlite3.connect(backup_file)
        with source, dest:
            source.backup(dest)
        source.close()
        dest.close()
        print(f"Database backed up to {backup_file}")
    except Exception as e:
        print(f"Backup failed: {e}")
        return

    # Cleanup backups older than 30 days
    retention_days = 30
    cutoff = datetime.datetime.now() - datetime.timedelta(days=retention_days)
    
    for f in backup_dir.glob("inventory_*.db"):
        if f.is_file():
            # Parse datetime from filename
            name_parts = f.stem.split("_")
            if len(name_parts) >= 3:
                try:
                    dt_str = f"{name_parts[1]}_{name_parts[2]}"
                    file_dt = datetime.datetime.strptime(dt_str, "%Y%m%d_%H%M%S")
                    if file_dt < cutoff:
                        f.unlink()
                        print(f"Deleted old backup: {f}")
                except ValueError:
                    pass

if __name__ == "__main__":
    main()
