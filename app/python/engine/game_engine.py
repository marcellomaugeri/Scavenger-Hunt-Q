"""Authoritative rules. Runtime serializes inputs; the lock also protects snapshots."""
import json
import logging
import math
import random
import threading
import time
from pathlib import Path
from dataclasses import asdict

from .events import GameEvent, GameEventType
from .game_state import GamePhase, GameState, RoundRecap
from voice import VOICE_COMMANDS
from clues import generate_clue, MODEL

log = logging.getLogger('scavenger.engine')
IDLE = {GamePhase.MENU, GamePhase.SETTINGS, GamePhase.TUTORIAL, GamePhase.GAME_OVER}
ACTIVE = {GamePhase.PREPARING, GamePhase.COUNTDOWN, GamePhase.ROUND, GamePhase.ROUND_RESULT, GamePhase.RECOVERY}


class GameEngine:
    def __init__(self, ui, llm, objects_path: Path, camera_width: int):
        if llm is None:
            raise ValueError('A local LLM is required to generate riddles')
        self.ui = ui
        self.llm = llm
        self.objects_path = objects_path
        self.camera_width = camera_width
        self.state = GameState()
        self.objects = self._load_objects()
        if not self.objects:
            raise ValueError('At least one enabled target object is required')
        self.match_targets = []
        self.round_clues = {}
        self.clue_thread = None
        self._match_id = 0
        self._staged_match = False
        self._clue_retry_at = 0.0
        self.clue_error = ''
        self.clue_last_seconds = None
        self.clues_generated = 0
        self.last_state_sent = 0.0
        self.revision = 0
        self.camera_ready = True
        self.display_ready = True
        self.inference_ready = True
        self.recovery_reason = ''
        self.voice_status = 'Voice loading'
        self._lock = threading.RLock()
        self._paused_phase = None
        self._held_seconds = 0.0
        self._resume_round = False
        self.accept_detections_after = 0.0
        self._reset_until = 0.0

    def boot(self):
        with self._lock:
            if self.state.phase == GamePhase.BOOT:
                self.state.phase = GamePhase.LOADING
                # Count visible startup time, not time spent waiting for the TV.
                self._reset_until = time.monotonic() + 5 if self.display_ready else math.inf
            self._send_state()

    def handle(self, event: GameEvent):
        with self._lock:
            before = self.state.phase
            payload = event.payload or {}
            kind = event.type
            accepted = True
            if kind == GameEventType.START_GAME:
                accepted = self.start_game()
            elif kind == GameEventType.RESET_GAME:
                accepted = self.reset_game()
            elif kind in {GameEventType.SHOW_TUTORIAL, GameEventType.OPEN_SETTINGS}:
                accepted = self.state.phase in IDLE
                if accepted:
                    self.state.phase = GamePhase.TUTORIAL if kind == GameEventType.SHOW_TUTORIAL else GamePhase.SETTINGS
            elif kind == GameEventType.SET_ROUND_SECONDS:
                seconds = payload.get('seconds')
                accepted = self.state.phase in IDLE and type(seconds) is int and seconds in (30, 60, 120)
                if accepted:
                    self.state.round_seconds = seconds
            elif kind == GameEventType.BACK_TO_MENU:
                accepted = self.state.phase != GamePhase.LOADING
                if accepted:
                    if not self._staged_match:
                        self._match_id += 1  # Discard a reply from the abandoned match.
                    self.state.phase = GamePhase.MENU
                    self.recovery_reason = ''
                    self._paused_phase = None
                    self._resume_round = False
            elif kind == GameEventType.DETECTION:
                self.handle_detection(payload)
            elif kind == GameEventType.VOICE_COMMAND:
                accepted = self.handle_voice_command(payload.get('command', ''))
            elif kind == GameEventType.PAUSE_GAME:
                accepted = self.pause('paused')
            elif kind == GameEventType.RESUME_GAME:
                accepted = self.resume()
            elif kind in {GameEventType.CAMERA_STATUS, GameEventType.DISPLAY_STATUS, GameEventType.INFERENCE_STATUS}:
                ready = payload.get('ready') is True
                reason = {GameEventType.CAMERA_STATUS:'camera', GameEventType.DISPLAY_STATUS:'connection', GameEventType.INFERENCE_STATUS:'recognition'}[kind]
                if reason == 'camera':
                    self.camera_ready = ready
                elif reason == 'recognition':
                    self.inference_ready = ready
                else:
                    self.display_ready = ready
                    if ready and self.state.phase == GamePhase.LOADING and self._reset_until == math.inf:
                        self._reset_until = time.monotonic() + 5
                if not ready:
                    self.pause(reason)
                elif self.state.phase == GamePhase.RECOVERY and self.recovery_reason != 'paused':
                    self.resume()
            else:
                accepted = False
            if kind != GameEventType.DETECTION:
                log.info('event=%s accepted=%s phase=%s->%s round=%s', kind.value, accepted, before.value, self.state.phase.value, self.state.current_round + 1)
                self._send_state()
            return accepted

    def reset_game(self):
        if self.state.phase == GamePhase.LOADING:
            return False
        self.state = GameState(phase=GamePhase.LOADING, round_seconds=self.state.round_seconds)
        self.match_targets = []
        self.round_clues = {}
        self._match_id += 1
        self._staged_match = False
        self.clue_error = ''
        self._clue_retry_at = 0.0
        self.recovery_reason = ''
        self._paused_phase = None
        self._held_seconds = 0.0
        self._resume_round = False
        self.accept_detections_after = time.monotonic()
        self._reset_until = self.accept_detections_after + .75
        return True

    def start_game(self):
        if self.state.phase not in IDLE:
            return False
        if not self._staged_match:
            self._stage_match()
        self._staged_match = False
        duration = self.state.round_seconds
        self.state = GameState(phase=GamePhase.PREPARING, round_seconds=duration)
        self._resume_round = False
        self._paused_phase = None
        self.recovery_reason = ''
        self._prepare_round()
        return True

    def _stage_match(self):
        self.match_targets = random.sample(self.objects, k=min(10, len(self.objects)))
        self.round_clues = {}
        self._match_id += 1
        self._staged_match = True
        self.clue_error = ''
        self._clue_retry_at = 0.0

    def _prepare_round(self):
        self.state.clue = ''
        self.state.target_label = ''
        self.state.target_name = ''
        self.state.phase = GamePhase.PREPARING
        self._ensure_clue()
        if self.camera_ready and self.display_ready and self.inference_ready:
            self.recovery_reason = ''
            if self.state.current_round in self.round_clues:
                self.state.phase = GamePhase.COUNTDOWN
                self.state.countdown_deadline = time.monotonic() + 3
        else:
            self.recovery_reason = 'camera' if not self.camera_ready else 'connection' if not self.display_ready else 'recognition'

    def _ensure_clue(self):
        # Drain the whole match in order, independently of round progression.
        if self.clue_thread and self.clue_thread.is_alive():
            return
        if time.monotonic() < self._clue_retry_at:
            return
        index = next((i for i in range(len(self.match_targets))
                      if i not in self.round_clues), None)
        if index is None:
            return
        match_id = self._match_id
        target = dict(self.match_targets[index])

        def generate():
            started = time.monotonic()
            log.info('clue_generation_started model=%s match=%s round=%s target=%s',
                     MODEL, match_id, index + 1, target['label'])
            try:
                clue = generate_clue(self.llm, target)
                with self._lock:
                    if match_id != self._match_id:
                        return
                    self.round_clues[index] = clue
                    self.clue_error = ''
                    self.clue_last_seconds = round(time.monotonic() - started, 3)
                    self.clues_generated += 1
                    log.info('clue_generated model=%s match=%s round=%s target=%s seconds=%s clue=%s',
                             MODEL, match_id, index + 1, target['label'], self.clue_last_seconds, json.dumps(clue))
                    if self.state.phase == GamePhase.PREPARING:
                        self._prepare_round()
                    self._send_state()
            except Exception as exc:
                with self._lock:
                    if match_id != self._match_id:
                        return
                    self.clue_error = str(exc)
                    self._clue_retry_at = time.monotonic() + 5
                    log.warning('clue_generation_failed round=%s target=%s error=%s; retrying in 5s', index + 1, target['label'], exc)
                    self._send_state()

        self.clue_thread = threading.Thread(target=generate, name='local-riddle', daemon=True)
        self.clue_thread.start()

    def tick(self):
        with self._lock:
            now = time.monotonic()
            phase = self.state.phase
            if phase in IDLE or phase == GamePhase.LOADING:
                if not self._staged_match:
                    self._stage_match()
            self._ensure_clue()
            if phase == GamePhase.LOADING and now >= self._reset_until:
                self.state.phase = GamePhase.MENU
                self._send_state()
            elif phase == GamePhase.PREPARING and self.camera_ready and self.display_ready and self.inference_ready:
                self._prepare_round()
                self.recovery_reason = ''
            elif phase == GamePhase.COUNTDOWN and now >= self.state.countdown_deadline:
                self._enter_round(now)
            elif phase == GamePhase.ROUND and now >= self.state.round_deadline:
                self._finish_round('none', 0)
            elif phase == GamePhase.ROUND_RESULT and now >= self.state.result_deadline:
                if self.state.current_round + 1 >= len(self.match_targets):
                    self.state.phase = GamePhase.GAME_OVER
                else:
                    self.state.current_round += 1
                    self._prepare_round()
            if now - self.last_state_sent >= .25:
                self._send_state()

    def _enter_round(self, now):
        self.accept_detections_after = now
        if self._resume_round:
            self.state.round_started_at = now - (self.state.round_seconds - self._held_seconds)
            self.state.round_deadline = now + self._held_seconds
            self._resume_round = False
        else:
            target = self.match_targets[self.state.current_round]
            self.state.clue = self.round_clues[self.state.current_round]
            self.state.target_label = target['label']
            self.state.target_name = target['name']
            self.state.round_started_at = now
            self.state.round_deadline = now + self.state.round_seconds
        self.state.phase = GamePhase.ROUND
        log.info('round_started round=%s target=%s duration_remaining=%.2f', self.state.current_round + 1, self.state.target_label, self.state.round_deadline - now)
        self._send_state()

    def pause(self, reason):
        if self.state.phase == GamePhase.RECOVERY:
            return False
        if self.state.phase not in ACTIVE:
            return False
        self._paused_phase = self.state.phase
        if self._paused_phase == GamePhase.ROUND:
            self._held_seconds = max(0, self.state.round_deadline - time.monotonic())
        elif self._paused_phase == GamePhase.ROUND_RESULT:
            self._held_seconds = max(0, self.state.result_deadline - time.monotonic())
        self.state.phase = GamePhase.RECOVERY
        self.recovery_reason = reason
        return True

    def resume(self):
        if self.state.phase != GamePhase.RECOVERY or not self.camera_ready or not self.display_ready or not self.inference_ready:
            return False
        self.recovery_reason = ''
        if self._paused_phase == GamePhase.ROUND_RESULT:
            self.state.phase = GamePhase.ROUND_RESULT
            self.state.result_deadline = time.monotonic() + self._held_seconds
        elif self._paused_phase == GamePhase.PREPARING:
            self._prepare_round()
        else:
            self._resume_round = self._paused_phase == GamePhase.ROUND or self._resume_round
            self.state.phase = GamePhase.COUNTDOWN
            self.state.countdown_deadline = time.monotonic() + 3
        self._paused_phase = None
        return True

    def handle_detection(self, detections):
        if self.state.phase != GamePhase.ROUND or not isinstance(detections, dict):
            return
        if time.monotonic() >= self.state.round_deadline:
            self._finish_round('none', 0)
            return
        for label, values in detections.items():
            if self._normalize_label(label) != self._normalize_label(self.state.target_label):
                continue
            for value in self._as_detection_list(values):
                if not isinstance(value, dict):
                    continue
                confidence = value.get('confidence', 0)
                if not isinstance(confidence, (float, int)) or not math.isfinite(confidence) or confidence < .5:
                    continue
                player = self._player_from_detection(value)
                if player:
                    self._finish_round(player, self._score_now())
                    return

    def handle_voice_command(self, command):
        command = str(command).strip().lower()
        command = VOICE_COMMANDS.get(command, command)
        if command == 'play' and self.state.phase == GamePhase.RECOVERY:
            return self.resume()
        if command == 'play' and self.state.phase in {GamePhase.MENU, GamePhase.TUTORIAL, GamePhase.GAME_OVER}:
            return self.start_game()
        if command == 'pause':
            return self.pause('paused')
        if command == 'menu':
            return self.handle(GameEvent(GameEventType.BACK_TO_MENU))
        if command in {'30', '60', '120'} and self.state.phase == GamePhase.SETTINGS:
            return self.handle(GameEvent(GameEventType.SET_ROUND_SECONDS, {'seconds': int(command)}))
        if command in {'tutorial', 'settings'} and self.state.phase in IDLE:
            self.state.phase = GamePhase.TUTORIAL if command == 'tutorial' else GamePhase.SETTINGS
            return True
        return False

    def _finish_round(self, winner, points):
        target = self.match_targets[self.state.current_round]
        if winner in self.state.scores:
            self.state.scores[winner] += points
        self.state.recaps.append(RoundRecap(self.state.current_round + 1, target['name'], self.state.clue, winner, points))
        self.state.phase = GamePhase.ROUND_RESULT
        self.state.result_deadline = time.monotonic() + 2.5
        log.info('round_result round=%s target=%s winner=%s points=%s scores=%s', self.state.current_round+1, target['name'], winner, points, self.state.scores)
        self._send_state()

    def _score_now(self):
        elapsed = time.monotonic() - self.state.round_started_at
        if elapsed <= 10:
            return 1000
        decay_window = max(1, self.state.round_seconds - 10)
        ratio = math.log1p(min(decay_window, elapsed - 10)) / math.log1p(decay_window)
        return max(100, round(1000 - 900 * ratio))

    def _load_objects(self):
        with self.objects_path.open(encoding='utf-8') as file:
            return [item for item in json.load(file) if item.get('enabled') and item.get('name') and item.get('label')]

    def _as_detection_list(self, values):
        return values if isinstance(values, list) else [values] if isinstance(values, dict) else []

    def _player_from_detection(self, value):
        box = value.get('bounding_box_xyxy') or value.get('bbox') or value.get('box')
        if box is None and {'x', 'y', 'width', 'height'} <= value.keys():
            try:
                box = [value['x'], value['y'], value['x']+value['width'], value['y']+value['height']]
            except TypeError:
                return None
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            return None
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box):
            return None
        x1, y1, x2, y2 = box
        if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1:
            return None
        # A crop's team is fixed before inference, independently of box position.
        if value.get('team') in ('red', 'blue'):
            return value['team']
        threshold = .5 if max(box) <= 1 else self.camera_width / 2
        return 'red' if (x1+x2)/2 < threshold else 'blue'

    def _normalize_label(self, label):
        return str(label).strip().lower().replace('_', ' ')

    def snapshot(self):
        with self._lock:
            now = time.monotonic()
            remaining = max(0, self.state.round_deadline - now)
            if self.state.phase == GamePhase.RECOVERY or self._resume_round:
                remaining = self._held_seconds if self._paused_phase == GamePhase.ROUND or self._resume_round else remaining
            if self.state.phase in IDLE:
                remaining = 0
            result = asdict(self.state.recaps[-1]) if self.state.recaps and self.state.phase in {GamePhase.ROUND_RESULT, GamePhase.GAME_OVER} else None
            return {
                'phase': self.state.phase.value, 'round_seconds': self.state.round_seconds,
                'current_round': self.state.current_round + 1, 'total_rounds': len(self.match_targets) or min(10, len(self.objects)),
                'scores': dict(self.state.scores), 'clue': self.state.clue,
                'target_name': self.state.target_name if result else '',
                'countdown_remaining': max(0, self.state.countdown_deadline - now),
                'round_remaining': remaining, 'result_remaining': max(0, self.state.result_deadline - now),
                'recaps': [asdict(recap) for recap in self.state.recaps], 'result': result,
                'recovery_reason': self.recovery_reason, 'paused': self.state.phase == GamePhase.RECOVERY,
                'camera_ready': self.camera_ready, 'inference_ready': self.inference_ready, 'voice_status': self.voice_status, 'revision': self.revision,
                'clue_error': bool(self.clue_error),
            }

    def _send_state(self):
        self.revision += 1
        self.ui.send_message('game_state', self.snapshot())
        self.last_state_sent = time.monotonic()
