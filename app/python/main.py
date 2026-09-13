import json
import logging
import queue
import threading
import time
from collections import deque
from concurrent.futures import Future, TimeoutError
from pathlib import Path

from fastapi import HTTPException
from arduino.app_utils import App
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.object_detection import ObjectDetection
from arduino.app_bricks.llm import LargeLanguageModel
from clues import MODEL, SYSTEM_PROMPT
from camera_feed import SharedCamera
from inference import FrameDetector
from recognition import MIN_CONFIDENCE, select_recognition

from controls import parse_command
from engine.events import GameEvent, GameEventType
from engine.game_engine import GameEngine
from voice import VoiceControl

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s', force=True)
log = logging.getLogger('scavenger.runtime')
ROOT = Path(__file__).resolve().parent
config = json.loads((ROOT / 'runtime.json').read_text())
CAMERA_CAPTURE_RESOLUTION = (1920, 1080)
CAMERA_RESOLUTION = (1440, 1080)  # Central 12:9 crop shared by TV and detector.
DETECTOR_RESOLUTION = (416, 416)
MAX_DETECTION_AGE = 1.5  # Discard old images; bounded to the guidance lifetime.
ui = WebUI(port=7000, cors_origins='')
llm = LargeLanguageModel(model=MODEL, system_prompt=SYSTEM_PROMPT, temperature=.2, max_tokens=64,
                         timeout=240, max_retries=0,
                         extra_body={'chat_template_kwargs': {'enable_thinking': False}})
engine = GameEngine(ui=ui, llm=llm, objects_path=ROOT / 'objects.json', camera_width=CAMERA_RESOLUTION[0])
engine.camera_ready = False
engine.inference_ready = False
engine.display_ready = False
pending = queue.Queue(maxsize=128)
recent_commands = deque(maxlen=100)
clients = set()
client_lock = threading.Lock()
health = {'last_frame_at': 0.0, 'last_loop_at': time.monotonic(), 'last_inference_at': 0.0, 'frames': 0, 'detections': 0, 'last_detection': {}, 'last_recognition': {'red': None, 'blue': None}, 'voice_status': 'Voice loading'}
health_lock = threading.Lock()


def frame_seen(frame):
    with health_lock:
        health['last_frame_at'] = time.monotonic()
        health['frames'] += 1
        health['frame_size'] = [int(frame.shape[1]), int(frame.shape[0])]
    return frame


camera = SharedCamera(resolution=CAMERA_RESOLUTION, capture_resolution=CAMERA_CAPTURE_RESOLUTION,
                      fps=30, codec='MJPG', on_frame=frame_seen)
# Guidance and scoring both accept detections at 50% confidence.
detection = ObjectDetection(confidence=MIN_CONFIDENCE)


def inference_seen(boxes, captured_at, duration):
    fresh = time.monotonic() - captured_at <= MAX_DETECTION_AGE
    selected = select_recognition(boxes if fresh else [], *CAMERA_RESOLUTION)
    detections = {}
    for box in boxes:
        label = box.get('label')
        if not isinstance(label, str) or not label.strip():
            continue
        detections.setdefault(label, []).append({'confidence': box['value'], 'team': box['team'],
            'bounding_box_xyxy': [box['x'], box['y'], box['x'] + box['width'], box['y'] + box['height']]})
    with health_lock:
        # Replies too old to score must not keep an unplayable round running.
        if fresh:
            health['last_inference_at'] = time.monotonic()
        health['last_recognition'] = selected
        health['detections'] += bool(detections)
        health['inference_frames'] = health.get('inference_frames', 0) + 1
        health['last_detection'] = detections
        health['inference_seconds'] = round(duration, 3)
        health['inference_frame_age_seconds'] = round(time.monotonic() - captured_at, 3)
    ui.send_message('recognition', selected)
    if fresh:
        enqueue(GameEvent(GameEventType.DETECTION, detections), 'camera', captured_at=captured_at)


frame_detection = FrameDetector(camera, detection.url, inference_seen, DETECTOR_RESOLUTION)


def enqueue(event, source='internal', future=None, captured_at=None):
    try:
        pending.put_nowait((event, source, time.monotonic() if captured_at is None else captured_at, future))
    except queue.Full:
        if future:
            future.set_exception(ValueError('Game command queue is busy; retry'))
        elif event.type != GameEventType.DETECTION:
            log.warning('Command queue full: %s', event.type.value)


def run_command(command, arguments, source='cli'):
    try:
        event = parse_command(command, arguments)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    future = Future()
    enqueue(event, source, future)
    try:
        return future.result(timeout=3)
    except TimeoutError as exc:
        # A timed-out queued request must never execute later unexpectedly.
        future.cancel()
        raise HTTPException(503, 'Game loop is busy; command cancelled') from exc
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc


