import cv2
from pylibdmtx.pylibdmtx import decode as dmtx_decode
from pyzbar.pyzbar import decode as qr_decode

def scan_code_in_roi(frame, x1, y1, x2, y2):
    """DataMatrix/QR скенер. Оптимизиран за висок detection rate."""
    h, w = frame.shape[:2]

    # 1. Голям padding — DataMatrix ИЗИСКВА широка Quiet Zone
    pad = 80
    x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
    x2, y2 = min(w, x2 + pad), min(h, y2 + pad)

    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return None, None

    roi = roi.copy()
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    # 2. Adaptive Threshold — чисто черно/бяло, имунно на сенки и отблясъци
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 51, 10
    )

    # 3. Без resize — пълна 1080p резолюция, без загуба на детайл
    try:
        codes = dmtx_decode(binary, max_count=1, timeout=150)
        if codes:
            return codes[0].data.decode('utf-8'), "DataMatrix"

        # Fallback: сурово grayscale (понякога бинаризацията пречи)
        codes = dmtx_decode(gray, max_count=1, timeout=150)
        if codes:
            return codes[0].data.decode('utf-8'), "DataMatrix"

        # QR/Barcode fallback
        qrs = qr_decode(binary)
        if qrs:
            return qrs[0].data.decode('utf-8'), "QR"
    except Exception:
        pass

    return None, None
