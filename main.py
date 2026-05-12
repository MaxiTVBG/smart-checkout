import os
os.environ["QT_QPA_PLATFORM"] = "xcb"

import cv2
import time
import yaml
import sys
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from src.database import InventoryDatabase
from src.scanner import scan_code_in_roi
from src.secure_codes import SecureCodeError, load_code_secret, validate_registered_code
from src.ui import draw_hud
from src.vision import create_pipeline

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_config(config_path='config.yaml'):
    try:
        with open(config_path, 'r') as file:
            return yaml.safe_load(file)
    except Exception as e:
        print(f"Грешка при зареждане на {config_path}: {e}")
        sys.exit(1)


def is_stationary(history, threshold=15):
    if len(history) < 6:
        return False
    recent = history[-6:]
    avg_x = sum(p[0] for p in recent) / 6
    avg_y = sum(p[1] for p in recent) / 6
    return all(math.hypot(x - avg_x, y - avg_y) <= threshold for x, y in recent)


# ---------------------------------------------------------------------------
# YOLO Pipeline (persistent worker thread)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# State Machine
# ---------------------------------------------------------------------------

class SystemState:
    LOCKED = "LOCKED"
    ACTIVE = "ACTIVE"

    def __init__(self):
        self._state = self.ACTIVE
        self._lock = threading.Lock()

    @property
    def is_active(self):
        with self._lock:
            return self._state == self.ACTIVE

    @property
    def current(self):
        with self._lock:
            return self._state

    def unlock(self):
        with self._lock:
            if self._state != self.ACTIVE:
                self._state = self.ACTIVE
                print("[STATE] ОТКЛЮЧЕНА → ACTIVE")

    def lock(self):
        with self._lock:
            if self._state != self.LOCKED:
                self._state = self.LOCKED
                print("[STATE] ЗАКЛЮЧЕНА → LOCKED")


# ---------------------------------------------------------------------------
# Async Scanner
# ---------------------------------------------------------------------------

class AsyncScanner:
    def __init__(self, max_workers=2):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._pending = {}

    def submit_scan(self, track_id, hires_frame, x1, y1, x2, y2):
        """Подава scan заявка с 1080p координати и 1080p кадър."""
        if track_id in self._pending:
            return
        snapshot = hires_frame.copy()
        future = self._executor.submit(
            scan_code_in_roi, snapshot, int(x1), int(y1), int(x2), int(y2)
        )
        self._pending[track_id] = future

    def get_result(self, track_id):
        if track_id not in self._pending:
            return None
        future = self._pending[track_id]
        if not future.done():
            return None
        try:
            result = future.result()
        except Exception:
            result = (None, None)
        del self._pending[track_id]
        return result

    def shutdown(self):
        self._executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Coordinate Mapping: lores → hires
# ---------------------------------------------------------------------------

# Предварително изчислени скейл фактори (избягваме деление на всеки кадър)
SCALE_X = CameraStream.HIRES[0] / CameraStream.LORES[0]  # 1920/640 = 3.0
SCALE_Y = CameraStream.HIRES[1] / CameraStream.LORES[1]  # 1080/480 = 2.25


def lores_to_hires(x1, y1, x2, y2):
    """Преобразува координати от 640x480 (YOLO) към 1920x1080 (Scanner)."""
    return (
        int(x1 * SCALE_X),
        int(y1 * SCALE_Y),
        int(x2 * SCALE_X),
        int(y2 * SCALE_Y),
    )


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------

