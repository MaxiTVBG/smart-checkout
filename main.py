import os
# Заобикаляне на проблема с Wayland на Raspberry Pi Bookworm
os.environ["QT_QPA_PLATFORM"] = "xcb"

import cv2
import time
import yaml
import sys
import math
import numpy as np
from ultralytics import YOLO

# Импортиране на локални модули
from src.database import InventoryDatabase
from src.scanner import scan_code_in_roi
from src.secure_codes import SecureCodeError, load_code_secret, validate_registered_code
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

    try:
        code_secret = load_code_secret(config)
    except SecureCodeError as e:
        print(f"Грешка в защитата на Data Matrix кодовете: {e}")
        return
    
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
    
    rfid_enable = config.get('raspberry', {}).get('rfid', False)
    buzzer_enable = config.get('raspberry', {}).get('buzzer', False)
    
    rfid = None
    buzzer = None

    if rfid_enable:
        print("Инициализация на RFID четец...")
        from src.rfid_reader import RFIDReader
        print(config['raspberry']['rfid_rst_pin'])
        rfid = RFIDReader(rst_pin=config['raspberry']['rfid_rst_pin'])
        

    if buzzer_enable:
        print("Инициализация на buzzer...")
        from src.buzzer import Buzzer
        buzzer = Buzzer(pin=config['raspberry']['buzzer_pin'])

    # Състояние за Smart Counter
    track_history = {}    # { track_id: [(cx, cy), ...] }
    processed_tracks = {} # { track_id: "STATUS_MESSAGE" }
    
    scan_cooldown = config['system'].get('scan_cooldown_sec', 1.0)
    headless = config['ui'].get('headless', False)

    # Управление на сесията
    current_user = "Guest" if not rfid_enable else None
    session_timeout = 0
    SESSION_LENGTH_SEC = 500.0

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
        
        # --- RFID И ЛОГИКА ЗА ДОСТЪП ---
        if rfid_enable and rfid:
            scanned_chip_id = rfid.get_last_scan() 
            
            if scanned_chip_id:
                user_name = inventory_db.is_user_valid(scanned_chip_id)
                if user_name:
                    if current_user == user_name:
                        # Tap Out (Ръчно излизане)
                        print(f"[LOGOUT] Успешно излизане, {current_user}")
                        current_user = None
                        track_history.clear()
                        processed_tracks.clear()
                        if buzzer: buzzer.play_checkout()
                    else:
                        # Tap In (Влизане или смяна на потребител)
                        current_user = user_name
                        session_timeout = time.time() + SESSION_LENGTH_SEC
                        print(f"[ACCESS GRANTED] Добре дошъл, {user_name}!")
                        if buzzer: buzzer.play_login()
                else:
                    print(f"[ACCESS DENIED] Непознат чип: {scanned_chip_id}")
                    if buzzer: buzzer.play_error()
            
            # Автоматично изтичане на сесията
            if current_user and current_user != "Guest" and time.time() > session_timeout:
                print(f"[СЕСИЯТА ИЗТЕЧЕ] Довиждане, {current_user}")
                current_user = None
                track_history.clear() 
                processed_tracks.clear()
                if buzzer: buzzer.play_checkout()

        if current_user is not None:
            results = model.track(frame, persist=True, tracker="bytetrack.yaml", verbose=False)

            # Обновяване на кеша само ако има промяна
            if db_needs_update:
                cached_inv_state = inventory_db.get_inventory_state()
                cached_rec_logs = inventory_db.get_recent_logs()
                db_needs_update = False

            # Чертане на новия HUD интерфейс
            draw_hud(frame, cached_inv_state, cached_rec_logs)

            # Изписване на активния потребител
            cv2.putText(frame, f"ACTIVE: {current_user}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            
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
                            payload, code_type = scan_code_in_roi(frame, x1, y1, x2, y2)
                            
                            if payload:
                                # Ако сканираме същия предмет, който вече е успешен, само подновяваме таймера
                                if track_id in processed_tracks and processed_tracks[track_id].get('payload') == payload and "SUCCESS" in processed_tracks[track_id].get('status', ''):
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
                                        # Валидация 1: Anti-Spoofing
                                        if secure_code.item_class != yolo_class:
                                            status = f"ERROR: SPOOF! YOLO={yolo_class}, Code={secure_code.item_class}"
                                        else:
                                            # Валидация 2 & 3: Database Status
                                            is_in_stock = inventory_db.check_item_status(inventory_uid)

                                            if current_zone == "IN":
                                                if is_in_stock is True:
                                                    status = f"ERROR: Veche e vutre!"
                                                else:
                                                    inventory_db.log_action(inventory_uid, yolo_class, 'ADDED')
                                                    db_needs_update = True
                                                    status = f"SUCCESS: Vkarano!"
                                                    if buzzer: buzzer.play_item_detected()
                                                    if rfid_enable: session_timeout = time.time() + SESSION_LENGTH_SEC

                                            elif current_zone == "OUT":
                                                if is_in_stock is False or is_in_stock is None:
                                                    status = f"ERROR: Ne e v sklada!"
                                                else:
                                                    inventory_db.log_action(inventory_uid, yolo_class, 'REMOVED')
                                                    db_needs_update = True
                                                    status = f"SUCCESS: Izkarano!"
                                                    if buzzer: buzzer.play_item_detected()
                                                    if rfid_enable: session_timeout = time.time() + SESSION_LENGTH_SEC
                                    
                                    processed_tracks[track_id] = {
                                        'status': status,
                                        'time': time.time(),
                                        'uid': inventory_uid,
                                        'payload': payload
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
            if current_user is not None:
                cv2.imshow("Smart Checkout Tracker", frame)
            else:
                # ЧЕРЕН ЕКРАН ПРИ ЗАКЛЮЧЕНА СИСТЕМА
                standby_frame = np.zeros_like(frame)
                text1 = "SYSTEM LOCKED"
                text2 = "Please scan your RFID card to begin"
                
                (tw1, th1), _ = cv2.getTextSize(text1, cv2.FONT_HERSHEY_SIMPLEX, 1.5, 3)
                (tw2, th2), _ = cv2.getTextSize(text2, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
                
                cv2.putText(standby_frame, text1, ((w - tw1) // 2, h // 2 - 20), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 3)
                cv2.putText(standby_frame, text2, ((w - tw2) // 2, h // 2 + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
                
                cv2.imshow("Smart Checkout Tracker", standby_frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            
    cap.stop()
    cv2.destroyAllWindows()
    if rfid: rfid.stop()
    if buzzer: buzzer.cleanup()
    print("Системата е спряна успешно.")

if __name__ == "__main__":
    main()
