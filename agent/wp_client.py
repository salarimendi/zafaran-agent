"""Thin WordPress / WooCommerce REST client. Reused by every later stage."""
import requests

from .config import Config


class WPClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.mode = "pretty"  # "pretty" => /wp-json/...  |  "rest_route" => /?rest_route=/...
        self.wc_auth_mode = "basic"  # "basic" or "query"
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "ZafaranAgent/1.0", "Accept": "application/json"})

    def url(self, route: str) -> str:
        route = route.lstrip("/")
        if self.mode == "pretty":
            return f"{self.cfg.site_url}/wp-json/{route}"
        return f"{self.cfg.site_url}/?rest_route=/{route}"

    def request(self, method: str, route: str, auth: str = "wp", **kw) -> requests.Response:
        params = dict(kw.pop("params", {}) or {})
        timeout = kw.pop("timeout", self.cfg.timeout)
        a = None
        if auth == "wp":
            a = (self.cfg.wp_user, self.cfg.wp_app_password)
        elif auth == "wc":
            if self.wc_auth_mode == "basic":
                a = (self.cfg.wc_key, self.cfg.wc_secret)
            else:
                params.update(consumer_key=self.cfg.wc_key, consumer_secret=self.cfg.wc_secret)
        return self.s.request(method, self.url(route), auth=a, params=params,
                              timeout=timeout, **kw)

    def get(self, route: str, auth: str = "wp", **kw) -> requests.Response:
        return self.request("GET", route, auth=auth, **kw)

    def detect_mode(self) -> requests.Response:
        """Find out whether pretty /wp-json/ works; else fall back to ?rest_route=."""
        last = None
        for mode in ("pretty", "rest_route"):
            self.mode = mode
            try:
                r = self.get("", auth=None)
                last = r
                if r.ok and "namespaces" in _json(r):
                    return r
            except requests.RequestException:
                continue
        self.mode = "pretty"
        if last is None:
            raise requests.ConnectionError("Could not connect to the site at all.")
        return last


def _json(r: requests.Response):
    try:
        return r.json()
    except ValueError:
        return {}


def short_body(r: requests.Response, n: int = 160) -> str:
    return " ".join(r.text.split())[:n]
