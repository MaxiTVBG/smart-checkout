import cv2
import threading
import time
import numpy as np

class CameraStream:
    """
    Dual-Resolution Camera Stream за Raspberry Pi 5.
    
    Заснема в 1080p, предоставя два изхода:
    - hires (1920x1080): за DataMatrix сканиране
    - lores (640x480):    за YOLO inference и UI рендериране
    
    Фоновата нишка предварително resize-ва, за да не товари main loop.
    """
    HIRES = (1920, 1080)
    LORES = (640, 480)
    RECONNECT_DELAY = 2.0

    def __init__(self, src=0):
        self._src = src
        self.stream = self._open_stream(src)

        grabbed, frame = self.stream.read()
        if not grabbed or frame is None:
            raise RuntimeError(
                f"ГРЕШКА: Камерата на индекс {src} не връща валидни кадри! "
                "Смени 'camera_index' в config.yaml!"
            )

        hires = cv2.resize(frame, self.HIRES)
        self._hires = hires
        self._lores = cv2.resize(hires, self.LORES, interpolation=cv2.INTER_AREA)
        self.grabbed = True
        self.stopped = False
        self._lock = threading.Lock()

    def _open_stream(self, src):
        stream = cv2.VideoCapture(src)
        if not stream.isOpened():
            raise RuntimeError(f"Грешка при отваряне на камера {src}!")
        stream.set(cv2.CAP_PROP_FRAME_WIDTH, self.HIRES[0])
        stream.set(cv2.CAP_PROP_FRAME_HEIGHT, self.HIRES[1])
        stream.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return stream

    def start(self):
        threading.Thread(target=self._update, daemon=True).start()
        return self

    def _update(self):
        consecutive_failures = 0
        while not self.stopped:
            grabbed, frame = self.stream.read()
            if not grabbed or frame is None:
                consecutive_failures += 1
                if consecutive_failures > 30:
                    print(f"[CAMERA] Reconnect след {self.RECONNECT_DELAY}s...")
                    self.stream.release()
                    time.sleep(self.RECONNECT_DELAY)
                    try:
                        self.stream = self._open_stream(self._src)
                        consecutive_failures = 0
                        print("[CAMERA] Камерата е свързана отново.")
                    except RuntimeError:
                        pass
                continue

            consecutive_failures = 0
            hires = cv2.resize(frame, self.HIRES)
            lores = cv2.resize(hires, self.LORES, interpolation=cv2.INTER_AREA)
            with self._lock:
                self.grabbed = True
                self._hires = hires
                self._lores = lores

    def read(self):
        """Връща (success, lores_frame, hires_frame)."""
        with self._lock:
            return self.grabbed, self._lores, self._hires

    def stop(self):
        self.stopped = True
        self.stream.release()
