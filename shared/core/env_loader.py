import inspect
import os
from dotenv import load_dotenv

load_dotenv()


class EnvLoader:
    """Detecta ambiente (DEV local vs PROD Cloud Run) e trigger (MANUAL vs SCHEDULED)."""

    def load_env(self, request=None) -> tuple[str, str]:
        env = "PROD" if os.getenv("K_SERVICE") else "DEV"
        os.environ["ENV"] = env

        trigger = "SCHEDULED" if (request and request.headers.get("X-CloudScheduler") == "true") else "MANUAL"
        os.environ["TRIGGER_TYPE"] = trigger

        if env == "DEV":
            caller_dir = os.path.dirname(inspect.stack()[1].filename)
            os.environ["AUTOMATION_NAME"] = os.path.basename(caller_dir)
        elif not os.getenv("AUTOMATION_NAME"):
            os.environ["AUTOMATION_NAME"] = os.getenv("K_SERVICE", "unknown")

        return env, trigger
