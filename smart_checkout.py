import cv2
import sqlite3
import datetime
import numpy as np
from ultralytics import YOLO
from pyzbar.pyzbar import decode

# ==============================================================================
# КОНФИГУРАЦИЯ НА СИСТЕМАТА
# ==============================================================================
DB_PATH = 'inventory.db'
YOLO_MODEL_PATH = 'yolov8n.pt'  # Замени с твоя custom trained YOLO модел ('best.pt')
CAMERA_INDEX = 0                # За USB камера; за CSI може да се наложи друга стойност или pipeline
LINE_MARGIN = 20                # Мъртва зона около виртуалната линия (против трептене/jitter)

# ==============================================================================
# БАЗА ДАННИ (SQLite)
# ==============================================================================
def init_db(db_path=DB_PATH):
    """Инициализира SQLite базата данни и създава необходимите таблици."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # Таблица за моментното състояние (наличности)
    c.execute('''
        CREATE TABLE IF NOT EXISTS items (
            uid TEXT PRIMARY KEY,
            item_class TEXT,
            in_stock INTEGER
        )
    ''')
    
    # Таблица за история (логове)
    c.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT,
            action TEXT,
            timestamp DATETIME
        )
    ''')
    conn.commit()
    return conn

def log_action(conn, uid, item_class, action):
    """
    Записва събитие (ADDED / REMOVED) в базата.
    Обновява наличността и добавя запис в историята.
    """
    c = conn.cursor()
    stock_status = 1 if action == 'ADDED' else 0
    
    # Добавяме артикула, или ако вече съществува - само му обновяваме статуса
    c.execute('''
        INSERT INTO items (uid, item_class, in_stock) 
        VALUES (?, ?, ?)
        ON CONFLICT(uid) DO UPDATE SET in_stock = ?
    ''', (uid, item_class, stock_status, stock_status))
        
    # Записваме в лог таблицата
    c.execute('INSERT INTO logs (uid, action, timestamp) VALUES (?, ?, ?)', 
              (uid, action, datetime.datetime.now()))
    
    conn.commit()
    print(f"[{action}] UID: {uid} | Class: {item_class}")

# ==============================================================================
# КОМПЮТЪРНО ЗРЕНИЕ & ЛОГИКА
# ==============================================================================
def scan_qr_in_roi(frame, x1, y1, x2, y2):
    """
    Изрязва ROI (Region of Interest) на базата на YOLO bounding box-a 
    и го подава на pyzbar за избягване на излишно натоварване на процесора.
    """
    # Защита от излизане на координатите извън границите на изображението
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    
    roi = frame[y1:y2, x1:x2]
    
    if roi.size == 0:
        return None
        
    qrs = decode(roi)
    if qrs:
        # Връщаме първия успешно разчетен QR код като стринг
        return qrs[0].data.decode('utf-8')
    return None

def main():
    print("Зареждане на YOLO модел...")
    # Използва се YOLOv8 Nano по подразбиране (може да се ползва и YOLOv11)
    model = YOLO(YOLO_MODEL_PATH)
    
    print("Инициализация на база данни...")
    conn = init_db()
    
    print("Свързване с камерата...")
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("Грешка при отваряне на камерата!")
        return

    # === Tracking State ===
    # Запазва информация за прочетени QR кодове: track_id -> {"uid": string, "valid": bool}
    qr_cache = {}      
    # Запазва последната позиция на всеки обект (Ляво 'L' или Дясно 'R') спрямо линията
    last_side = {}     
    
    # Визуални настройки за линията
    flash_frames = 0
    flash_color = (255, 255, 0) # По подразбиране е Cyan (жълто-синьо)

    print("Системата е готова и работи. Натисни 'q' за изход.")

    while cap.isOpened():
        success, frame = cap.read()
        if not success:
            break
            
        h, w = frame.shape[:2]
        line_x = w // 2 # Виртуална вертикална линия в центъра
        
        # Проследяване с вградения в Ultralytics ByteTrack tracker (persist=True запазва ID-тата)
        results = model.track(frame, persist=True, tracker="bytetrack.yaml", verbose=False)
        
        # Анимация на виртуалната линия при пресичане (зелено/червено премигване)
        if flash_frames > 0:
            current_line_color = flash_color
            flash_frames -= 1
        else:
            current_line_color = (255, 255, 0) # Нормален цвят

        # Ако са разпознати обекти с активни Track IDs
        if results[0].boxes is not None and results[0].boxes.id is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            track_ids = results[0].boxes.id.int().cpu().numpy()
            clss = results[0].boxes.cls.cpu().numpy()
            class_names = results[0].names
            
            for box, track_id, cls_idx in zip(boxes, track_ids, clss):
                x1, y1, x2, y2 = map(int, box)
                
                # Намираме центъра на обекта (за пресичането)
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                
                yolo_class = class_names[int(cls_idx)]
                
                # -----------------------------------------------------------
                # 1. СКАНИРАНЕ И ВАЛИДАЦИЯ НА QR (С КЕШИРАНЕ)
                # -----------------------------------------------------------
                # Сканираме само ако вече нямаме кеширан QR за това Track ID
                if track_id not in qr_cache:
                    uid = scan_qr_in_roi(frame, x1, y1, x2, y2)
                    if uid:
                        # Разделяне на "class_randomseed" и вземане на префикса
                        qr_prefix = uid.split('_')[0] if '_' in uid else uid
                        
                        # Hybrid Validation: YOLO Class == QR Prefix
                        is_valid = (qr_prefix == yolo_class)
                        
                        qr_cache[track_id] = {
                            "uid": uid,
                            "valid": is_valid
                        }
                        
                        if not is_valid:
                            print(f"[АНТИ-SPOOF] Отхвърлено! YOLO: {yolo_class} != QR: {qr_prefix}")

                # Настройки на UI цветовете според статуса
                box_color = (255, 165, 0) # Оранжево: Чака се разчитане на QR
                info_text = f"ID:{track_id} {yolo_class} [QR Search...]"
                
                if track_id in qr_cache:
                    qr_data = qr_cache[track_id]
                    if not qr_data["valid"]:
                        box_color = (0, 0, 255) # Червено: Аномалия (Spoof)
                        info_text = f"ID:{track_id} {yolo_class} [SPOOF: {qr_data['uid']}]"
                    else:
                        box_color = (0, 255, 0) # Зелено: Валидиран и сигурен
                        info_text = f"ID:{track_id} {yolo_class} [OK: {qr_data['uid']}]"
                        
                        # ---------------------------------------------------
                        # 2. ПРОСЛЕДЯВАНЕ И ЛОГИКА ЗА ПРЕСИЧАНЕ (Hysteresis)
                        # ---------------------------------------------------
                        # Използваме 'марж' около линията, за да предотвратим 
                        # многократни отчитания, ако обектът трепери върху нея.
                        current_side = None
                        if cx < line_x - LINE_MARGIN:
                            current_side = 'L'
                        elif cx > line_x + LINE_MARGIN:
                            current_side = 'R'
                            
                        if current_side is not None:
                            if track_id in last_side:
                                prev_side = last_side[track_id]
                                
                                # Пресичане от ЛЯВО надясно -> ВЛИЗА В СКЛАДА
                                if prev_side == 'L' and current_side == 'R':
                                    log_action(conn, qr_data["uid"], yolo_class, 'ADDED')
                                    flash_color = (0, 255, 0) # Зелена линия
                                    flash_frames = 15
                                    
                                # Пресичане от ДЯСНО наляво -> ИЗЛИЗА ОТ СКЛАДА
                                elif prev_side == 'R' and current_side == 'L':
                                    log_action(conn, qr_data["uid"], yolo_class, 'REMOVED')
                                    flash_color = (0, 0, 255) # Червена линия
                                    flash_frames = 15
                                    
                            # Обновяване на състоянието за следващия кадър
                            last_side[track_id] = current_side

                # -----------------------------------------------------------
                # 3. ВИЗУАЛИЗАЦИЯ (Очертания и Текст)
                # -----------------------------------------------------------
                cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)
                cv2.circle(frame, (cx, cy), 5, box_color, -1)
                
                # Черен фон зад текста за по-добра четимост
                (tw, th), _ = cv2.getTextSize(info_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                cv2.rectangle(frame, (x1, y1 - 20), (x1 + tw, y1), (0, 0, 0), -1)
                cv2.putText(frame, info_text, (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        # -----------------------------------------------------------
        # 4. ОБЩИ ИНТЕРФЕЙСНИ ЕЛЕМЕНТИ (UI)
        # -----------------------------------------------------------
        cv2.line(frame, (line_x, 0), (line_x, h), current_line_color, 2)
        cv2.putText(frame, "OUT (Left)", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.putText(frame, "IN / WAREHOUSE (Right)", (line_x + 20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

        cv2.imshow("Smart Checkout Tracker", frame)
        
        # Излизане от цикъла при натискане на 'q'
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
            
    # Затваряне на ресурсите
    cap.release()
    cv2.destroyAllWindows()
    conn.close()
    print("Системата е спряна успешно.")

if __name__ == "__main__":
    main()
