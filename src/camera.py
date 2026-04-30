import cv2
import threading

class CameraStream:
    """
    Чете кадри от камерата в отделна фонова нишка (Thread).
    
    Хардуерна Оптимизация:
    Ако главната програма чете кадрите линейно (synchronous), 
    тежките задачи като YOLO и DataMatrix декодирането забавят цикъла. 
    По време на забавянето хардуерният буфер на USB камерата се пълни със стари кадри, 
    което води до ОГРОМЕН ЛАГ на екрана.
    
    Тази нишка постоянно чете и изпразва хардуерния буфер, 
    а главната програма винаги взема само НАЙ-ПРЕСНИЯ наличен кадър.
    """
    def __init__(self, src=0):
        self.stream = cv2.VideoCapture(src)
        if not self.stream.isOpened():
            raise RuntimeError(f"Грешка при отваряне на камера {src}!")
            
        # Хардуерна оптимизация: Ограничаваме резолюцията до 640x480
        # YOLO не се нуждае от повече пиксели, а големите кадри сриват кадрите в секунда (FPS)
        self.stream.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        self.stream.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)

        # Четем първия кадър
        (self.grabbed, frame) = self.stream.read()
        if not self.grabbed or frame is None:
            raise RuntimeError(f"ГРЕШКА: Камерата на индекс {src} не връща валидни кадри! Смени 'camera_index' в config.yaml!")
            
        self.frame = cv2.resize(frame, (1920, 1080))
        self.stopped = False

    def start(self):
        # Стартираме нишката като Daemon (спира автоматично при затваряне на програмата)
        threading.Thread(target=self.update, args=(), daemon=True).start()
        return self

    def update(self):
        while not self.stopped:
            # Непрекъснато "източваме" буфера на камерата във фонов режим
            (grabbed, frame) = self.stream.read()
            self.grabbed = grabbed
            # Гарантираме 640x480 дори ако камерата игнорира CAP_PROP!
            if grabbed and frame is not None:
                self.frame = cv2.resize(frame, (1920, 1080))

    def read(self):
        # Връщаме статуса и последния заснет кадър
        return self.grabbed, self.frame

    def stop(self):
        self.stopped = True
        self.stream.release()
