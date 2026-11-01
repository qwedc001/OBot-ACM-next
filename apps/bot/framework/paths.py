"""Source workspace locations shared by backend integrations."""

from pathlib import Path

# apps/bot/framework/paths.py:
# parents[0] = framework, parents[1] = apps/bot, parents[2] = apps, parents[3] = repo root
BOT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BACKEND_ROOT = BOT_ROOT
