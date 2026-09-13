"""Shared vocabulary for browser, CLI and speech input."""
from engine.events import GameEvent, GameEventType

COMMANDS = {
    'start': GameEventType.START_GAME,
    'reset': GameEventType.RESET_GAME,
    'menu': GameEventType.BACK_TO_MENU,
    'tutorial': GameEventType.SHOW_TUTORIAL,
    'settings': GameEventType.OPEN_SETTINGS,
    'duration': GameEventType.SET_ROUND_SECONDS,
    'pause': GameEventType.PAUSE_GAME,
    'resume': GameEventType.RESUME_GAME,
    'voice': GameEventType.VOICE_COMMAND,
}


def parse_command(command, payload):
    if not isinstance(command, str) or command not in COMMANDS or not isinstance(payload, dict):
        raise ValueError('Unknown command or malformed arguments')
    if command == 'duration':
        seconds = payload.get('seconds')
        if type(seconds) is not int or seconds not in (30, 60, 120):
            raise ValueError('Duration must be 30, 60 or 120 seconds')
        return GameEvent(COMMANDS[command], {'seconds': seconds})
    if command == 'voice':
        text = payload.get('text')
        if not isinstance(text, str) or len(text) > 80:
            raise ValueError('Voice command expects a short phrase')
        return GameEvent(COMMANDS[command], {'command': text})
    return GameEvent(COMMANDS[command], {})
