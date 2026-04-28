import cv2

def draw_hud(frame, inventory_state, recent_logs):
    """Рисува модерен прозрачен HUD върху кадъра, без да закрива работното поле."""
    h, w = frame.shape[:2]
    overlay = frame.copy()
    
    # 1. Лента с наличности (Долу)
    cv2.rectangle(overlay, (0, h - 40), (w, h), (0, 0, 0), -1)
    
    # 2. Балони за логове (Горе вляво и Горе вдясно)
    cv2.rectangle(overlay, (10, 10), (w // 2 - 20, 100), (0, 0, 0), -1)
    cv2.rectangle(overlay, (w // 2 + 20, 10), (w - 10, 100), (0, 0, 0), -1)
    
    # Прилагаме прозрачността (Alpha blending)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)
    
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
