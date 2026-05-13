import os
# Заобикаляне на проблема с Wayland на Raspberry Pi Bookworm
os.environ["QT_QPA_PLATFORM"] = "xcb"

import cv2
import time
import yaml
import sys
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from ultralytics import YOLO

# Импортиране на локални модули
from src.database import InventoryDatabase
from src.scanner import scan_code_in_roi
from src.secure_codes import SecureCodeError, load_code_secret, validate_registered_code
from src.ui import draw_hud
from src.camera import CameraStream


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
    """Проверява дали обектът е останал на място през последните 6 кадъра."""
    if len(history) < 6:
        return False
    recent = history[-6:]
    avg_x = sum(p[0] for p in recent) / 6
    avg_y = sum(p[1] for p in recent) / 6
    for x, y in recent:
        if math.hypot(x - avg_x, y - avg_y) > threshold:
            return False
    return True


# ---------------------------------------------------------------------------
# Асинхронен YOLO Inference Pipeline
# ---------------------------------------------------------------------------

class YOLOPipeline:
    """
    Изпълнява YOLO inference в единствена фонова нишка.
    
    Използва Event-базиран модел вместо да създава нова нишка на всеки кадър.
    Главният цикъл подава кадри чрез submit(), а резултатите се четат с get_results().
    Frame skipping: ако предишен inference все още работи, кадърът се пропуска.
    """
    def __init__(self, model_path):
        self.model = YOLO(model_path)
        self._result = None
        self._result_lock = threading.Lock()
        self._frame = None
        self._frame_lock = threading.Lock()
        self._new_frame = threading.Event()
        self._stopped = False
        # Единствена нишка — без overhead от Thread() на всеки кадър
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def submit(self, frame):
        """Подава нов кадър. Ако предишният не е обработен, той се презаписва (frame skip)."""
        with self._frame_lock:
            self._frame = frame
        self._new_frame.set()

    def _worker(self):
        while not self._stopped:
            self._new_frame.wait(timeout=1.0)
            self._new_frame.clear()
            
            with self._frame_lock:
                frame = self._frame
                self._frame = None
            
            if frame is None:
                continue
                
            try:
                results = self.model.track(
                    frame, persist=True, tracker="bytetrack.yaml", verbose=False
                )
                with self._result_lock:
                    self._result = results
            except Exception as e:
                print(f"[YOLO ERROR] {e}")

    def get_results(self):
        """Връща последния готов YOLO резултат (или None)."""
        with self._result_lock:
            return self._result

    def stop(self):
        self._stopped = True


# ---------------------------------------------------------------------------
# State Machine: LOCKED / ACTIVE
# ---------------------------------------------------------------------------

class SystemState:
    """
    Контролира достъпа до касата.
    LOCKED  – Камерата работи, YOLO засича обекти, но НЕ логва.
    ACTIVE  – Пълна функционалност: сканиране + логване.
    """
    LOCKED = "LOCKED"
    ACTIVE = "ACTIVE"

    def __init__(self):
        self._state = self.ACTIVE  # По подразбиране ACTIVE
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
                print("[STATE] Система ОТКЛЮЧЕНА → ACTIVE")

    def lock(self):
        with self._lock:
            if self._state != self.LOCKED:
                self._state = self.LOCKED
                print("[STATE] Система ЗАКЛЮЧЕНА → LOCKED")


# ---------------------------------------------------------------------------
# Non-blocking Scanner
# ---------------------------------------------------------------------------

