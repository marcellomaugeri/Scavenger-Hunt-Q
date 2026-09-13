from dataclasses import dataclass
from enum import Enum


class GameEventType(Enum):
    START_GAME = 'start_game'
    RESET_GAME = 'reset_game'
    SHOW_TUTORIAL = 'show_tutorial'
    OPEN_SETTINGS = 'open_settings'
    SET_ROUND_SECONDS = 'set_round_seconds'
    BACK_TO_MENU = 'back_to_menu'
    DETECTION = 'detection'
    VOICE_COMMAND = 'voice_command'
    PAUSE_GAME = 'pause_game'
    RESUME_GAME = 'resume_game'
    CAMERA_STATUS = 'camera_status'
    DISPLAY_STATUS = 'display_status'
    INFERENCE_STATUS = 'inference_status'


@dataclass(frozen=True)
class GameEvent:
    type: GameEventType
    payload: dict | None = None
