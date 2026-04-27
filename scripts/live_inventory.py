from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
import time

import cv2
from ultralytics import YOLO

# Change this path when you want to use a different trained model.
MODEL_PATH = "best.pt"
DB_NAME = "inventory.db"

CAMERA_INDEX = 0
FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080
CAMERA_WARMUP_SECONDS = 2.0

TRACKER_CONFIG = "bytetrack.yaml"
CONFIDENCE_THRESHOLD = 0.40
INFERENCE_SIZE = 640

LINE_RATIO = 0.50
ROI_WIDTH_RATIO = 1.00
DEAD_ZONE_PX = 60

STALE_TRACK_SECONDS = 2.0
MIN_TRACK_HITS = 3
MIN_SIDE_FRAMES = 2
CLASS_HISTORY_SIZE = 8
MIN_CLASS_VOTES = 3
MAX_CENTER_JUMP_RATIO = 0.28

MIN_BOX_AREA_RATIO = 0.00001
MAX_BOX_AREA_RATIO = 0.98
MAX_BOX_WIDTH_RATIO = 0.98
MAX_BOX_HEIGHT_RATIO = 0.98


@dataclass
class TrackState:
    last_center_x: float
    last_seen: float
    hits: int = 0
    anchor_side: str | None = None
    current_side: str | None = None
    current_side_frames: int = 0
    locked_class: str | None = None
    class_history: deque = field(default_factory=lambda: deque(maxlen=CLASS_HISTORY_SIZE))


def open_db():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    with conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS items (name TEXT PRIMARY KEY, quantity INTEGER NOT NULL DEFAULT 0)"
        )
    return conn


def init_inventory_rows(conn, class_names):
    with conn:
        for class_name in class_names:
            conn.execute(
                "INSERT OR IGNORE INTO items (name, quantity) VALUES (?, 0)",
                (class_name,),
            )


def load_inventory(conn, class_names):
    inventory = {class_name: 0 for class_name in class_names}
    placeholders = ",".join("?" for _ in class_names)
    rows = conn.execute(
        f"SELECT name, quantity FROM items WHERE name IN ({placeholders}) ORDER BY name",
        tuple(class_names),
    ).fetchall()
    
    for name, quantity in rows:
        inventory[name] = quantity
    return inventory


def update_inventory(conn, item_name, delta):
    with conn:
        conn.execute(
            "UPDATE items SET quantity = MAX(quantity + ?, 0) WHERE name = ?",
            (delta, item_name),
        )
    row = conn.execute("SELECT quantity FROM items WHERE name = ?", (item_name,)).fetchone()
    return row[0] if row else 0


def get_class_names(model):
    if isinstance(model.names, dict):
        return [model.names[index] for index in sorted(model.names)]
    return list(model.names)