class AsyncScanner:
    """
    Обвива scan_code_in_roi в ThreadPoolExecutor.
    Подава КОПИЕ на кадъра, за да не го замърси HUD рендерирането.
    """
    def __init__(self, max_workers=2):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._pending = {}  # track_id -> Future

    def submit_scan(self, track_id, frame, x1, y1, x2, y2):
        """Подава заявка за сканиране. Не презаписва готов/чакащ резултат."""
        if track_id in self._pending:
            return
        # КРИТИЧНО: Подаваме КОПИЕ на кадъра, защото главният цикъл
        # продължава да рисува HUD/рамки върху оригинала
        frame_snapshot = frame.copy()
        future = self._executor.submit(scan_code_in_roi, frame_snapshot, int(x1), int(y1), int(x2), int(y2))
        self._pending[track_id] = future

    def get_result(self, track_id):
        """Връща (payload, code_type) ако сканирането е готово, иначе None."""
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
    
    print("Зареждане на YOLO модел (Async Pipeline, 1080p)...")
    yolo_pipe = YOLOPipeline(config['paths']['yolo_model'])
    
    print("Свързване с камерата (1080p)...")
    try:
        cap = CameraStream(config['system']['camera_index']).start()
    except Exception as e:
        print(e)
        return

    system_state = SystemState()
    scanner = AsyncScanner(max_workers=2)

    track_history = {}    # { track_id: [(cx, cy), ...] }
    processed_tracks = {} # { track_id: {"status", "time", "uid", "payload"} }
    
    scan_cooldown = config['system'].get('scan_cooldown_sec', 1.0)
    headless = config['ui'].get('headless', False)

    print("Системата е готова (1080p). Натисни 'q' за изход.")

    cached_inv_state = inventory_db.get_inventory_state()
    cached_rec_logs = inventory_db.get_recent_logs()
    db_needs_update = False

    fps_timer = time.time()
    fps_count = 0
    fps_display = 0

    while True:
        success, frame = cap.read()
        if not success or frame is None:
            continue

        h, w = frame.shape[:2]
        split_x = w // 2
            
        yolo_pipe.submit(frame)
        results = yolo_pipe.get_results()

        if db_needs_update:
            cached_inv_state = inventory_db.get_inventory_state()
            cached_rec_logs = inventory_db.get_recent_logs()
            db_needs_update = False

        # HUD се рисува по-надолу само ако not headless
        if results is not None and results[0].boxes is not None and results[0].boxes.id is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            track_ids = results[0].boxes.id.int().cpu().numpy()
            clss = results[0].boxes.cls.cpu().numpy()
            class_names = results[0].names
            
            active_ids = set()
            
            for box, track_id, cls_idx in zip(boxes, track_ids, clss):
                active_ids.add(track_id)
                
                x1, y1, x2, y2 = map(int, box)
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                
                yolo_class = class_names[int(cls_idx)]
                
                if track_id not in track_history:
                    track_history[track_id] = []
                track_history[track_id].append((cx, cy))
                if len(track_history[track_id]) > 6:
                    track_history[track_id].pop(0)

                current_zone = "IN" if cx < split_x else "OUT"
                
                if is_stationary(track_history[track_id]) and system_state.is_active:
                    # ВАЖНО: Първо проверяваме за готов резултат, ПРЕДИ да подадем нов scan
                    scan_result = scanner.get_result(track_id)
                    if scan_result is not None:
                        payload, code_type = scan_result
                    else:
                        payload, code_type = None, None
                        # Подаваме нов scan само ако няма чакащ
                        should_scan = False
                        if track_id not in processed_tracks:
                            should_scan = True
                        elif time.time() - processed_tracks[track_id].get('time', 0) > scan_cooldown:
                            should_scan = True
                        if should_scan:
                            scanner.submit_scan(track_id, frame, x1, y1, x2, y2)
                    
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
                                            status = f"ERROR: Veche e vutre!"
                                        else:
                                            inventory_db.log_action(inventory_uid, yolo_class, 'ADDED')
                                            db_needs_update = True
                                            status = f"SUCCESS: Vkarano!"

                                    elif current_zone == "OUT":
                                        if is_in_stock is False or is_in_stock is None:
                                            status = f"ERROR: Ne e v sklada!"
                                        else:
                                            inventory_db.log_action(inventory_uid, yolo_class, 'REMOVED')
                                            db_needs_update = True
                                            status = f"SUCCESS: Izkarano!"
                            
                            processed_tracks[track_id] = {
                                'status': status,
                                'time': time.time(),
                                'uid': inventory_uid,
                                'payload': payload
                            }

                box_color = (0, 165, 255)
                info_text = f"ID:{track_id} {yolo_class} (Moving)"
                
                if track_id in processed_tracks:
                    status = processed_tracks[track_id].get('status', '')
                    if status.startswith("SUCCESS: Vkarano"):
                        box_color = (0, 255, 0)
                        info_text = status
                    elif status.startswith("SUCCESS: Izkarano"):
                        box_color = (255, 100, 0)
                        info_text = status
                    elif status.startswith("ERROR"):
                        box_color = (0, 0, 255)
                        info_text = status
                else:
                    if is_stationary(track_history.get(track_id, [])):
                        box_color = (0, 255, 255)
                        info_text = f"ID:{track_id} {yolo_class} (Scanning...)"

                if not system_state.is_active:
                    box_color = (128, 128, 128)
                    info_text = f"ID:{track_id} {yolo_class} (LOCKED)"

                cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)
                
                (tw, th), _ = cv2.getTextSize(info_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                cv2.rectangle(frame, (x1, y1 - 20), (x1 + tw, y1), (0, 0, 0), -1)
                cv2.putText(frame, info_text, (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

            lost_tracks = list(set(track_history.keys()) - active_ids)
            for tid in lost_tracks:
                track_history.pop(tid, None)
                processed_tracks.pop(tid, None)

        fps_count += 1
        if time.time() - fps_timer >= 1.0:
            fps_display = fps_count
            fps_count = 0
            fps_timer = time.time()
        
        if not headless:
            draw_hud(frame, cached_inv_state, cached_rec_logs)
            cv2.line(frame, (split_x, 0), (split_x, h), (200, 200, 200), 1)

            # ОПТИМИЗАЦИЯ ЗА RASPBERRY PI: Ресайзваме кадъра преди imshow.
            # 1080p frame rendering в X11/Wayland отнема много CPU/Време.
            # Показваме го на 540p (половин размер).
            display_frame = cv2.resize(frame, (w // 2, h // 2))
            
            state_label = system_state.current
            cv2.putText(
                display_frame, f"FPS: {fps_display} | {state_label}",
                (display_frame.shape[1] - 180, display_frame.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                (0, 255, 0) if system_state.is_active else (0, 0, 255), 1
            )

            cv2.imshow("Smart Checkout Tracker", display_frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            
    scanner.shutdown()
    yolo_pipe.stop()
    cap.stop()
    cv2.destroyAllWindows()
    print("Системата е спряна успешно.")

if __name__ == "__main__":
    main()
