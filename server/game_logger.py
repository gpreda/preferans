"""Game logger — two lines per step in logs/game_<id>.log

Format
------
  <cmd1>, <cmd2>, ...   — available commands (comma-separated labels)
  <executed cmd>        — the command the user chose
"""
import os

LOGS_DIR = os.path.join(os.path.dirname(__file__), '..', 'logs')


class GameLogger:
    def __init__(self, game_id: str):
        os.makedirs(LOGS_DIR, exist_ok=True)
        self._path = os.path.join(LOGS_DIR, f'game_{game_id}.log')

    def log_step(self, commands: str, executed: str):
        with open(self._path, 'a', encoding='utf-8') as f:
            f.write(commands + '\n')
            f.write(executed + '\n')
