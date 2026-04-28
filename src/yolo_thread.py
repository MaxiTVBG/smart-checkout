import threading
import time
from ultralytics import YOLO

class AsyncYOLO:
    """
    Управлява YOLO модела в отделна нишка (Thread).
    Това позволява на главната програма (UI) да върви с 30 FPS,
    докато YOLO обработва тежките 1080p кадри на заден фон с по-нисък FPS.
    """
    def __init__(self, model_path):
        self.model = YOLO(model_path)
        self.frame_to_process = None
        self.latest_results = None
        self.lock = threading.Lock()
        self.stopped = False

    def start(self):
        threading.Thread(target=self.update, args=(), daemon=True).start()
        return self

    def update(self):
        while not self.stopped:
            frame = None
            # Взимаме копие на кадъра за обработка безопасно (thread-safe)
            with self.lock:
                if self.frame_to_process is not None:
                    frame = self.frame_to_process.copy()
                    self.frame_to_process = None
                    
            if frame is not None:
                # YOLO обработва кадъра БЕЗ да блокира видеото!
                results = self.model.track(frame, persist=True, tracker="bytetrack.yaml", verbose=False)
                
                # Записваме резултата безопасно
                with self.lock:
                    if len(results) > 0:
                        self.latest_results = results[0]
            else:
                # Почивка, ако няма нов кадър, за да не "пържим" процесора излишно
                time.sleep(0.01)

    def process_frame(self, frame):
        """Подава нов кадър на YOLO нишката, ако тя е свободна."""
        with self.lock:
            if self.frame_to_process is None:
                self.frame_to_process = frame

    def get_results(self):
        """Връща последните намерени обекти."""
        with self.lock:
            return self.latest_results

    def stop(self):
        self.stopped = True
