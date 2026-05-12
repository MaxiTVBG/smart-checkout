<p align="center">
  <img src="https://img.shields.io/badge/Platform-Raspberry%20Pi%205-c51a4a?style=for-the-badge&logo=raspberrypi&logoColor=white" alt="RPi 5"/>
  <img src="https://img.shields.io/badge/Resolution-1080p-00cec9?style=for-the-badge" alt="1080p"/>
  <img src="https://img.shields.io/badge/AI-YOLOv8%20NCNN-6c5ce7?style=for-the-badge" alt="YOLO"/>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/>
</p>

# 🛒 Smart Checkout

**AI-powered inventory tracking system** built for Raspberry Pi 5. Uses real-time computer vision (YOLOv8 + ByteTrack) and cryptographically signed DataMatrix codes to track items entering and leaving a checkout zone — all at 1080p resolution.

---

## ✨ Key Features

| Feature | Description |
|---------|-------------|
| 🎯 **Real-Time Object Tracking** | YOLOv8 with ByteTrack for persistent object identification across frames |
| 📷 **1080p Pipeline** | Full HD capture with async inference — no resolution compromise |
| 🔐 **HMAC-Signed DataMatrix** | Each item carries a cryptographically signed code — prevents spoofing |
| 🛡️ **Anti-Spoofing** | Cross-validates YOLO visual class against the signed code's class |
| 🗄️ **SQLite with WAL** | Write-Ahead Logging optimized for SD card longevity on RPi |
| 🌐 **Web Admin Panel** | Full dashboard with RBAC, Google OAuth, inventory management, and SQL console |
| 🔄 **NFC State Machine** | LOCKED/ACTIVE states — integrates with external NFC access control |
| ⚡ **Async Architecture** | Threaded YOLO inference + non-blocking DataMatrix scanner = smooth 30+ FPS HUD |

---

## 🏗️ Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  USB Camera  │────▶│  CameraStream    │────▶│   Main Loop     │
│  1080p/30fps │     │  (background     │     │   (HUD @ 30fps) │
└──────────────┘     │   thread)        │     └────────┬────────┘
                     └──────────────────┘              │
                                                       ▼
                     ┌──────────────────┐     ┌─────────────────┐
                     │  YOLOPipeline    │◀────│  Frame Submit   │
                     │  (worker thread) │     │  (skip if busy) │
                     └───────┬──────────┘     └─────────────────┘
                             │
                             ▼
                     ┌──────────────────┐     ┌─────────────────┐
                     │  AsyncScanner    │────▶│  SQLite WAL DB  │
                     │  (ThreadPool)    │     │  (thread-safe)  │
                     └──────────────────┘     └─────────────────┘
```

---

## 📂 Project Structure

```
smart-checkout/
├── main.py                    # Main vision loop (async YOLO + scanner)
├── config.example.yaml        # Configuration template
├── setup.sh                   # RPi 5 one-click setup script
├── checkout.service           # systemd unit for auto-start
├── deploy.md                  # RPi 5 deployment checklist
├── requirements.txt           # Python dependencies
│
├── src/
│   ├── camera.py              # 1080p threaded capture with auto-reconnect
│   ├── database.py            # Thread-safe SQLite with WAL optimization
│   ├── scanner.py             # DataMatrix/QR decoder (adaptive threshold)
│   ├── secure_codes.py        # HMAC payload signing & verification
│   ├── ui.py                  # OpenCV HUD (optimized sub-ROI overlay)
│   ├── admin_queries.py       # Admin panel query engine
│   └── web/
│       ├── app.py             # FastAPI application
│       ├── auth.py            # RBAC + Google OAuth
│       ├── utils.py           # HTML rendering utilities
│       ├── routes/
│       │   ├── auth_routes.py
│       │   ├── page_routes.py
│       │   └── action_routes.py
│       └── templates/         # Jinja2 HTML templates
│
├── scripts/
│   ├── web_admin.py           # Start web admin server
│   ├── db_tools.py            # CLI database tools
│   ├── generate_datamatrix.py # Generate signed DataMatrix codes
│   ├── backup_db.py           # Database backup utility
│   ├── export_model.py        # YOLO → NCNN model export
│   └── capture_data.py        # Training data capture
│
├── models/                    # YOLO NCNN models
├── data/                      # SQLite database (auto-created)
├── datamatrix_codes/          # Generated DataMatrix images
└── tests/
```

---

## 🚀 Quick Start

### Prerequisites

| Component | Requirement |
|-----------|-------------|
| **Python** | 3.10+ |
| **Camera** | USB webcam (1080p) OR **Raspberry Pi AI Camera** (IMX500) |
| **OS** | Raspberry Pi OS Bookworm / macOS / Ubuntu |

### 1. Clone & Install

```bash
git clone https://github.com/MaxiTVBG/smart-checkout.git
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

**Raspberry Pi / Debian:**
```bash
sudo apt-get install -y libzbar0 libdmtx0b libatlas-base-dev
```

