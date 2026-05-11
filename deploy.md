# RPi 5 Deployment Checklist

## Хардуер
- [ ] **Захранване**: 5V / 5A (оригинално RPi 5). По-слабо → камерата пада
- [ ] **Охлаждане**: Активен вентилатор е **задължителен**. YOLO на 1080p → 80°C+ без охлаждане
- [ ] **Камера**: USB камера с поддръжка на 1080p. Проверка: `v4l2-ctl --list-formats-ext -d /dev/video0`

## Инсталация
```bash
# 1. Клониране
git clone <repo> ~/s-check && cd ~/s-check

# 2. Setup (инсталира всичко)
chmod +x setup.sh && ./setup.sh

# 3. Тест
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
