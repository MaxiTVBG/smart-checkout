#!/bin/bash
set -e

echo "=== Smart Checkout — RPi 5 Setup ==="

# 1. System dependencies
sudo apt-get update
sudo apt-get install -y \
    python3-venv python3-dev \
    libdmtx0b libzbar0 \
    libatlas-base-dev libopenblas-dev \
    libgl1 libglib2.0-0 \
    v4l-utils

# 2. Camera permissions (no sudo needed for /dev/video*)
sudo usermod -aG video "$USER"

# 3. USB power — disable auto-suspend (prevents camera drops)
echo -1 | sudo tee /sys/module/usbcore/parameters/autosuspend > /dev/null 2>&1 || true

# 4. GPU memory — allocate 256MB for 1080p
if ! grep -q "gpu_mem=256" /boot/firmware/config.txt 2>/dev/null; then
    echo "gpu_mem=256" | sudo tee -a /boot/firmware/config.txt
    echo ">>> Added gpu_mem=256. Reboot required."
fi

# 5. Virtual environment
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "=== Setup complete. Run: source .venv/bin/activate && python3 main.py ==="
echo "=== Or install service: sudo cp checkout.service /etc/systemd/system/ ==="