### 3. Configure

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` — set your vision backend, camera index, tokens, and signing secret.
If using the **Raspberry Pi AI Camera**, set `vision_backend: pi_ai`. For standard USB/Mac cameras, use `vision_backend: mac`.

```bash
# Required: DataMatrix signing secret (min 32 characters)
export SMART_CHECKOUT_CODE_SECRET='your-very-long-random-secret-key-here-min-32-chars'
```

### 4. Generate DataMatrix Codes

Before tracking items, register signed codes for them:

```bash
python scripts/generate_datamatrix.py --class raspberry_pi_5 --count 5
```

This creates signed DataMatrix PNGs in `datamatrix_codes/` and registers them in the database.

### 5. Run

**Camera tracker (main system):**
```bash
python main.py
```

**Web admin panel (separate terminal):**
```bash
python scripts/web_admin.py
# → http://localhost:8000
```

Press `q` in the camera window to quit.

---

## 🌐 Web Admin Panel

Full-featured management dashboard accessible from any device on the network.

### Pages

| Page | Description | Permission |
|------|-------------|------------|
| **Dashboard** | Real-time metrics, 7-day chart, anomaly detection | `view_dashboard` |
| **Inventory** | Browse items, manual Add/Remove | `view_inventory` |
| **Movements** | Filter & export movement logs (CSV) | `view_logs` |
| **Codes** | View/activate/deactivate DataMatrix codes | `manage_codes` |
| **Trace** | Full lifecycle investigation for any item | `view_trace` |
| **Tables** | Raw database table browser | `view_tables` |
| **SQL** | Direct SELECT queries | `run_sql` |
| **Users** | Add/remove users, change roles | `manage_users` |

### Authentication

| Method | When |
|--------|------|
| **Access Token** | From localhost — enter token from `config.yaml` |
| **Google OAuth** | From remote devices — requires [Google Cloud Console](https://console.cloud.google.com) setup |

### Roles

| Role | Access |
|------|--------|
| `admin` | Full access including SQL console and user management |
| `manager` | Inventory actions, codes, exports (no SQL, no user mgmt) |
| `viewer` | Read-only access to all views |

Custom roles with granular permissions can be defined in `config.yaml`.

---

## 🍓 Raspberry Pi 5 Deployment

### One-Click Setup

```bash
chmod +x setup.sh
./setup.sh
sudo reboot  # Required for GPU memory allocation
```

The setup script handles: system dependencies, camera permissions, USB power optimization, GPU memory (256MB for 1080p), and Python virtual environment.

### Auto-Start on Boot

```bash
# Install the systemd service
sudo cp checkout.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable checkout.service
sudo systemctl start checkout.service

# View logs
journalctl -u checkout.service -f
```

> **Note:** Edit `checkout.service` if your username is not `pi` — change `User=` and `WorkingDirectory=`.

### Hardware Requirements

| Component | Requirement | Why |
|-----------|-------------|-----|
| **Power Supply** | 5V / 5A (official RPi 5 PSU) | Camera + YOLO = high power draw |
| **Cooling** | Active fan **mandatory** | 1080p YOLO inference → 80°C+ without cooling |
| **Camera** | USB webcam OR Pi AI Camera | AI Camera handles inference on NPU, saving CPU resources. |
| **Storage** | 16GB+ SD card (A2 class recommended) | WAL mode is optimized but fast storage helps |

### Health Checks

```bash
# Camera resolution
v4l2-ctl --get-fmt-video -d /dev/video0

# CPU temperature (should be <75°C)
vcgencmd measure_temp

# CPU frequency (2400000 = not throttled)
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq
```

---

## 🔎 CLI Database Tools

```bash
# Overview
python scripts/db_tools.py summary

# Browse items
python scripts/db_tools.py items --in-stock yes

# Movement logs
python scripts/db_tools.py logs --sort timestamp --limit 50

# Trace specific item
python scripts/db_tools.py trace raspberry_pi_5_20F9FD53

# Find anomalies
python scripts/db_tools.py anomalies

# Backup database
python scripts/backup_db.py
```

---

## 🔒 Security Model

- **DataMatrix codes** are HMAC-SHA256 signed — cannot be forged without the secret key
- **Anti-spoofing** cross-validates YOLO detection class against the signed code's class
- **Web admin** uses session-based auth with CSRF protection and rate limiting
- **Google OAuth** with email allowlist for remote access
- **SQL console** is read-only (`SELECT` only) and restricted to `admin` role
- **Secrets** are loaded from environment variables, never committed to git

---

## 🧠 How It Works

1. **Camera** captures 1080p frames in a background thread
2. **YOLO** runs inference asynchronously (frame skipping when busy)
3. **ByteTrack** assigns persistent IDs to detected objects
4. When an object is **stationary for 6 frames**, the scanner activates
5. **DataMatrix decoder** reads the code from the object's ROI (with adaptive thresholding)
6. **HMAC verification** ensures the code is authentic and registered
7. **Zone detection** (left = IN, right = OUT) determines if item is being added or removed
8. **SQLite** logs the action and updates inventory state

---

## 📄 License

Private project — all rights reserved.
