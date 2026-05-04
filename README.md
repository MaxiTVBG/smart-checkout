# Smart Checkout Tracker

Professional Computer Vision system for inventory management using YOLO, ByteTrack, DataMatrix/QR codes, and NoSQL Database.

## 📋 Features
- **Real-Time Object Tracking**: Uses Ultralytics YOLO & ByteTrack.
- **Secure DataMatrix Support**: HMAC-signed Data Matrix payloads with local registry validation.
- **Anti-Spoofing Validations**: Ensures visually detected classes match registered signed codes.
- **SQLite Inventory DB**: Uses SQLite WAL for stock state, movement logs, and registered labels.
- **Hysteresis Line Crossing**: Robust mechanism to prevent jitter and track IN/OUT movements.
- **OpenCV Dashboard**: Real-time overlay showing stock status and recent activity logs.

## 🛠️ Project Structure
```
smart-checkout/
├── config.yaml          # System Configuration
├── main.py              # Main execution script
├── requirements.txt     # Python dependencies
├── data/                # Database files (inventory.db)
├── models/              # YOLO models (.pt)
├── scripts/             # Utility scripts (training, data capture)
└── src/                 # Core logic
    ├── database.py      # SQLite logic
    ├── scanner.py       # QR & DataMatrix scanning logic
    └── ui.py            # OpenCV UI drawing
```

## 🚀 Installation

1. Install system dependencies (macOS):
```bash
brew install zbar libdmtx
```
2. Create a virtual environment and install Python packages:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
*(If you are on an Apple Silicon Mac, you may need to symlink `libzbar.dylib` and `libdmtx.dylib` inside `~/lib`)*

## ⚙️ Configuration
Edit `config.yaml` to change the camera index, paths to YOLO models, scanning cooldowns, or the signing-secret environment variable name.

Set a strong signing secret before generating labels or running the tracker:
```bash
export SMART_CHECKOUT_CODE_SECRET='replace-with-at-least-32-random-characters'
```

Generate and register secure labels:
```bash
python scripts/generate_datamatrix.py --cls multicet --count 10 --out datamatrix_codes
```

Old sequential payloads such as `multicet_1001` are rejected by the scanner.

## ▶️ Usage
Run the main tracker:
```bash
python main.py
```

Press `q` on the OpenCV window to exit.

## 🗄️ Admin Web Interface

The project includes a dependency-free SQLite web admin that runs on Raspberry Pi 5.
It is read-only by default and shows inventory, movement logs, registered codes,
generic table browsing, item tracing, CSV exports, and SELECT-only SQL queries.

Run locally on the Pi:
```bash
python scripts/web_admin.py --host 127.0.0.1 --port 8000
```

Expose it to the local network with a token:
```bash
SMART_CHECKOUT_ADMIN_TOKEN='change-this-token' python scripts/web_admin.py --host 0.0.0.0 --port 8000
```

Open:
```text
http://raspberrypi.local:8000/?token=change-this-token
```

For systemd on Raspberry Pi, use `deployment/smart-checkout-admin.service` and
change the token before enabling it.

## 🔎 Database Tools

Useful CLI reports and maintenance scripts:
```bash
python scripts/db_tools.py summary
python scripts/db_tools.py logs --sort timestamp --limit 50
python scripts/db_tools.py logs --action REMOVED --from 2026-04-30 --csv --out removed.csv
python scripts/db_tools.py items --in-stock yes --sort item_class
python scripts/db_tools.py trace led_box_BC418EA5
python scripts/db_tools.py anomalies
python scripts/db_tools.py schema
python scripts/db_tools.py export-table logs --out logs.csv
python scripts/db_tools.py backup
```

The tools are intentionally tolerant of future database columns such as
`operator_id`, `session_id`, `source`, or NFC-backed `cash_sessions`; those fields
will appear automatically in table views and CSV exports once your colleague adds
them to the database.
