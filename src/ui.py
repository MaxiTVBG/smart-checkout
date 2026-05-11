import cv2
import numpy as np

def draw_hud(frame, inventory_state, recent_logs):
    """Рисува модерен прозрачен HUD върху кадъра, без да закрива работното поле."""
    h, w = frame.shape[:2]
    
    # Оптимизация: Вместо frame.copy() за целия 1080p кадър,
    # рисуваме правоъгълниците директно с полупрозрачност чрез sub-ROI overlay.
    # Това спестява ~4MB копиране на всеки кадър.
    
    # 1. Лента с наличности (Долу) — sub-ROI overlay
    bar_roi = frame[h - 40:h, 0:w]
    bar_overlay = bar_roi.copy()
    cv2.rectangle(bar_overlay, (0, 0), (w, 40), (0, 0, 0), -1)
    cv2.addWeighted(bar_overlay, 0.6, bar_roi, 0.4, 0, bar_roi)
    
    # 2. Балони за логове (Горе вляво и Горе вдясно) — sub-ROI overlay
    left_roi = frame[10:100, 10:w // 2 - 20]
    left_overlay = left_roi.copy()
    cv2.rectangle(left_overlay, (0, 0), (left_roi.shape[1], left_roi.shape[0]), (0, 0, 0), -1)
    cv2.addWeighted(left_overlay, 0.6, left_roi, 0.4, 0, left_roi)
    
    right_roi = frame[10:100, w // 2 + 20:w - 10]
    right_overlay = right_roi.copy()
    cv2.rectangle(right_overlay, (0, 0), (right_roi.shape[1], right_roi.shape[0]), (0, 0, 0), -1)
    cv2.addWeighted(right_overlay, 0.6, right_roi, 0.4, 0, right_roi)
    
    # --- ИЗРИСУВАНЕ НА ТЕКСТОВЕТЕ ---
    
    # Наличности (Най-отдолу)
    inv_text = "SKLAD: "
    if not inventory_state:
        inv_text += "Prazen"
    else:
        items = [f"{cls}({count})" for cls, count in inventory_state.items()]
        inv_text += " | ".join(items)
        
    # Ограничаване на дължината, ако е прекалено дълго
    if len(inv_text) > 75:
        inv_text = inv_text[:72] + "..."
        
    cv2.putText(frame, inv_text, (10, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    
    # Разделяне на логовете
    added_logs = [l['text'] for l in recent_logs if l['action'] == "ADDED"][:3]
    removed_logs = [l['text'] for l in recent_logs if l['action'] == "REMOVED"][:3]
    
    # Ляв Балон (Вкарване)
    cv2.putText(frame, "[ POSLEDNO VKARANI ]", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
    y = 55
    for txt in added_logs:
        clean_txt = txt.replace(" ДОБАВЕН", "").replace(" ADDED", "")
        cv2.putText(frame, clean_txt, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 255, 200), 1)
        y += 20
        
    # Десен Балон (Изкарване)
    cv2.putText(frame, "[ POSLEDNO IZKARANI ]", (w // 2 + 30, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 165, 255), 1)
    y = 55
    for txt in removed_logs:
        clean_txt = txt.replace(" ИЗКАРАН", "").replace(" REMOVED", "")
        cv2.putText(frame, clean_txt, (w // 2 + 30, y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 255), 1)
        y += 20
