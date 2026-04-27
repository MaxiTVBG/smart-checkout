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
            
        # Четем първия кадър
        (self.grabbed, self.frame) = self.stream.read()
        self.stopped = False

    def start(self):
        # Стартираме нишката като Daemon (спира автоматично при затваряне на програмата)
        threading.Thread(target=self.update, args=(), daemon=True).start()
        return self

    def update(self):
        while not self.stopped:
            # Непрекъснато "източваме" буфера на камерата във фонов режим
            (self.grabbed, self.frame) = self.stream.read()

    def read(self):
        # Връщаме статуса и последния заснет кадър
        return self.grabbed, self.frame

    def stop(self):
        self.stopped = True
        self.stream.release()