def get_roi_bounds(frame_width, line_x):
    roi_width = max(int(frame_width * ROI_WIDTH_RATIO), 1)
    roi_x1 = max(line_x - (roi_width // 2), 0)
    roi_x2 = min(roi_x1 + roi_width, frame_width)
    roi_x1 = max(roi_x2 - roi_width, 0)
    return roi_x1, roi_x2


def line_side(center_x, line_x):
    if center_x < line_x - DEAD_ZONE_PX:
        return "left"
    if center_x > line_x + DEAD_ZONE_PX:
        return "right"
    return "middle"


def box_is_valid(x1, y1, x2, y2, frame_width, frame_height, roi_x1, roi_x2):
    box_width = max(x2 - x1, 0.0)
    box_height = max(y2 - y1, 0.0)
    frame_area = float(frame_width * frame_height)
    
    if frame_area <= 0.0 or box_width <= 0.0 or box_height <= 0.0:
        return False

    # FIX: Added the missing ROI check
    center_x = (x1 + x2) / 2.0
    if not (roi_x1 <= center_x <= roi_x2):
        return False

    area_ratio = (box_width * box_height) / frame_area
    width_ratio = box_width / frame_width
    height_ratio = box_height / frame_height

    return (
        MIN_BOX_AREA_RATIO <= area_ratio <= MAX_BOX_AREA_RATIO
        and width_ratio <= MAX_BOX_WIDTH_RATIO
        and height_ratio <= MAX_BOX_HEIGHT_RATIO
    )


def stable_class_name(state):
    if len(state.class_history) < MIN_CLASS_VOTES:
        return None

    counts = Counter(state.class_history)
    class_name, votes = counts.most_common(1)[0]
    if votes < MIN_CLASS_VOTES:
        return None
    return class_name


def reset_track(track_states, track_id, center_x, now, class_name):
    state = TrackState(last_center_x=center_x, last_seen=now, hits=1)
    state.class_history.append(class_name)
    track_states[track_id] = state
    return state


def track_needs_reset(state, center_x, now, frame_width):
    if now - state.last_seen > STALE_TRACK_SECONDS:
        return True
    max_jump = frame_width * MAX_CENTER_JUMP_RATIO
    if abs(center_x - state.last_center_x) > max_jump:
        return True
    return False


def observe_track(state, class_name, center_x, side, now):
    state.last_seen = now
    state.last_center_x = center_x
    state.hits += 1
    state.class_history.append(class_name)

    candidate_class = stable_class_name(state)
    if state.locked_class is None and candidate_class is not None:
        state.locked_class = candidate_class
    elif candidate_class is not None and state.locked_class != candidate_class:
        return "reset"

    if side == "middle":
        return None

    if state.current_side == side:
        state.current_side_frames += 1
    else:
        state.current_side = side
        state.current_side_frames = 1

    if state.anchor_side is None:
        if state.current_side_frames >= MIN_SIDE_FRAMES:
            state.anchor_side = side
        return None

    if (
        side != state.anchor_side
        and state.current_side_frames >= MIN_SIDE_FRAMES
        and state.hits >= MIN_TRACK_HITS
        and state.locked_class is not None
    ):
        previous_side = state.anchor_side
        state.anchor_side = side
        if previous_side == "left" and side == "right":
            return "in"
        if previous_side == "right" and side == "left":
            return "out"

    return None


def prune_stale_tracks(track_states, now):
    stale_ids = [
        track_id
        for track_id, state in track_states.items()
        if now - state.last_seen > STALE_TRACK_SECONDS
    ]
    for track_id in stale_ids:
        del track_states[track_id]


def draw_overlay(frame, inventory, line_x, roi_x1, roi_x2):
    frame_height, _ = frame.shape[:2]
    
    # Draw tracking line
    cv2.line(frame, (line_x, 0), (line_x, frame_height), (255, 0, 0), 2)
    cv2.putText(frame, "IN ->", (line_x + 10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    cv2.putText(frame, "<- OUT", (line_x - 100, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

    # Draw inventory
    y_offset = 70
    for item_name, count in inventory.items():
        cv2.putText(
            frame,
            f"{item_name}: {count}",
            (20, y_offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
        )
        y_offset += 30


def main():
    model_path = Path(MODEL_PATH)
    if not model_path.exists():
        raise FileNotFoundError(f"Model file not found: {model_path}")

    print(f"Loading model: {model_path}")
    model = YOLO(str(model_path))
    class_names = get_class_names(model)

    conn = open_db()
    init_inventory_rows(conn, class_names)
    inventory = load_inventory(conn, class_names)

    print("Starting camera...")
    cap = cv2.VideoCapture(CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    time.sleep(CAMERA_WARMUP_SECONDS)

    if not cap.isOpened():
        conn.close()
        raise RuntimeError("Camera could not be opened.")

    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    line_x = int(frame_width * LINE_RATIO)
    roi_x1, roi_x2 = get_roi_bounds(frame_width, line_x)
    
    track_states = {}

    print("System is active. Press 'q' to quit.")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            results = model.track(
                frame,
                persist=True,
                verbose=False,
                imgsz=INFERENCE_SIZE,
                tracker=TRACKER_CONFIG,
                conf=CONFIDENCE_THRESHOLD,
            )

            annotated_frame = frame.copy()
            draw_overlay(annotated_frame, inventory, line_x, roi_x1, roi_x2)
            now = time.time()

            if results and results[0].boxes is not None and len(results[0].boxes) > 0:
                boxes_data = results[0].boxes
                
                boxes = boxes_data.xyxy.cpu().tolist()
                class_ids = boxes_data.cls.int().cpu().tolist()
                confidences = boxes_data.conf.cpu().tolist()
                
                if boxes_data.id is not None:
                    track_ids = boxes_data.id.int().cpu().tolist()
                else:
                    track_ids = [None] * len(boxes)

                for box, track_id, class_id, confidence in zip(boxes, track_ids, class_ids, confidences):
                    x1, y1, x2, y2 = box
                    if not box_is_valid(x1, y1, x2, y2, frame_width, frame_height, roi_x1, roi_x2):
                        continue

                    class_name = class_names[class_id]
                    label_name = class_name
                    label_color = (0, 255, 0)

                    if track_id is not None:
                        center_x = (x1 + x2) / 2.0
                        side = line_side(center_x, line_x)
                        state = track_states.get(track_id)

                        if state is None or track_needs_reset(state, center_x, now, frame_width):
                            state = reset_track(track_states, track_id, center_x, now, class_name)
                        else:
                            event = observe_track(state, class_name, center_x, side, now)
                            if event == "reset":
                                state = reset_track(track_states, track_id, center_x, now, class_name)
                                event = None

                            if event == "in" and state.locked_class is not None:
                                inventory[state.locked_class] = update_inventory(conn, state.locked_class, 1)
                                print(f"[+] ADDED: {state.locked_class} | Total: {inventory[state.locked_class]}")
                            elif event == "out" and state.locked_class is not None:
                                inventory[state.locked_class] = update_inventory(conn, state.locked_class, -1)
                                print(f"[-] REMOVED: {state.locked_class} | Total: {inventory[state.locked_class]}")

                        label_name = state.locked_class or class_name
                        label_text = f"{label_name} {confidence:.2f} id:{track_id}"
                    else:
                        label_text = f"{label_name} {confidence:.2f}"
                        label_color = (0, 200, 255)

                    cv2.rectangle(
                        annotated_frame,
                        (int(x1), int(y1)),
                        (int(x2), int(y2)),
                        label_color,
                        2,
                    )
                    cv2.putText(
                        annotated_frame,
                        label_text,
                        (int(x1), max(int(y1) - 10, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        label_color,
                        2,
                    )

            prune_stale_tracks(track_states, now)
            cv2.imshow("Smart Workshop Inventory", annotated_frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        conn.close()
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()