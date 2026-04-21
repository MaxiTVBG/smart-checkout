import cv2
import time
import os

# ==========================================
# НАСТРОЙКИ ЗА ПРОЕКТА
# ==========================================
ITEM_NAME = "servo_motor_1"  # Променяй това за всеки нов предмет (или "negative_hand")
NUM_PHOTOS = 300              # Колко снимки искаш
DELAY = 0.5                  # Пауза между снимките (секунди)
# ==========================================

save_dir = f"dataset/{ITEM_NAME}"
os.makedirs(save_dir, exist_ok=True)

print("Стартиране на камерата с висока резолюция...")
cap = cv2.VideoCapture(0)

# Оптимизация за CZUR Камера
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)

time.sleep(2) # Време за загряване на сензора на Mac-а

if not cap.isOpened():
    print("Грешка: Камерата не може да бъде отворена.")
    exit()

print(f"--- Готовност за снимане на: {ITEM_NAME} ---")
print("Натисни 's' за СТАРТ. Натисни 'q' за ИЗХОД.")

capturing = False
count = 0
last_capture_time = 0

while True:
    ret, frame = cap.read()
    if not ret:
        break

    cv2.putText(frame, f"Obekt: {ITEM_NAME}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 0), 2)
    
    if not capturing:
        cv2.putText(frame, "Natisni 'S' za START", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
    else:
        cv2.putText(frame, f"Snimane: {count}/{NUM_PHOTOS}", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 3)
        
        current_time = time.time()
        if current_time - last_capture_time >= DELAY:
            filename = os.path.join(save_dir, f"{ITEM_NAME}_{count:03d}.jpg")
            cv2.imwrite(filename, frame)
            count += 1
            last_capture_time = current_time
            
            if count >= NUM_PHOTOS:
                print(f"Готово! {NUM_PHOTOS} снимки са запазени в {save_dir}")
                capturing = False
                count = 0

    cv2.imshow("Data Collection", frame)
    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'):
        break
    elif key == ord('s') and not capturing:
        capturing = True
        last_capture_time = time.time()
        print("Снимането започна!")

cap.release()
cv2.destroyAllWindows()