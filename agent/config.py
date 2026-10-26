import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    site_url: str
    wp_user: str
    wp_app_password: str
    wc_key: str
    wc_secret: str
    timeout: int


def load_config() -> Config:
    load_dotenv(ROOT / ".env")
    site = os.getenv("WP_SITE_URL", "").strip().rstrip("/")
    user = os.getenv("WP_USER", "").strip()
    pw = os.getenv("WP_APP_PASSWORD", "").strip()
    missing = [n for n, v in (("WP_SITE_URL", site), ("WP_USER", user), ("WP_APP_PASSWORD", pw)) if not v]
    if missing:
        raise SystemExit("Missing in .env: " + ", ".join(missing))
    if "xxxx" in pw:
        raise SystemExit("WP_APP_PASSWORD in .env is still the placeholder.")
    return Config(
        site_url=site,
        wp_user=user,
        wp_app_password=pw,
        wc_key=os.getenv("WC_CONSUMER_KEY", "").strip(),
        wc_secret=os.getenv("WC_CONSUMER_SECRET", "").strip(),
        timeout=int(os.getenv("REQUEST_TIMEOUT", "30")),
    )
