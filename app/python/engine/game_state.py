from dataclasses import dataclass, field
from enum import Enum


class GamePhase(Enum):
    BOOT = 'boot'
    LOADING = 'loading'
    MENU = 'menu'
    SETTINGS = 'settings'
    TUTORIAL = 'tutorial'
    PREPARING = 'preparing'
    COUNTDOWN = 'countdown'
    ROUND = 'round'
    ROUND_RESULT = 'round_result'
    RECOVERY = 'recovery'
    GAME_OVER = 'game_over'


@dataclass
class RoundRecap:
    round_number: int
    target: str
    clue: str
    winner: str
    points: int


@dataclass
class GameState:
    phase: GamePhase = GamePhase.BOOT
    round_seconds: int = 60
    current_round: int = 0
    scores: dict = field(default_factory=lambda: {'red': 0, 'blue': 0})
    clue: str = ''
    target_label: str = ''
    target_name: str = ''
    round_started_at: float = 0.0
    round_deadline: float = 0.0
    countdown_deadline: float = 0.0
    result_deadline: float = 0.0
    recaps: list[RoundRecap] = field(default_factory=list)
