import cv2
import time
import yaml
import sys
import os
from ultralytics import YOLO

# Импортиране на локални модули
from src.database import InventoryDatabase
from src.scanner import scan_code_in_roi
from src.ui import draw_dashboard
from src.camera import CameraStream

def load_config(config_path='config.yaml'):
    try:
        with open(config_path, 'r') as file:
            return yaml.safe_load(file)
    except Exception as e:
        print(f"Грешка при зареждане на {config_path}: {e}")
        sys.exit(1)

def main():
    config = load_config()
    
    # Инициализация
    print("Инициализация на база данни (TinyDB)...")
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

    # Състояние
    qr_cache = {}      
    last_side = {}     
    scan_cooldown = {}
    flash_frames = 0
    flash_color = (255, 255, 0)
    
    line_margin = config['system']['line_margin']
    scan_cooldown_sec = config['system']['scan_cooldown_sec']
    panel_w = config['ui']['dashboard_width']
    headless = config['ui'].get('headless', False)

    print("Системата е готова. Натисни 'q' за изход.")

    # Главен цикъл (вече не проверяваме isOpened, защото нишката се грижи за това)
    while True:
        success, frame = cap.read()
        if not success or frame is None:
            continue
            
        h, w = frame.shape[:2]
        # Изместване на виртуалната линия след Dashboard-a
        line_x = (w // 2) + 100 
        
        results = model.track(frame, persist=True, tracker="bytetrack.yaml", verbose=False)
        
        if flash_frames > 0:
            current_line_color = flash_color
            flash_frames -= 1
        else:
            current_line_color = (255, 255, 0)

        # Чертане на интерфейс
        inv_state = inventory_db.get_inventory_state()
        rec_logs = inventory_db.get_recent_logs()
        draw_dashboard(frame, inv_state, rec_logs, panel_w)

        if results[0].boxes is not None and results[0].boxes.id is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            track_ids = results[0].boxes.id.int().cpu().numpy()
            clss = results[0].boxes.cls.cpu().numpy()
            class_names = results[0].names
            
            for box, track_id, cls_idx in zip(boxes, track_ids, clss):
                x1, y1, x2, y2 = map(int, box)
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                
                yolo_class = class_names[int(cls_idx)]
                
                # Сканиране с cooldown
                if track_id not in qr_cache:
                    current_time = time.time()
                    if current_time - scan_cooldown.get(track_id, 0) > scan_cooldown_sec:
                        scan_cooldown[track_id] = current_time
                        
                        uid, code_type = scan_code_in_roi(frame, x1, y1, x2, y2)
                        if uid:
                            qr_prefix = uid.split('_')[0] if '_' in uid else uid
                            is_valid = (qr_prefix == yolo_class)
                            
                            qr_cache[track_id] = {
                                "uid": uid,
                                "valid": is_valid,
                                "type": code_type
                            }
                            
                            if not is_valid:
                                print(f"[АНТИ-SPOOF] Отхвърлено! YOLO: {yolo_class} != Code: {qr_prefix}")

                # Визуално оформление на bounding box
                box_color = (255, 165, 0) 
                info_text = f"ID:{track_id} {yolo_class} [Scan...]"
                
                if track_id in qr_cache:
                    qr_data = qr_cache[track_id]
                    c_type = qr_data['type']
                    if not qr_data["valid"]:
                        box_color = (0, 0, 255)
                        info_text = f"ID:{track_id} {yolo_class} [SPOOF {c_type}]"
                    else:
                        box_color = (0, 255, 0)
                        info_text = f"ID:{track_id} {yolo_class} [OK {c_type}]"
                        
                        # Логика за пресичане (Hysteresis)
                        current_side = None
                        if cx < line_x - line_margin:
                            current_side = 'L'
                        elif cx > line_x + line_margin:
                            current_side = 'R'
                            
                        if current_side is not None:
                            if track_id in last_side:
                                prev_side = last_side[track_id]
                                
                                if prev_side == 'L' and current_side == 'R':
                                    inventory_db.log_action(qr_data["uid"], yolo_class, 'ADDED')
                                    flash_color = (0, 255, 0) 
                                    flash_frames = config['ui']['flash_frames']
                                    
                                elif prev_side == 'R' and current_side == 'L':
                                    inventory_db.log_action(qr_data["uid"], yolo_class, 'REMOVED')
                                    flash_color = (0, 0, 255)
                                    flash_frames = config['ui']['flash_frames']
                                    
                            last_side[track_id] = current_side

                cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)
                cv2.circle(frame, (cx, cy), 5, box_color, -1)
                
                (tw, th), _ = cv2.getTextSize(info_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                cv2.rectangle(frame, (x1, y1 - 20), (x1 + tw, y1), (0, 0, 0), -1)
                cv2.putText(frame, info_text, (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        cv2.line(frame, (line_x, 0), (line_x, h), current_line_color, 2)
        cv2.putText(frame, "OUT", (line_x - 60, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.putText(frame, "IN (Warehouse)", (line_x + 20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

        if not headless:
            cv2.imshow("Smart Checkout Tracker", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            
    cap.stop()
    cv2.destroyAllWindows()
    print("Системата е спряна успешно.")

if __name__ == "__main__":
    main()
