from bot.events import freeze_signals
from bot.util.telegram_log_handler import install_admin_alert_handler

# the worker process (dramatiq CLI) never runs main.py's startup, so signals
# must be frozen here too, once bot.events has registered all handlers
freeze_signals()

# same reason: main.py's setup() never runs in this process
install_admin_alert_handler()
