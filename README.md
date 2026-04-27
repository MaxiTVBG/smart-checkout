# Smart Checkout Tracker

Professional Computer Vision system for inventory management using YOLO, ByteTrack, DataMatrix/QR codes, and NoSQL Database.

## 📋 Features
- **Real-Time Object Tracking**: Uses Ultralytics YOLO & ByteTrack.
- **DataMatrix & QR Support**: Fast, ROI-based barcode reading (`pylibdmtx` / `pyzbar`).
- **Anti-Spoofing Validations**: Ensures visually detected classes match encoded QR/DataMatrix seeds.
- **NoSQL Inventory DB**: Uses `TinyDB` for lightweight JSON database management.
- **Hysteresis Line Crossing**: Robust mechanism to prevent jitter and track IN/OUT movements.
- **OpenCV Dashboard**: Real-time overlay showing stock status and recent activity logs.

## 🛠️ Project Structure
```
smart-checkout/
├── config.yaml          # System Configuration
├── main.py              # Main execution script
├── requirements.txt     # Python dependencies
├── data/                # Database files (inventory.json)
├── models/              # YOLO models (.pt)
├── scripts/             # Utility scripts (training, data capture)
└── src/                 # Core logic
    ├── database.py      # TinyDB logic
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
Edit `config.yaml` to change the camera index, paths to YOLO models, or scanning cooldowns.

## ▶️ Usage
Run the main tracker:
```bash
python main.py
```

Press `q` on the OpenCV window to exit.