def status():
    snapshot = engine.snapshot()
    with health_lock:
        details = dict(health)
    details['frame_age_seconds'] = round(time.monotonic() - details.pop('last_frame_at'), 3)
    details['loop_age_seconds'] = round(time.monotonic() - details.pop('last_loop_at'), 3)
    details['inference_age_seconds'] = round(time.monotonic() - details.pop('last_inference_at'), 3)
    details['connected_displays'] = len(clients)
    details['test_mode'] = config.get('test_mode', False)
    details['clues'] = 'local_llm'
    with engine._lock:
        details['llm'] = {'model': MODEL, 'generated': engine.clues_generated,
                         'last_generation_seconds': engine.clue_last_seconds,
                         'error': engine.clue_error, 'ready_rounds': sorted(i + 1 for i in engine.round_clues)}
    details['capture_resolution'] = CAMERA_CAPTURE_RESOLUTION
    details['camera_crop'] = [240, 0, *CAMERA_RESOLUTION]
    details['detector_resolution'] = DETECTOR_RESOLUTION
    details['detector_regions'] = {'full': [0, 0, *CAMERA_RESOLUTION]}
    details['detector_mode'] = 'full-frame'
    snapshot['runtime'] = details
    if config.get('test_mode'):
        snapshot['test_target_label'] = engine.state.target_label
    return snapshot


def command_api(payload: dict):
    if not isinstance(payload.get('command'), str):
        raise HTTPException(400, 'A command name is required')
    return run_command(payload['command'], payload.get('arguments', {}))


def test_detection(payload: dict):
    if config.get('test_mode') is not True:
        raise HTTPException(403, 'Simulation is disabled in normal play')
    player = payload.get('player')
    if player not in ('red', 'blue'):
        raise HTTPException(400, 'Player must be red or blue')
    label = payload.get('label')
    if not isinstance(label, str) or len(label) > 80:
        raise HTTPException(400, 'Provide the target label explicitly')
    x = int(engine.camera_width * (.2 if player == 'red' else .7))
    data = {label: [{'confidence': .99, 'bounding_box_xyxy': [x, 50, x+int(engine.camera_width * .1), 170]}]}
    future = Future()
    enqueue(GameEvent(GameEventType.DETECTION, data), 'SIMULATED_TEST', future)
    try:
        return future.result(timeout=3)
    except TimeoutError as exc:
        future.cancel()
        raise HTTPException(503, 'Simulation cancelled: game loop busy') from exc


def on_connect(sid):
    with client_lock:
        clients.add(sid)
        enqueue(GameEvent(GameEventType.DISPLAY_STATUS, {'ready': True}), 'browser-connect')


def on_disconnect(sid):
    with client_lock:
        clients.discard(sid)
        enqueue(GameEvent(GameEventType.DISPLAY_STATUS, {'ready': bool(clients)}), 'browser-disconnect')


def voice_status(message):
    with health_lock:
        health['voice_status'] = message
    engine.voice_status = message
    log.info('voice_status=%s', message)


voice = VoiceControl(config['voice_model_path'], lambda command: enqueue(GameEvent(GameEventType.VOICE_COMMAND, {'command': command}), 'microphone'), voice_status)


def loop():
    with health_lock:
        health['last_loop_at'] = time.monotonic()
        frame_age = time.monotonic() - health['last_frame_at']
        inference_age = time.monotonic() - health['last_inference_at']
    ready = frame_age < 3 and camera.status == 'streaming'
    if ready != engine.camera_ready:
        engine.handle(GameEvent(GameEventType.CAMERA_STATUS, {'ready': ready}))
    if (inference_age < 5) != engine.inference_ready:
        engine.handle(GameEvent(GameEventType.INFERENCE_STATUS, {'ready': inference_age < 5}))
    for _ in range(32):
        try:
            event, source, received_at, future = pending.get_nowait()
        except queue.Empty:
            break
        if future and not future.set_running_or_notify_cancel():
            continue
        # Prevent queued camera frames being applied to a later round.
        stale = event.type == GameEventType.DETECTION and (time.monotonic() - received_at > MAX_DETECTION_AGE or received_at < engine.accept_detections_after)
        accepted = False if stale else engine.handle(event)
        result = {'accepted': bool(accepted), 'command': event.type.value, 'source': source, 'state': engine.snapshot()}
        if event.type != GameEventType.DETECTION or source == 'SIMULATED_TEST':
            record = {'at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'command': event.type.value, 'source': source, 'accepted': bool(accepted), 'phase': result['state']['phase']}
            recent_commands.append(record)
            log.info('control %s', json.dumps(record))
        if future:
            future.set_result(result)
    engine.tick()
    time.sleep(.05)


ui.expose_api('GET', '/api/status', status)
ui.expose_api('GET', '/api/commands', lambda: list(recent_commands))
ui.expose_api('POST', '/api/command', command_api)
ui.expose_api('POST', '/api/test/detection', test_detection)
ui.on_connect(on_connect)
ui.on_disconnect(on_disconnect)
for name, command in {
    'start_game':'start', 'show_tutorial':'tutorial', 'open_settings':'settings',
    'set_round_seconds':'duration', 'back_to_menu':'menu', 'pause_game':'pause', 'resume_game':'resume',
}.items():
    ui.on_message(name, lambda sid, data, command=command: run_command(command, data or {}, 'browser'))

# The camera supports automatic reconnection. Start it before exposing MJPEG.
camera.start()
frame_detection.start()
ui.expose_camera('/camera', camera)
engine.boot()
if config.get('voice_enabled', True):
    voice.start()
else:
    voice_status('Voice disabled · use buttons or CLI')
log.info('Game ready; API=/api/status control=/api/command test_mode=%s clues=local_llm model=%s', config.get('test_mode'), MODEL)
try:
    App.run(user_loop=loop)
finally:
    voice.stop()
    frame_detection.stop()
    camera.stop()
