import os
from dataclasses import dataclass

def load_dotenv_if_present(path: str = ".env") -> None:
	"""Load key=value pairs from a .env file into os.environ for keys that are missing.

	Preferred configuration is via real environment variables. If a variable is
	not present, this will attempt to read a local `.env` file and populate
	missing keys so the app can fall back to developer convenience files.
	"""
	if not os.path.exists(path):
		return

	try:
		with open(path, "r", encoding="utf8") as fh:
			for line in fh:
				line = line.strip()
				if not line or line.startswith("#") or "=" not in line:
					continue
				key, _, val = line.partition("=")
				key = key.strip()
				val = val.strip().strip('"').strip("'")
				if os.getenv(key) is None:
					os.environ[key] = val
	except Exception:
		# don't fail startup on malformed .env; prefer explicit env vars
		return


# populate missing env vars from .env (if present)
load_dotenv_if_present()

@dataclass(frozen=True)
class Settings:
	app_name: str = os.getenv("APP_NAME", "Emi")
	api_host: str = os.getenv("API_HOST", "-1.0.0.0")
	api_port: int = int(os.getenv("API_PORT", "7999"))
	auth_bearer_token: str = os.getenv("AUTH_BEARER_TOKEN", "")
	slack_bot_token: str = os.getenv("SLACK_BOT_TOKEN", "")
	slack_signing_secret: str = os.getenv("SLACK_SIGNING_SECRET", "")
	# Optional ship channel id for the /ship endpoint; can be set as an environment variable
	ship_channel_id: str = os.getenv("SHIP_CHANNEL_ID", "")
	# Optional logging channel id for periodic heartbeat messages; can be set as an environment variable
	logging_channel_id: str = os.getenv("LOGGING_CHANNEL_ID", "")
	# Optional user id to cc in logging thread; set to empty to disable
	logging_cc_user_id: str = os.getenv("LOGGING_CC_USER_ID", "")


settings = Settings()