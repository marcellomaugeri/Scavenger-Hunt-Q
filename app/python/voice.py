"""Offline Vosk adapter for the game's configured control phrases.

No recordings are retained. Only configured control words and decision reasons are logged.
"""
import json
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger('scavenger.voice')
# Visible button text is accepted verbatim, with short words retained as aliases.
VOICE_COMMANDS = {
    'go': 'play', 'play': 'play', 'play again': 'play', 'start': 'play',
    'back': 'menu', 'menu': 'menu', 'back to menu': 'menu',
    'stop': 'pause', 'pause': 'pause',
    'tutorial': 'tutorial', 'settings': 'settings',
    'thirty': '30', 'thirty seconds': '30',
    'sixty': '60', 'sixty seconds': '60',
    'one twenty': '120', 'one hundred twenty': '120', 'one hundred twenty seconds': '120',
    'one hundred and twenty': '120', 'one hundred and twenty seconds': '120', 'two minutes': '120',
}
WORDS = tuple(VOICE_COMMANDS)


def command_from_result(result):
    text = result.get('text', '').strip().lower()
    words = result.get('result', [])
    if text not in VOICE_COMMANDS:
        if text:
            log.info('voice_rejected reason=unrecognised_phrase')
        return None
    if [word.get('word') for word in words] != text.split():
        log.info('voice_rejected command=%s reason=incomplete_word_result', text)
        return None
    confidence = min(word.get('conf', 0) for word in words)
    if confidence < .5:
        log.info('voice_rejected command=%s confidence=%.3f reason=low_confidence', text, confidence)
        return None
    return text


class VoiceControl:
    def __init__(self, model_path, on_command, on_status):
        self.model_path = Path(model_path)
        self.on_command = on_command
        self.on_status = on_status
        self.running = threading.Event()
        self.thread = None

    def start(self):
        self.running.set()
        self.thread = threading.Thread(target=self._run, name='voice-control', daemon=True)
        self.thread.start()

    def stop(self):
        self.running.clear()

    def _run(self):
        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel
            from arduino.app_peripherals.microphone import Microphone
            if not self.model_path.is_dir():
                raise FileNotFoundError('Offline speech model has not been installed')
            SetLogLevel(-1)
            model = Model(str(self.model_path))
            recognizer = KaldiRecognizer(model, 16000, json.dumps([*WORDS, '[unk]']))
            recognizer.SetWords(True)
            last_command = 0.0
            last_action = None
            while self.running.is_set():
                try:
                    with Microphone(Microphone.USB_MIC_1, sample_rate=16000, channels=1, buffer_size=2000, auto_reconnect=False) as mic:
                        self.on_status('Voice ready: "play", "pause", "menu"')
                        last_audio = time.monotonic()
                        while self.running.is_set():
                            chunk = mic.capture()
                            if chunk is None:
                                if time.monotonic() - last_audio >= 3:
                                    raise TimeoutError('Microphone produced no audio chunks for three seconds')
                                time.sleep(.02)
                                continue
                            last_audio = time.monotonic()
                            if recognizer.AcceptWaveform(chunk.tobytes()):
                                command = command_from_result(json.loads(recognizer.Result()))
                                now = time.monotonic()
                                if command:
                                    action = VOICE_COMMANDS[command]
                                    if action == last_action and now - last_command <= 2:
                                        log.info('voice_rejected command=%s reason=duplicate_cooldown', command)
                                        continue
                                    log.info('voice_command=%s model=vosk-small-en-us-0.15', command)
                                    self.on_command(command)
                                    last_command = now
                                    last_action = action
                except Exception as exc:
                    self.on_status('Microphone unavailable · use buttons or CLI')
                    log.warning('Microphone unavailable; retrying: %s', exc)
                    recognizer.Reset()
                    time.sleep(3)
        except Exception:
            log.exception('Voice unavailable; browser and CLI controls remain active')
            self.on_status('Voice unavailable · use buttons or CLI')
