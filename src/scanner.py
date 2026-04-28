import cv2
from pylibdmtx.pylibdmtx import decode as dmtx_decode
from pyzbar.pyzbar import decode as qr_decode

def scan_code_in_roi(frame, x1, y1, x2, y2):
    """
    Търси първо за Data Matrix, а ако не намери - търси за QR/Barcode.
    ROI-то се конвертира в Grayscale за драстично ускорение на pylibdmtx.
    """
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return None, None
        
    # -------------------------------------------------------------
    # ОПТИМИЗАЦИЯ ЗА СКОРОСТ
    # -------------------------------------------------------------
    # Конвертиране в черно-бяло. Намалява данните 3 пъти, което 
    # значително ускорява CPU-heavy алгоритъма на pylibdmtx.
    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    
    # ОПТИМИЗАЦИЯ СРЕЩУ ЗАБИВАНЕ: Смаляваме ROI, ако е твърде голямо.
    # Data Matrix се чете перфектно на малки резолюции, но pylibdmtx блокира (freeze)
    # на големи изображения (>200px) заради тежък алгоритъм.
    max_size = 150
    roi_h, roi_w = gray_roi.shape[:2]
    if roi_w > max_size or roi_h > max_size:
        scale = max_size / max(roi_w, roi_h)
        gray_roi = cv2.resize(gray_roi, (int(roi_w * scale), int(roi_h * scale)), interpolation=cv2.INTER_AREA)
        
    # 1. Приоритетно сканиране за Data Matrix (с max_count=1)
    dmtx_codes = dmtx_decode(gray_roi, max_count=1)
    if dmtx_codes:
        return dmtx_codes[0].data.decode('utf-8'), "DataMatrix"
        
    # 2. Резервно сканиране за стандартен QR/Barcode
    qrs = qr_decode(gray_roi)
    if qrs:
        return qrs[0].data.decode('utf-8'), "QR"
        
    return None, None
