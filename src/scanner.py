import cv2
from pylibdmtx.pylibdmtx import decode as dmtx_decode
from pyzbar.pyzbar import decode as qr_decode

def scan_code_in_roi(frame, x1, y1, x2, y2):
    """
    Търси първо за Data Matrix, а ако не намери - търси за QR/Barcode.
    """
    h, w = frame.shape[:2]
    
    # 1. ДОБАВЯНЕ НА PADDING (Много важно!)
    # Data Matrix кодът ЗАДЪЛЖИТЕЛНО има нужда от "Quiet Zone" (бяла рамка около него), 
    # за да бъде разпознат. Често YOLO изрязва точно по черния ръб на предмета и скенерът фейлва.
    pad = 20
    x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
    x2, y2 = min(w, x2 + pad), min(h, y2 + pad)
    
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return None, None
        
    # Конвертиране в черно-бяло
    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    
    # 2. ПОДОБРЯВАНЕ НА КОНТРАСТА (CLAHE)
    # Изсветлява белите и потъмнява черните зони, идеално за кодове при лоша светлина
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    enhanced_roi = clahe.apply(gray_roi)
    
    # 3. УВЕЛИЧАВАНЕ НА ЛИМИТА
    # Предишният лимит (150px) беше твърде малък и разваляше резолюцията на баркода.
    # Вдигаме го на 400px. Тъй като камерата вече снима в 640x480, рядко ще се налага resize.
    max_size = 400
    roi_h, roi_w = enhanced_roi.shape[:2]
    if roi_w > max_size or roi_h > max_size:
        scale = max_size / max(roi_w, roi_h)
        enhanced_roi = cv2.resize(enhanced_roi, (int(roi_w * scale), int(roi_h * scale)), interpolation=cv2.INTER_AREA)
        
    # 4. СКАНИРАНЕ С ТАЙМАУТ (Anti-Freeze)
    # Използваме вградения timeout параметър. Ако алгоритъмът не намери код до 100 милисекунди, 
    # просто се отказва. Това ни позволява да подаваме големи детайлни картинки БЕЗ малинката да забива!
    dmtx_codes = dmtx_decode(enhanced_roi, max_count=1, timeout=100)
    
    if dmtx_codes:
        return dmtx_codes[0].data.decode('utf-8'), "DataMatrix"
        
    # Ако не го хване от първия път, пробваме и без CLAHE (понякога е по-добре)
    dmtx_codes_fallback = dmtx_decode(gray_roi, max_count=1, timeout=100)
    if dmtx_codes_fallback:
        return dmtx_codes_fallback[0].data.decode('utf-8'), "DataMatrix"
        
    # Резервно сканиране за стандартен QR/Barcode
    qrs = qr_decode(gray_roi)
    if qrs:
        return qrs[0].data.decode('utf-8'), "QR"
        
    return None, None
