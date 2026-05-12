import threading
import time

class Detection:
    def __init__(self, track_id, box, class_name):
        self.track_id = track_id
        self.box = box # [x1, y1, x2, y2]
        self.class_name = class_name

class VisionPipeline:
    """Базов клас за Vision пайплайн (Камера + AI Детекция)."""
    def start(self):
        return self

    def read(self):
        """Връща (success, lores_frame, hires_frame, detections)."""
        raise NotImplementedError

    def stop(self):
        pass


class MacVisionPipeline(VisionPipeline):
    """
    Имплементация за Mac/Windows/Linux.
    Използва съществуващия CameraStream (cv2) и YOLO в отделна нишка (CPU).
    """
    def __init__(self, camera_index, yolo_model_path):
        from src.camera import CameraStream
        from ultralytics import YOLO

        self.cap = CameraStream(camera_index)
        self.model = YOLO(yolo_model_path)
        
        self._result = []
        self._result_lock = threading.Lock()
        
        self._frame = None
        self._frame_lock = threading.Lock()
        
        self._new_frame = threading.Event()
        self._stopped = False
        
        self._thread = threading.Thread(target=self._worker, daemon=True)

    def start(self):
        self.cap.start()
        self._thread.start()
        return self

    def _worker(self):
        while not self._stopped:
            self._new_frame.wait(timeout=1.0)
            self._new_frame.clear()
            
            with self._frame_lock:
                frame = self._frame
                self._frame = None
                
            if frame is None:
                continue
                
            try:
                results = self.model.track(
                    frame, persist=True, tracker="bytetrack.yaml", verbose=False
                )
                
                detections = []
                if results and results[0].boxes is not None and results[0].boxes.id is not None:
                    boxes = results[0].boxes.xyxy.cpu().numpy()
                    track_ids = results[0].boxes.id.int().cpu().numpy()
                    clss = results[0].boxes.cls.cpu().numpy()
                    class_names = results[0].names
                    
                    for box, track_id, cls_idx in zip(boxes, track_ids, clss):
                        class_name = class_names[int(cls_idx)]
                        detections.append(Detection(track_id, box, class_name))

                with self._result_lock:
                    self._result = detections
            except Exception as e:
                print(f"[YOLO ERROR] {e}")

    def read(self):
        success, lores, hires = self.cap.read()
        
        if success:
            with self._frame_lock:
                self._frame = lores
            self._new_frame.set()
            
        with self._result_lock:
            current_detections = self._result
            
        return success, lores, hires, current_detections

    def stop(self):
        self._stopped = True
        self.cap.stop()


class PiAIVisionPipeline(VisionPipeline):
    """
    Имплементация за Raspberry Pi 5 с AI Camera (IMX500).
    Използва picamera2 за хардуерно ускорен inference, без ultralytics.
    Това е базова структура, която не крашва лаптопа, ако picamera2 липсва.
    """
    def __init__(self, camera_index=0, yolo_model_path=""):
        try:
            from picamera2 import Picamera2
        except ImportError:
            print("[PiAI] ВНИМАНИЕ: picamera2 не е инсталирана! Това е очаквано на Mac/Windows.")
            self.picam2 = None
            return

        self.picam2 = Picamera2()
        # Тук ще се добави конфигурацията за IMX500
        # self.picam2.configure(...)

    def start(self):
        if self.picam2:
            self.picam2.start()
        print("[PiAI] Pipeline стартиран (IMX500).")
        return self

    def read(self):
        if not self.picam2:
            time.sleep(1) # Fake delay
            return False, None, None, []
            
        # Реален код за Pi:
        # request = self.picam2.capture_request()
        # lores = request.make_array("lores")
        # hires = request.make_array("hires")
        # metadata = request.metadata
        # Извличане на detections от metadata...
        
        # Stub за момента:
        return False, None, None, []

    def stop(self):
        if self.picam2:
            self.picam2.stop()


def create_pipeline(config):
    """Factory функция за създаване на правилния пайплайн."""
    backend = config['system'].get('vision_backend', 'mac')
    camera_idx = config['system'].get('camera_index', 0)
    yolo_model = config['paths'].get('yolo_model', 'models/new_checkout_ncnn_model')
    
    if backend == 'pi_ai':
        return PiAIVisionPipeline(camera_index=camera_idx, yolo_model_path=yolo_model)
    else:
        return MacVisionPipeline(camera_index=camera_idx, yolo_model_path=yolo_model)
