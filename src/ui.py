import cv2

def draw_dashboard(frame, inventory_state, recent_logs, panel_w=320):
    """Чертае прозрачно информационно табло (Dashboard) в лявата част на екрана."""
    h, w = frame.shape[:2]
    
    # Създаваме полупрозрачен черен панел
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (panel_w, h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)
    
    # Заглавие "INVENTORY" (Наличности)
    y_pos = 30
    cv2.putText(frame, "- INVENTORY -", (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    y_pos += 25
    
    if not inventory_state:
        cv2.putText(frame, "Empty / No items", (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 150, 150), 1)
        y_pos += 25
    else:
        for cls, count in inventory_state.items():
            cv2.putText(frame, f"{cls}: {count} pcs", (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
            y_pos += 20
            
    # Заглавие "RECENT LOGS"
    y_pos += 20
    cv2.putText(frame, "- RECENT LOGS -", (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    y_pos += 25
    
    for log in recent_logs:
        color = (0, 255, 0) if log['action'] == "ADDED" else (0, 0, 255)
        cv2.putText(frame, log['text'], (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
        y_pos += 25
