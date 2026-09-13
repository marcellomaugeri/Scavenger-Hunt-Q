import json
import sys
import tempfile
import types
import unittest
from array import array
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/python'))
import voice


class VoiceRecoveryTests(unittest.TestCase):
    def run_microphones(self, audio):
        """Run the real adapter against PCM/None hardware reads and a fake clock."""
        events, commands, statuses, recognised = [], [], [], []
        clock = types.SimpleNamespace(now=100.0)
        clock.monotonic = lambda: clock.now

        def advance(seconds):
            clock.now += seconds
            if clock.now >= 120:
                control.stop()

        clock.sleep = advance

        class Microphone:
            USB_MIC_1 = 'usb-microphone'
            count = 0

            def __init__(self, device, *, sample_rate, channels, buffer_size, auto_reconnect):
                self.session = Microphone.count
                Microphone.count += 1
                self.reads = 0
                self_test.assertEqual((device, sample_rate, channels, buffer_size, auto_reconnect),
                                      ('usb-microphone', 16000, 1, 2000, False))

            def __enter__(self):
                events.append(('opened', self.session, clock.now))
                return self

            def __exit__(self, *exc):
                events.append(('closed', self.session, clock.now))

            def capture(self):
                advance(.25)
                self.reads += 1
                return audio(self.session, self.reads)

        class Recognizer:
            def __init__(self, model, sample_rate, grammar):
                self_test.assertEqual(sample_rate, 16000)
                self_test.assertIn('play', json.loads(grammar))

            def SetWords(self, enabled):
                pass

            def AcceptWaveform(self, pcm):
                recognised.append(pcm)
                return pcm == array('h', [123]).tobytes()

            def Result(self):
                return json.dumps({'text': 'play', 'result': [
                    {'word': 'play', 'conf': .99}]})

            def Reset(self):
                events.append(('reset', None, clock.now))

        def command_received(command):
            commands.append(command)
            control.stop()

        self_test = self
        modules = {
            'vosk': types.SimpleNamespace(Model=lambda path: object(), KaldiRecognizer=Recognizer,
                                          SetLogLevel=lambda level: None),
            'arduino.app_peripherals.microphone': types.SimpleNamespace(Microphone=Microphone),
        }
        with tempfile.TemporaryDirectory() as model_path:
            control = voice.VoiceControl(model_path, command_received, statuses.append)
            control.running.set()
            with patch.dict(sys.modules, modules), patch.object(voice, 'time', clock):
                control._run()
        return events, commands, statuses, recognised

    def test_missing_chunks_close_stale_microphone_and_reacquire_for_commands(self):
        def audio(session, read):
            if session == 0:
                return array('h', [0] * 2000) if read == 1 else None
            return array('h', [123])

        events, commands, statuses, _ = self.run_microphones(audio)
        self.assertEqual(commands, ['play'])
        opened = [event for event in events if event[0] == 'opened']
        self.assertEqual(len(opened), 2)
        closed = next(event for event in events if event[:2] == ('closed', 0))
        self.assertLess(closed[2] - opened[0][2], 4)
        self.assertLess(events.index(closed), events.index(opened[1]))
        self.assertTrue(any(event[0] == 'reset' for event in events))
        self.assertIn('Microphone unavailable', statuses[1])
        self.assertIn('"play"', statuses[2])

    def test_intermittent_silent_pcm_keeps_microphone_open(self):
        def audio(session, read):
            return array('h', [0] * 2000) if read % 8 == 1 else None

        events, commands, statuses, recognised = self.run_microphones(audio)
        self.assertEqual(commands, [])
        self.assertEqual(len([event for event in events if event[0] == 'opened']), 1)
        self.assertFalse(any(event[0] == 'reset' for event in events))
        self.assertFalse(any('unavailable' in status for status in statuses))
        self.assertGreater(len(recognised), 2)
        self.assertTrue(all(pcm == bytes(4000) for pcm in recognised))


if __name__ == '__main__':
    unittest.main()
