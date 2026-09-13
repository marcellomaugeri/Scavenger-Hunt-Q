"""Select visible recognition guidance independently of the scoring rules."""
import math

MIN_CONFIDENCE = .5
MIN_FRAME_AREA = .05


def frame_layout(frame_width, frame_height, detector_size=(416, 416)):
    """Fit the complete camera frame into the model input without distortion."""
    if frame_width < 1 or frame_height < 1 or min(detector_size) < 1:
        raise ValueError('Positive camera and detector dimensions are required')
    input_width, input_height = detector_size
    scale = min(input_width / frame_width, input_height / frame_height)
    resized_width, resized_height = max(1, round(frame_width * scale)), max(1, round(frame_height * scale))
    return {'width': frame_width, 'height': frame_height,
            'resized_width': resized_width, 'resized_height': resized_height,
            'pad_x': (input_width - resized_width) // 2,
            'pad_y': (input_height - resized_height) // 2}


def map_frame_boxes(boxes, frame_width, frame_height, detector_size=(416, 416)):
    """Remove padding, restore TV coordinates and assign each box to one team."""
    layout = frame_layout(frame_width, frame_height, detector_size)
    if not isinstance(boxes, list):
        raise ValueError('Detector response has no bounding-box list')
    mapped = []
    px, py = layout['pad_x'], layout['pad_y']
    rw, rh = layout['resized_width'], layout['resized_height']
    for box in boxes:
        if not isinstance(box, dict):
            continue
        values = [box.get(key) for key in ('value', 'x', 'y', 'width', 'height')]
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            continue
        confidence, x, y, width, height = values
        if not MIN_CONFIDENCE <= confidence <= 1 or width <= 0 or height <= 0:
            continue
        x1, y1 = max(px, x), max(py, y)
        x2, y2 = min(px + rw, x + width), min(py + rh, y + height)
        if x2 <= x1 or y2 <= y1:
            continue
        team = 'red' if (x1 + x2) / 2 < px + rw / 2 else 'blue'
        mapped.append({**box, 'team': team,
                       'x': (x1 - px) * frame_width / rw,
                       'y': (y1 - py) * frame_height / rh,
                       'width': (x2 - x1) * frame_width / rw,
                       'height': (y2 - y1) * frame_height / rh})
    return mapped


def select_recognition(boxes, frame_width, frame_height):
    selected = {'red': None, 'blue': None}
    ranks = {'red': 0.0, 'blue': 0.0}
    if not isinstance(boxes, list):
        return selected
    frame_area = frame_width * frame_height
    for box in boxes:
        if not isinstance(box, dict):
            continue
        label = box.get('label')
        values = [box.get(key) for key in ('value', 'x', 'y', 'width', 'height')]
        if not isinstance(label, str) or not label.strip() or len(label) > 80:
            continue
        if label.strip().casefold() == 'person':
            continue
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
            continue
        confidence, x, y, width, height = values
        if not MIN_CONFIDENCE <= confidence <= 1 or width <= 0 or height <= 0:
            continue
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(frame_width, x + width), min(frame_height, y + height)
        if x2 <= x1 or y2 <= y1:
            continue
        area_fraction = (x2 - x1) * (y2 - y1) / frame_area
        if area_fraction < MIN_FRAME_AREA:
            continue
        team = box.get('team')
        if team not in selected:
            team = 'red' if x + width / 2 < frame_width / 2 else 'blue'
        rank = confidence * area_fraction
        if rank > ranks[team]:
            ranks[team] = rank
            selected[team] = {'label': label.strip(), 'confidence': confidence,
                              'box': [x1, y1, x2, y2], 'area_fraction': area_fraction}
    return selected
