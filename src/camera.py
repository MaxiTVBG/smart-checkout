import cv2
import threading
import time

class CameraStream:
    """
    1080p Camera Stream за Raspberry Pi 5.
    
    Заснема и предоставя кадри изцяло в 1080p резолюция.
    Фоновата нишка постоянно източва хардуерния буфер на камерата,
    за да се избегне натрупване на стари кадри и лаг.
    
    При прекъсване на камерата автоматично опитва reconnect.
    """
    HIRES = (1920, 1080)
    RECONNECT_DELAY = 2.0  # Секунди между опитите за повторно свързване

    def __init__(self, src=0):
        self._src = src
        self.stream = self._open_stream(src)
        
        # Четем първия кадър за валидация
        grabbed, frame = self.stream.read()
        if not grabbed or frame is None:
            raise RuntimeError(
                f"ГРЕШКА: Камерата на индекс {src} не връща валидни кадри! "
                "Смени 'camera_index' в config.yaml!"
            )
            
        self.frame = cv2.resize(frame, self.HIRES)
        self.grabbed = True
        self.stopped = False
        self._lock = threading.Lock()

    def _open_stream(self, src):
        """Отваря камерата и настройва 1080p."""
        stream = cv2.VideoCapture(src)
        if not stream.isOpened():
            raise RuntimeError(f"Грешка при отваряне на камера {src}!")
        stream.set(cv2.CAP_PROP_FRAME_WIDTH, self.HIRES[0])
        stream.set(cv2.CAP_PROP_FRAME_HEIGHT, self.HIRES[1])
        stream.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return stream

    def start(self):
        threading.Thread(target=self._update, args=(), daemon=True).start()
        return self

    def _update(self):
        consecutive_failures = 0
        while not self.stopped:
            grabbed, frame = self.stream.read()
            if not grabbed or frame is None:
                consecutive_failures += 1
                if consecutive_failures > 30:
                    # Камерата е паднала — опитваме reconnect
                    print(f"[CAMERA] Камерата прекъсна. Reconnect след {self.RECONNECT_DELAY}s...")
                    self.stream.release()
                    time.sleep(self.RECONNECT_DELAY)
                    try:
                        self.stream = self._open_stream(self._src)
                        consecutive_failures = 0
                        print("[CAMERA] Камерата е свързана отново.")
                    except RuntimeError:
                        pass  # Ще опитаме пак на следващата итерация
                continue
            
            consecutive_failures = 0
            frame = cv2.resize(frame, self.HIRES)
            with self._lock:
                self.grabbed = True
                self.frame = frame

    def read(self):
        """Връща (success, frame)."""
        with self._lock:
            return self.grabbed, self.frame

    def stop(self):
        self.stopped = True
        self.stream.release()
