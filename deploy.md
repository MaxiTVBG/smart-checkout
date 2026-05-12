# RPi 5 Deployment Checklist

## Хардуер
- [ ] **Захранване**: 5V / 5A (оригинално RPi 5). По-слабо → камерата/AI модулът може да се рестартира
- [ ] **Охлаждане**: Активен вентилатор е **задължителен**. YOLO на CPU → 80°C+ без охлаждане
- [ ] **Камера**: 
  - *Опция A (USB)*: USB камера с поддръжка на 1080p. Проверка: `v4l2-ctl --list-formats-ext -d /dev/video0`
  - *Опция B (AI Camera)*: Raspberry Pi AI Camera (IMX500). Свързва се към MIPI CSI/CSI-2 порта на RPi 5. Проверете с `libcamera-hello`.

## Инсталация
```bash
# 1. Клониране
git clone <repo> ~/s-check && cd ~/s-check

# 2. Setup (инсталира всичко)
chmod +x setup.sh && ./setup.sh

# 3. Конфигурация
# Ако използвате AI камера (IMX500), редактирайте config.yaml и задайте:
# system:
#   vision_backend: pi_ai
cp config.example.yaml config.yaml

# 4. Тест
source .venv/bin/activate
python3 main.py

# 4. Auto-start service
sudo cp checkout.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable checkout.service
sudo systemctl start checkout.service

# 5. Логове
journalctl -u checkout.service -f
```

## Проверки след първи старт
- [ ] `v4l2-ctl --get-fmt-video -d /dev/video0` → потвърди 1920x1080
- [ ] `vcgencmd measure_temp` → под 75°C при натоварване
- [ ] `cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq` → 2400000 (не throttled)

## Промяна на потребител
Ако потребителят НЕ е `pi`, редактирай:
- `checkout.service` → `User=` и `WorkingDirectory=`
