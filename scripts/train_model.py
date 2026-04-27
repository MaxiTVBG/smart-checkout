from ultralytics import YOLO

# Зареждаме най-бързия модел (Nano)
model = YOLO('yolov8n.pt')

# Стартираме тренирането, оптимизирано за Mac M4
results = model.train(
    data='data.yaml', 
    epochs=50,
    imgsz=640,
    batch=16,
    device='mps',      # Използва графичните ядра на M4
    name='inventory_model',
    cache=True,        
    workers=6          # 4 нишки за подреждане на данните
)