def main():
    config = load_config()

    try:
        code_secret = load_code_secret(config)
    except SecureCodeError as e:
        print(f"Грешка в защитата на Data Matrix кодовете: {e}")
        return

    print("Инициализация на база данни (SQLite WAL)...")
    db_path = config['paths']['db_path']
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    inventory_db = InventoryDatabase(db_path)

    print("Инициализация на Vision Pipeline...")
    try:
        pipeline = create_pipeline(config).start()
    except Exception as e:
        print(f"Грешка при стартиране на камерата: {e}")
        return

    system_state = SystemState()
    scanner = AsyncScanner(max_workers=2)

    track_history = {}
    processed_tracks = {}

    scan_cooldown = config['system'].get('scan_cooldown_sec', 1.0)
    headless = config['ui'].get('headless', False)

    print("Системата е готова. YOLO@640x480, Scanner@1080p. Натисни 'q' за изход.")

    cached_inv_state = inventory_db.get_inventory_state()
    cached_rec_logs = inventory_db.get_recent_logs()
    db_needs_update = False

    fps_timer = time.time()
    fps_count = 0
    fps_display = 0

    while True:
        success, lores, hires, detections = pipeline.read()
        if not success or lores is None:
            continue

        h, w = lores.shape[:2]  # 480, 640
        split_x = w // 2

        if db_needs_update:
            cached_inv_state = inventory_db.get_inventory_state()
            cached_rec_logs = inventory_db.get_recent_logs()
            db_needs_update = False

        # UI се рисува на 640x480 → бърз rendering
        draw_hud(lores, cached_inv_state, cached_rec_logs)
        cv2.line(lores, (split_x, 0), (split_x, h), (200, 200, 200), 1)

        active_ids = set()

        if detections:
            for det in detections:
                track_id = det.track_id
                active_ids.add(track_id)

                # Координати в lores (640x480) пространство
                x1, y1, x2, y2 = map(int, det.box)
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2

                yolo_class = det.class_name

                if track_id not in track_history:
                    track_history[track_id] = []
                track_history[track_id].append((cx, cy))
                if len(track_history[track_id]) > 6:
                    track_history[track_id].pop(0)

                current_zone = "IN" if cx < split_x else "OUT"

                if is_stationary(track_history[track_id]) and system_state.is_active:
                    scan_result = scanner.get_result(track_id)
                    if scan_result is not None:
                        payload, code_type = scan_result
                    else:
                        payload, code_type = None, None
                        should_scan = False
                        if track_id not in processed_tracks:
                            should_scan = True
                        elif time.time() - processed_tracks[track_id].get('time', 0) > scan_cooldown:
                            should_scan = True
                        if should_scan:
                            # Мащабиране: lores → hires за скенера
                            hx1, hy1, hx2, hy2 = lores_to_hires(x1, y1, x2, y2)
                            scanner.submit_scan(track_id, hires, hx1, hy1, hx2, hy2)

                    if payload:
                        if (track_id in processed_tracks
                            and processed_tracks[track_id].get('payload') == payload
                            and "SUCCESS" in processed_tracks[track_id].get('status', '')):
                            processed_tracks[track_id]['time'] = time.time()
                        else:
                            status = ""
                            inventory_uid = None

                            try:
                                secure_code = validate_registered_code(payload, code_secret, inventory_db)
                                inventory_uid = secure_code.inventory_uid
                            except SecureCodeError as e:
                                status = f"ERROR: CODE! {e}"
                            else:
                                if secure_code.item_class != yolo_class:
                                    status = f"ERROR: SPOOF! YOLO={yolo_class}, Code={secure_code.item_class}"
                                else:
                                    is_in_stock = inventory_db.check_item_status(inventory_uid)

                                    if current_zone == "IN":
                                        if is_in_stock is True:
                                            status = "ERROR: Veche e vutre!"
                                        else:
                                            inventory_db.log_action(inventory_uid, yolo_class, 'ADDED')
                                            db_needs_update = True
                                            status = "SUCCESS: Vkarano!"

                                    elif current_zone == "OUT":
                                        if is_in_stock is False or is_in_stock is None:
                                            status = "ERROR: Ne e v sklada!"
                                        else:
                                            inventory_db.log_action(inventory_uid, yolo_class, 'REMOVED')
                                            db_needs_update = True
                                            status = "SUCCESS: Izkarano!"

                            processed_tracks[track_id] = {
                                'status': status,
                                'time': time.time(),
                                'uid': inventory_uid,
                                'payload': payload
                            }

                # --- UI (рисуване на lores) ---
                box_color = (0, 165, 255)
                info_text = f"ID:{track_id} {yolo_class}"

                if track_id in processed_tracks:
                    st = processed_tracks[track_id].get('status', '')
                    if "SUCCESS: Vkarano" in st:
                        box_color = (0, 255, 0)
                        info_text = st
                    elif "SUCCESS: Izkarano" in st:
                        box_color = (255, 100, 0)
                        info_text = st
                    elif st.startswith("ERROR"):
                        box_color = (0, 0, 255)
                        info_text = st
                elif is_stationary(track_history.get(track_id, [])):
                    box_color = (0, 255, 255)
                    info_text = f"ID:{track_id} Scanning..."

                if not system_state.is_active:
                    box_color = (128, 128, 128)
                    info_text = f"ID:{track_id} LOCKED"

                cv2.rectangle(lores, (x1, y1), (x2, y2), box_color, 2)
                (tw, _), _ = cv2.getTextSize(info_text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
                cv2.rectangle(lores, (x1, y1 - 16), (x1 + tw, y1), (0, 0, 0), -1)
                cv2.putText(lores, info_text, (x1, y1 - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

            lost = set(track_history.keys()) - active_ids
            for tid in lost:
                track_history.pop(tid, None)
                processed_tracks.pop(tid, None)

        # FPS counter
        fps_count += 1
        if time.time() - fps_timer >= 1.0:
            fps_display = fps_count
            fps_count = 0
            fps_timer = time.time()

        state_label = system_state.current
        cv2.putText(
            lores, f"FPS:{fps_display} | {state_label}",
            (w - 200, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
            (0, 255, 0) if system_state.is_active else (0, 0, 255), 1
        )

        if not headless:
            cv2.imshow("Smart Checkout", lores)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    scanner.shutdown()
    pipeline.stop()
    cv2.destroyAllWindows()
    print("Системата е спряна.")

if __name__ == "__main__":
    main()
