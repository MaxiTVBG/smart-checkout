import cv2

print("🔍 Търсене на активни камери...")

for i in range(5):
    cap = cv2.VideoCapture(i)
    if cap.isOpened():
        ret, frame = cap.read()
        if ret:
            print(f"✅ НАМЕРЕНА КАМЕРА НА ИНДЕКС: {i}")
            # Показва кадър от камерата за малко
            cv2.imshow(f"Camera Test - Index {i}", frame)
            cv2.waitKey(2000) # Държи прозореца отворен 2 секунди
            cv2.destroyAllWindows()
        cap.release()
    else:
        print(f"❌ Няма камера на индекс: {i}")

print("Търсенето приключи!")