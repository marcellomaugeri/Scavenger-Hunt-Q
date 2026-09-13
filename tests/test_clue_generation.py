"""A round must use its own generated clue without blocking game controls."""
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/python'))
from engine.game_engine import GameEngine
from engine.events import GameEvent, GameEventType

OBJECTS = Path(__file__).resolve().parents[1] / 'app/python/objects.json'
CLUE = 'I keep your daily essentials together when you take them on a journey.'


class UI:
    def send_message(self, *args, **kwargs):
        pass


class DelayedLLM:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.fail = False

    def clear_memory(self):
        pass

    def chat(self, prompt):
        self.started.set()
        self.release.wait(2)
        if self.fail:
            raise RuntimeError('Local model unavailable')
        return CLUE


class GeneratedClueTests(unittest.TestCase):
    def setUp(self):
        self.llm = DelayedLLM()
        self.now = 100.0
        self.clock = patch('engine.game_engine.time.monotonic', side_effect=lambda: self.now)
        self.clock.start()
        self.engine = GameEngine(UI(), self.llm, OBJECTS, 1440)
        self.engine.boot()
        self.now += 5
        self.engine.tick()

    def tearDown(self):
        self.llm.release.set()
        if self.engine.clue_thread:
            self.engine.clue_thread.join(2)
        self.clock.stop()

    def event(self, kind):
        return self.engine.handle(GameEvent(kind))

    def start_generation(self):
        self.event(GameEventType.START_GAME)
        self.assertTrue(self.llm.started.wait(.5), 'The game never requested a generated clue')

    def finish_generation(self):
        self.llm.release.set()
        self.engine.clue_thread.join(2)

    def test_slow_model_does_not_start_timer_or_block_menu(self):
        self.event(GameEventType.START_GAME)
        self.now += 20
        self.engine.tick()
        self.assertEqual(self.engine.state.phase.value, 'preparing')
        self.assertEqual(self.engine.state.round_deadline, 0)
        self.assertTrue(self.event(GameEventType.BACK_TO_MENU))

    def test_menu_prepares_first_riddle_without_starting_match(self):
        self.assertTrue(self.llm.started.wait(.5))
        self.assertEqual(self.engine.state.phase.value, 'menu')
        self.finish_generation()
        self.event(GameEventType.START_GAME)
        self.assertEqual(self.engine.state.phase.value, 'countdown')
        self.now += 3
        self.engine.tick()
        self.assertEqual(self.engine.state.clue, CLUE)

    def test_menu_generates_all_ten_without_waiting_for_round_changes(self):
        self.finish_generation()
        for count in range(2, 11):
            self.engine.tick()
            self.engine.clue_thread.join(2)
            self.assertEqual(len(self.engine.round_clues), count)
            self.assertEqual(self.engine.state.phase.value, 'menu')
        self.assertEqual(self.engine.clues_generated, 10)
        self.engine.tick()
        self.assertEqual(self.engine.clues_generated, 10)

    def test_generation_continues_through_all_rounds_while_paused(self):
        self.start_generation()
        self.event(GameEventType.PAUSE_GAME)
        self.finish_generation()
        for count in range(2, 11):
            self.engine.tick()
            self.engine.clue_thread.join(2)
            self.assertEqual(len(self.engine.round_clues), count)
        self.assertEqual(self.engine.state.phase.value, 'recovery')
        self.assertEqual(self.engine.state.round_deadline, 0)

    def test_first_round_can_precompute_every_remaining_riddle(self):
        self.start_generation()
        self.finish_generation()
        self.now += 3
        self.engine.tick()
        self.engine.clue_thread.join(2)
        deadline = self.engine.state.round_deadline
        for count in range(3, 11):
            self.engine.tick()
            self.engine.clue_thread.join(2)
            self.assertEqual(len(self.engine.round_clues), count)
        self.assertEqual(self.engine.state.phase.value, 'round')
        self.assertEqual(self.engine.state.round_deadline, deadline)

    def test_next_riddle_is_requested_during_current_round(self):
        self.start_generation()
        self.finish_generation()
        self.llm.release.clear()
        self.llm.started.clear()
        self.now += 3
        self.engine.tick()
        self.assertTrue(self.llm.started.wait(.5))
        self.assertEqual(self.engine.state.phase.value, 'round')
        self.finish_generation()
        self.now += 60
        self.engine.tick()
        self.now += 2.5
        self.engine.tick()
        self.assertEqual(self.engine.state.phase.value, 'countdown')
        self.assertEqual(self.engine.state.current_round, 1)

    def test_round_shows_generated_text_instead_of_stored_clue(self):
        self.start_generation()
        self.finish_generation()
        self.now += 3
        self.engine.tick()
        self.assertEqual(self.engine.state.phase.value, 'round')
        self.assertEqual(self.engine.state.clue, CLUE)
        self.assertEqual(self.engine.snapshot()['round_remaining'], 60)

    def test_failed_model_keeps_round_waiting_without_static_fallback(self):
        self.llm.fail = True
        self.start_generation()
        self.finish_generation()
        self.engine.tick()
        self.assertEqual(self.engine.state.phase.value, 'preparing')
        self.assertNotIn(0, self.engine.round_clues)
        self.assertTrue(self.engine.snapshot()['clue_error'])

    def test_reset_discards_inflight_clue(self):
        self.start_generation()
        self.event(GameEventType.RESET_GAME)
        self.finish_generation()
        self.assertEqual(self.engine.round_clues, {})
        self.assertEqual(self.engine.state.phase.value, 'loading')

    def test_pause_resume_during_generation_still_waits_for_clue(self):
        self.start_generation()
        self.event(GameEventType.PAUSE_GAME)
        self.event(GameEventType.RESUME_GAME)
        self.assertEqual(self.engine.state.phase.value, 'preparing')
        self.finish_generation()
        self.now += 3
        self.engine.tick()
        self.assertEqual(self.engine.state.clue, CLUE)


if __name__ == '__main__':
    unittest.main()
