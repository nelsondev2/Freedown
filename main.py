"""
Entry point — bootstraps logging, validates config, and starts the bot.
"""
import sys

from deltabot_cli import BotCli

from config import config
from handlers import register
from utils.logger import setup_logging


def main() -> None:
    setup_logging(config.bot.log_level)

    cli = BotCli("moodlebot")
    register(cli)
    cli.start()


if __name__ == "__main__":
    try:
        main()
    except EnvironmentError as exc:
        print(f"[FATAL] Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nBot stopped.")
        sys.exit(0)
