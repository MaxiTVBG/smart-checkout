import os
# Заобикаляне на проблема с Wayland на Raspberry Pi Bookworm
os.environ["QT_QPA_PLATFORM"] = "xcb"

import cv2
import time
import yaml
import sys
import math
from ultralytics import YOLO

# Импортиране на локални модули
from src.database import InventoryDatabase
from src.scanner import scan_code_in_roi
from src.ui import draw_hud
from src.camera import CameraStream

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
    
    # Намираме центъра на последните 6 позиции
    recent = history[-6:]
    avg_x = sum(p[0] for p in recent) / 6
    avg_y = sum(p[1] for p in recent) / 6
    
    # Проверяваме дали някоя точка бяга от центъра (липса на треперене)
    for x, y in recent:
        if math.hypot(x - avg_x, y - avg_y) > threshold:
            return False
    return True

def main():
    config = load_config()
    
    # Инициализация
    print("Инициализация на база данни (SQLite WAL)...")
    db_path = config['paths']['db_path']
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    inventory_db = InventoryDatabase(db_path)
    
    print("Зареждане на YOLO модел...")
    model = YOLO(config['paths']['yolo_model'])
    
    print("Свързване с камерата (асинхронно)...")
    try:
        cap = CameraStream(config['system']['camera_index']).start()
    except Exception as e:
        print(e)
        return

    # Състояние за Smart Counter
    track_history = {}    # { track_id: [(cx, cy), ...] }
    processed_tracks = {} # { track_id: "STATUS_MESSAGE" }
    
    scan_cooldown = config['system'].get('scan_cooldown_sec', 1.0)
    headless = config['ui'].get('headless', False)

    print("Системата е готова (Smart Counter режим). Натисни 'q' за изход.")

    # Кеш на базата данни (за да не я питаме 30 пъти в секунда)
    cached_inv_state = inventory_db.get_inventory_state()
    cached_rec_logs = inventory_db.get_recent_logs()
    db_needs_update = False

    while True:
        success, frame = cap.read()
        if not success or frame is None:
            continue
            
        h, w = frame.shape[:2]
        
        # Дефиниране на зоните (Средата на екрана)
        split_x = w // 2
        
        results = model.track(frame, persist=True, tracker="bytetrack.yaml", verbose=False)

        # Обновяване на кеша само ако има промяна
        if db_needs_update:
            cached_inv_state = inventory_db.get_inventory_state()
            cached_rec_logs = inventory_db.get_recent_logs()
            db_needs_update = False

        # Чертане на новия HUD интерфейс
        draw_hud(frame, cached_inv_state, cached_rec_logs)
        
        # Чертане на разделителната линия (Тънка, пунктирана или лека бяла линия)
        cv2.line(frame, (split_x, 0), (split_x, h), (200, 200, 200), 1)

        if results[0].boxes is not None and results[0].boxes.id is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            track_ids = results[0].boxes.id.int().cpu().numpy()
            clss = results[0].boxes.cls.cpu().numpy()
            class_names = results[0].names
            
            # Активни IDs в този кадър, за да чистим старата история
            active_ids = set()
            
            for box, track_id, cls_idx in zip(boxes, track_ids, clss):
                active_ids.add(track_id)
                x1, y1, x2, y2 = map(int, box)
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                
                yolo_class = class_names[int(cls_idx)]
                
                # Обновяване на историята на движението
                if track_id not in track_history:
                    track_history[track_id] = []
                track_history[track_id].append((cx, cy))
                if len(track_history[track_id]) > 6:
                    track_history[track_id].pop(0)

                # Идентификация на зоната
                current_zone = "IN" if cx < split_x else "OUT"
                
                # Логика за Dwell Time и Сканиране
                if is_stationary(track_history[track_id]):
                    should_scan = False
                    if track_id not in processed_tracks:
                        should_scan = True
                    else:
                        # Ако е имало грешка или обектът стои дълго, сканираме отново
                        if time.time() - processed_tracks[track_id].get('time', 0) > scan_cooldown:
                            should_scan = True
                            
                    if should_scan:
                        uid, code_type = scan_code_in_roi(frame, x1, y1, x2, y2)
                        
                        if uid:
                            # Ако сканираме същия предмет, който вече е успешен, само подновяваме таймера
                            if track_id in processed_tracks and processed_tracks[track_id].get('uid') == uid and "SUCCESS" in processed_tracks[track_id].get('status', ''):
                                processed_tracks[track_id]['time'] = time.time()
                            else:
                                # Използваме rsplit('_', 1), за да разделим само по ПОСЛЕДНАТА долна черта
                                qr_prefix = uid.rsplit('_', 1)[0] if '_' in uid else uid
                                status = ""
                                
                                # Валидация 1: Anti-Spoofing
                                if qr_prefix != yolo_class:
                                    status = f"ERROR: SPOOF! YOLO={yolo_class}, Code={qr_prefix}"
                                else:
                                    # Валидация 2 & 3: Database Status
                                    is_in_stock = inventory_db.check_item_status(uid)
                                    
                                    if current_zone == "IN":
                                        if is_in_stock is True:
                                            status = f"ERROR: Veche e vutre!"
                                        else:
                                            inventory_db.log_action(uid, yolo_class, 'ADDED')
                                            db_needs_update = True
                                            status = f"SUCCESS: Vkarano!"
                                            
                                    elif current_zone == "OUT":
                                        if is_in_stock is False or is_in_stock is None:
                                            status = f"ERROR: Ne e v sklada!"
                                        else:
                                            inventory_db.log_action(uid, yolo_class, 'REMOVED')
                                            db_needs_update = True
                                            status = f"SUCCESS: Izkarano!"
                                
                                processed_tracks[track_id] = {
                                    'status': status,
                                    'time': time.time(),
                                    'uid': uid
                                }

                # Визуално оформление (Feedback)
                box_color = (0, 165, 255) # Оранжево (Moving)
                info_text = f"ID:{track_id} {yolo_class} (Moving)"
                
                if track_id in processed_tracks:
                    status = processed_tracks[track_id].get('status', '')
                    if status.startswith("SUCCESS: Vkarano"):
                        box_color = (0, 255, 0) # Зелено
                        info_text = status
                    elif status.startswith("SUCCESS: Izkarano"):
                        box_color = (255, 100, 0) # Синьо-оранжево
                        info_text = status
                    elif status.startswith("ERROR"):
                        box_color = (0, 0, 255) # Червено
                        info_text = status
                else:
                    if is_stationary(track_history.get(track_id, [])):
                        box_color = (0, 255, 255) # Жълто
                        info_text = f"ID:{track_id} {yolo_class} (Scanning...)"

                cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)
                
                # Изчертаване на текста с черен фон
                (tw, th), _ = cv2.getTextSize(info_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                cv2.rectangle(frame, (x1, y1 - 20), (x1 + tw, y1), (0, 0, 0), -1)
                cv2.putText(frame, info_text, (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

            # Почистване на паметта (ако обектът е вдигнат от плота, го махаме от локнатите)
            lost_tracks = list(set(track_history.keys()) - active_ids)
            for tid in lost_tracks:
                track_history.pop(tid, None)
                processed_tracks.pop(tid, None)

        if not headless:
            cv2.imshow("Smart Checkout Tracker", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            
    cap.stop()
    cv2.destroyAllWindows()
    print("Системата е спряна успешно.")

if __name__ == "__main__":
    main()
