# Smart Checkout Tracker

Professional Computer Vision system for inventory management using YOLO, ByteTrack, DataMatrix/QR codes, and SQLite.

## 📋 Features
- **Real-Time Object Tracking**: Ultralytics YOLO & ByteTrack for detecting and counting items.
- **Secure DataMatrix Support**: HMAC-signed Data Matrix payloads with local registry validation.
- **Anti-Spoofing**: Ensures visually detected classes match registered signed codes.
- **SQLite Inventory DB**: Uses WAL mode for fast, concurrent stock tracking.
- **Web Admin Panel**: Full-featured dashboard with role-based access, Google OAuth, and live management.
- **Runs on Raspberry Pi 5**: Optimized for headless deployment with NCNN inference.

## 🛠️ Project Structure
```
smart-checkout/
├── config.example.yaml  # Config template (copy to config.yaml)
├── main.py              # Main tracker (YOLO + camera)
├── requirements.txt     # Python dependencies
├── data/                # SQLite database (auto-created)
├── models/              # YOLO NCNN models
├── scripts/
│   ├── web_admin.py     # Web admin server
│   ├── db_tools.py      # CLI database tools
│   └── generate_datamatrix.py
├── src/                 # Core logic modules
│   ├── database.py      # SQLite operations
│   ├── admin_queries.py # Admin query engine
│   ├── scanner.py       # QR & DataMatrix scanner
│   ├── secure_codes.py  # HMAC code validation
│   └── ui.py            # OpenCV HUD
├── deployment/          # systemd service files for Raspberry Pi
└── tests/
```

## 🚀 Quick Start

### 1. Clone & Setup
```bash
git clone https://github.com/YOUR_USER/smart-checkout.git
cd smart-checkout
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. System Dependencies

**macOS:**
```bash
brew install zbar libdmtx
```

**Raspberry Pi (Debian/Ubuntu):**
```bash
sudo apt install libzbar0 libdmtx0b
```

### 3. Configure
```bash
cp config.example.yaml config.yaml
# Edit config.yaml — set tokens, camera index, model path, etc.
```

Set a signing secret:
```bash
export SMART_CHECKOUT_CODE_SECRET='replace-with-at-least-32-random-characters'
```

### 4. Run

**Camera tracker:**
```bash
python main.py
```

**Web admin only:**
```bash
python scripts/web_admin.py
```

## 🌐 Web Admin Panel

The web admin provides a complete dashboard for inventory management.

### Features
- **Dashboard**: Real-time metrics, anomaly detection
- **Inventory**: Browse items with manual Add/Remove buttons
- **Movements**: Filter and export movement logs
- **Codes**: View and Activate/Deactivate registered codes
- **Trace**: Full item lifecycle investigation
- **SQL**: Direct SELECT queries (admin only)
- **User Management**: Add/remove users, change roles from the UI

### Authentication

**From localhost (host machine):** Enter an access token defined in `config.yaml`.

**From remote devices (phones, tablets):** Google OAuth sign-in. Requires a one-time setup in [Google Cloud Console](https://console.cloud.google.com):
1. Create OAuth credentials (Web Application type)
2. Add redirect URI: `http://<your-ip>.nip.io:<port>/auth/google/callback`
3. Add `client_id`, `client_secret`, and authorized emails to `config.yaml`

### Roles & Permissions

| Role | Access |
|---|---|
| `admin` | Everything (including SQL and user management) |
| `manager` | Inventory actions, codes, exports (no SQL, no user mgmt) |
| `viewer` | Read-only access to all views |

Custom roles can be defined in `config.yaml` with specific permissions.

## 🍓 Raspberry Pi 5 Deployment

### Setup
```bash
# On the Pi
git clone https://github.com/YOUR_USER/smart-checkout.git /home/pi/smart-checkout
cd /home/pi/smart-checkout
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo apt install libzbar0 libdmtx0b

cp config.example.yaml config.yaml
# Edit config.yaml with your settings
```

### Auto-start with systemd
```bash
# Web admin (starts automatically on boot)
sudo cp deployment/smart-checkout-admin.service /etc/systemd/system/
sudo systemctl enable smart-checkout-admin
sudo systemctl start smart-checkout-admin

# Camera tracker (optional, if running headless)
sudo cp deployment/smart-checkout.service /etc/systemd/system/
sudo systemctl enable smart-checkout
sudo systemctl start smart-checkout
```

### Check status
```bash
sudo systemctl status smart-checkout-admin
sudo journalctl -u smart-checkout-admin -f
```

## 🔎 CLI Database Tools
```bash
python scripts/db_tools.py summary
python scripts/db_tools.py logs --sort timestamp --limit 50
python scripts/db_tools.py items --in-stock yes
python scripts/db_tools.py trace led_box_BC418EA5
python scripts/db_tools.py anomalies
python scripts/db_tools.py backup
```

## 📄 License
Private project.
