import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/python'))

class ControlTests(unittest.TestCase):
    def test_command_validation_and_injected_voice(self):
        try:
            from controls import parse_command
        except ImportError:
            self.fail('Command parser is not implemented')
        from engine.events import GameEventType
        self.assertEqual(parse_command('start',{}).type, GameEventType.START_GAME)
        self.assertEqual(parse_command('duration',{'seconds':30}).payload, {'seconds':30})
        self.assertEqual(parse_command('voice',{'text':'go'}).type, GameEventType.VOICE_COMMAND)
        for command, payload in [('duration',{'seconds':'30'}),('duration',{'seconds':0}),('shell',{}),('start',[]),('duration',{'seconds':True})]:
            with self.subTest(command=command,payload=payload), self.assertRaises(ValueError):
                parse_command(command,payload)

    def test_voice_accepts_bare_commands_with_sufficient_confidence(self):
        try:
            from voice import command_from_result
        except ImportError:
            self.fail('Temporary speech-model mapping is not implemented')
        self.assertEqual(command_from_result({'text':'go','result':[{'word':'go','conf':.98}]}), 'go')
        self.assertIsNone(command_from_result({'text':'no','result':[{'word':'no','conf':1.0}]}))
        self.assertIsNone(command_from_result({'text':'game go','result':[{'word':'game','conf':.2},{'word':'go','conf':.98}]}))
        self.assertIsNone(command_from_result({'text':'please go away','result':[]}))
        self.assertIsNone(command_from_result({'text':'go','result':[{'word':'go','conf':.2}]}))
        self.assertIsNone(command_from_result({'text':'unknown'}))

    def test_voice_accepts_fifty_percent_but_rejects_uncertain_words(self):
        from voice import command_from_result
        for word, confidence in [('play', .5), ('menu', .563), ('back', .571)]:
            with self.subTest(word=word, confidence=confidence):
                self.assertEqual(command_from_result({
                    'text': word, 'result': [{'word': word, 'conf': confidence}]
                }), word)
        self.assertIsNone(command_from_result({
            'text': 'play', 'result': [{'word': 'play', 'conf': .499}]
        }))
        self.assertIsNone(command_from_result({
            'text': 'play again', 'result': [
                {'word': 'play', 'conf': .99}, {'word': 'again', 'conf': .49}]
        }))

if __name__ == '__main__': unittest.main()
