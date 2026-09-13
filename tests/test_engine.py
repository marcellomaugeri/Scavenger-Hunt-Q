"""Behavioural regressions: authoritative rounds, inputs and reconnect fairness."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/python'))
from engine.game_engine import GameEngine
from engine.events import GameEvent, GameEventType

OBJECTS = Path(__file__).resolve().parents[1] / 'app/python/objects.json'
class UI:
    def send_message(self, kind, state, **kwargs):
        self.last = state

class LLM:
    def clear_memory(self):
        pass

    def chat(self, prompt):
        return 'I keep your daily essentials together when you take them on a journey.'

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.clock = patch('engine.game_engine.time.monotonic', side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.ui = UI()
        self.engine = GameEngine(self.ui, LLM(), OBJECTS, 640)
        self.addCleanup(self.wait_for_clue)
        self.engine.boot()
        self.advance(5)

    def event(self, kind, payload=None):
        return self.engine.handle(GameEvent(kind, payload or {}))

    def advance(self, seconds):
        self.now += seconds
        self.engine.tick()
        self.wait_for_clue()

    def wait_for_clue(self):
        if self.engine.clue_thread:
            self.engine.clue_thread.join(1)

    def start_round(self):
        self.event(GameEventType.START_GAME)
        if self.engine.clue_thread:
            self.engine.clue_thread.join(1)
        self.advance(3)
        self.assertEqual(self.engine.state.phase.value, 'round')

    def detect(self, x=30):
        label = self.engine.state.target_label
        self.event(GameEventType.DETECTION, {label: [{'confidence': .95, 'bounding_box_xyxy': [x, 20, x+60, 90]}]})

    def test_browser_reconnect_does_not_reset_round(self):
        self.start_round()
        self.engine.boot()
        self.assertEqual(self.engine.state.phase.value, 'round')

    def test_startup_loading_waits_five_seconds_after_display_connects(self):
        self.engine = GameEngine(self.ui, LLM(), OBJECTS, 640)
        self.engine.display_ready = False
        self.engine.boot()
        self.assertEqual(self.engine.state.phase.value, 'loading')
        self.advance(30)
        self.assertEqual(self.engine.state.phase.value, 'loading')
        self.event(GameEventType.DISPLAY_STATUS, {'ready': True})
        self.advance(4.9)
        self.assertEqual(self.engine.state.phase.value, 'loading')
        self.assertFalse(self.event(GameEventType.START_GAME))
        self.engine.boot()  # Repeated boot notification cannot bypass the delay.
        self.assertEqual(self.engine.state.phase.value, 'loading')
        self.advance(.1)
        self.assertEqual(self.engine.state.phase.value, 'menu')
        self.event(GameEventType.RESET_GAME)
        self.advance(.8)
        self.assertEqual(self.engine.state.phase.value, 'menu')

    def test_reset_clears_match_through_loading_without_reinitialising_hardware(self):
        from controls import parse_command
        self.event(GameEventType.SET_ROUND_SECONDS, {'seconds': 30})
        self.start_round()
        self.detect()
        self.event(GameEventType.PAUSE_GAME)
        revision = self.engine.revision
        self.assertTrue(self.engine.handle(parse_command('reset', {})))
        state = self.engine.snapshot()
        self.assertEqual(state['phase'], 'loading')
        self.assertEqual(state['scores'], {'red': 0, 'blue': 0})
        self.assertEqual(state['recaps'], [])
        self.assertEqual(state['clue'], '')
        self.assertEqual(state['round_remaining'], 0)
        self.assertFalse(state['paused'])
        self.assertEqual(state['round_seconds'], 30)
        self.assertGreater(state['revision'], revision)
        self.assertTrue(self.engine.camera_ready and self.engine.inference_ready)
        self.assertEqual(self.engine.match_targets, [])
        self.assertEqual(self.engine.round_clues, {})
        self.assertIsNone(self.engine._paused_phase)
        self.assertFalse(self.engine._resume_round)
        self.assertFalse(self.event(GameEventType.START_GAME))
        self.advance(.4)
        self.assertEqual(self.engine.state.phase.value, 'loading')
        self.assertFalse(self.engine.handle(parse_command('reset', {})))
        self.advance(.4)
        self.assertEqual(self.engine.state.phase.value, 'menu')
        self.start_round()

    def test_reset_returns_to_menu_even_when_camera_is_unplugged(self):
        from controls import parse_command
        self.start_round()
        self.event(GameEventType.CAMERA_STATUS, {'ready': False})
        self.engine.handle(parse_command('reset', {}))
        self.advance(1)
        self.assertEqual(self.engine.state.phase.value, 'menu')
        self.assertFalse(self.engine.camera_ready)

    def test_return_to_menu_does_not_report_a_running_timer(self):
        self.start_round()
        self.advance(5)
        self.event(GameEventType.BACK_TO_MENU)
        self.assertEqual(self.engine.snapshot()['round_remaining'], 0)

    def test_invalid_duration_is_rejected(self):
        for seconds in (0, -10, 999, True, 'no'):
            with self.subTest(seconds=seconds):
                try:
                    self.event(GameEventType.SET_ROUND_SECONDS, {'seconds': seconds})
                except (ValueError, TypeError):
                    pass
                self.assertEqual(self.engine.state.round_seconds, 60)

    def test_duration_and_start_cannot_change_active_match(self):
        self.start_round()
        targets = list(self.engine.match_targets)
        self.event(GameEventType.SET_ROUND_SECONDS, {'seconds': 30})
        self.event(GameEventType.START_GAME)
        self.assertEqual(self.engine.state.round_seconds, 60)
        self.assertEqual(self.engine.match_targets, targets)
        self.assertEqual(self.engine.state.phase.value, 'round')

    def test_result_is_visible_and_duplicate_detection_awards_once(self):
        self.start_round()
        self.detect()
        self.assertEqual(self.engine.state.phase.value, 'round_result')
        self.assertEqual(self.engine.state.scores, {'red':1000,'blue':0})
        self.detect()
        self.assertEqual(len(self.engine.state.recaps), 1)
        self.advance(2.5)
        self.assertEqual(self.engine.state.phase.value, 'countdown')

    def test_detection_at_deadline_does_not_beat_timeout(self):
        self.start_round()
        self.now += 60
        self.detect()
        self.assertEqual(self.engine.state.scores['red'], 0)
        self.assertEqual(self.engine.state.recaps[-1].winner, 'none')

    def test_full_match_timeout_has_ten_rows_and_never_round_eleven(self):
        self.start_round()
        for index in range(10):
            self.advance(60)
            self.assertEqual(self.engine.state.phase.value, 'round_result')
            self.advance(2.5)
            if index < 9:
                self.advance(3)
        self.assertEqual(self.engine.state.phase.value, 'game_over')
        self.assertEqual(len(self.engine.state.recaps), 10)
        self.assertEqual(self.ui.last['current_round'], 10)
        self.assertEqual(self.engine.state.scores, {'red':0, 'blue':0})

    def test_box_validation_and_both_coordinate_scales(self):
        check = self.engine._player_from_detection
        self.assertEqual(check({'bbox':[10,0,100,100]}), 'red')
        self.assertEqual(check({'bbox':[340,0,500,100]}), 'blue')
        self.assertEqual(check({'bbox':[.1,.1,.3,.3]}), 'red')
        self.assertEqual(check({'bbox':[.7,.1,.9,.3]}), 'blue')
        for box in ([1], [100,10,30,50], ['x',0,30,50], [float('nan'),0,100,100]):
            with self.subTest(box=box):
                self.assertIsNone(check({'bbox':box}))

    def test_low_confidence_is_not_an_award(self):
        self.start_round()
        self.event(GameEventType.DETECTION,{self.engine.state.target_label:[{'confidence':.4999,'bbox':[10,0,100,100]}]})
        self.assertEqual(self.engine.state.phase.value,'round')

    def test_smaller_target_scores_despite_larger_higher_confidence_distractor(self):
        self.start_round()
        target = self.engine.state.target_label
        self.event(GameEventType.DETECTION, {
            'bed': [{'confidence': .99, 'bbox': [0, 0, 300, 400]}],
            target: [{'confidence': .49, 'bbox': [10, 10, 290, 390]},
                     {'confidence': .50, 'bbox': [40, 40, 70, 90]}],
        })
        self.assertEqual(self.engine.state.phase.value, 'round_result')
        self.assertEqual(self.engine.state.scores, {'red': 1000, 'blue': 0})

    def test_unknown_voice_does_not_start_game(self):
        self.engine.handle_voice_command('background noise')
        self.assertEqual(self.engine.state.phase.value, 'menu')

    def test_pause_holds_time_and_resumes_with_full_countdown(self):
        self.start_round()
        self.advance(12)
        self.event(GameEventType.PAUSE_GAME)
        self.assertEqual(self.engine.snapshot()['round_remaining'], 48)
        self.advance(100)
        self.assertEqual(self.engine.snapshot()['round_remaining'], 48)
        self.event(GameEventType.RESUME_GAME)
        self.assertEqual(self.engine.state.phase.value, 'countdown')
        self.advance(3)
        self.assertEqual(self.engine.snapshot()['round_remaining'], 48)
        self.assertEqual(self.engine.state.phase.value, 'round')
        self.advance(48)
        self.assertEqual(self.engine.state.recaps[-1].winner, 'none')

    def test_camera_loss_does_not_resume_while_display_is_missing(self):
        self.start_round()
        self.advance(7)
        self.event(GameEventType.CAMERA_STATUS, {'ready': False})
        self.event(GameEventType.DISPLAY_STATUS, {'ready': False})
        self.advance(90)
        self.event(GameEventType.CAMERA_STATUS, {'ready': True})
        self.assertEqual(self.engine.state.phase.value, 'recovery')
        self.event(GameEventType.DISPLAY_STATUS, {'ready': True})
        self.advance(3)
        self.assertEqual(self.engine.snapshot()['round_remaining'], 53)

    def test_pause_during_resume_countdown_preserves_remaining_time(self):
        self.start_round()
        self.advance(15)
        self.event(GameEventType.PAUSE_GAME)
        self.event(GameEventType.RESUME_GAME)
        self.advance(1)
        self.event(GameEventType.PAUSE_GAME)
        self.advance(90)
        self.event(GameEventType.RESUME_GAME)
        self.advance(3)
        self.assertEqual(self.engine.snapshot()['round_remaining'], 45)

    def test_manual_pause_stays_paused_on_camera_reconnect(self):
        self.start_round()
        self.event(GameEventType.PAUSE_GAME)
        self.event(GameEventType.CAMERA_STATUS, {'ready':False})
        self.event(GameEventType.CAMERA_STATUS, {'ready':True})
        self.assertEqual(self.engine.state.phase.value, 'recovery')

    def test_waits_for_camera_before_initial_countdown(self):
        self.event(GameEventType.CAMERA_STATUS, {'ready':False})
        self.event(GameEventType.START_GAME)
        self.advance(90)
        self.assertIn(self.engine.state.phase.value, ('preparing','recovery'))
        self.event(GameEventType.CAMERA_STATUS, {'ready':True})
        self.advance(0)
        self.assertEqual(self.engine.state.phase.value, 'countdown')
        self.advance(3)
        self.assertEqual(self.engine.snapshot()['round_remaining'], 60)

    def test_resume_uses_a_new_detection_eligibility_boundary(self):
        self.start_round()
        self.advance(15)
        self.event(GameEventType.PAUSE_GAME)
        self.advance(80)
        self.event(GameEventType.RESUME_GAME)
        self.advance(3)
        self.assertEqual(getattr(self.engine, 'accept_detections_after', None), self.now)
        self.assertEqual(self.engine.state.round_started_at, self.now-15)

    def test_score_baseline(self):
        self.start_round()
        self.now += 10
        self.assertEqual(self.engine._score_now(), 1000)
        self.now += 50
        self.assertEqual(self.engine._score_now(), 100)

if __name__ == '__main__':
    unittest.main()
