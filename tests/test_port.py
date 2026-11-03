"""PANEL_PORT from .env: parsing, validation, fixed port vs default range, busy port."""
import os
import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import panel  # noqa: E402


class PanelPort(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.pop("PANEL_PORT", None)
        self.dot = panel.ROOT
        import dotenv
        self._ld = dotenv.load_dotenv
        dotenv.load_dotenv = lambda *a, **k: None   # never read a real .env here

    def tearDown(self):
        import dotenv
        dotenv.load_dotenv = self._ld
        os.environ.pop("PANEL_PORT", None)
        if self.old is not None:
            os.environ["PANEL_PORT"] = self.old

    def test_default_range_when_unset(self):
        self.assertIsNone(panel.configured_port())
        self.assertEqual(panel._ports(), list(panel.PANEL_PORTS))

    def test_fixed_port(self):
        os.environ["PANEL_PORT"] = " 3456 "
        self.assertEqual(panel.configured_port(), 3456)
        self.assertEqual(panel._ports(), [3456])

    def test_invalid_values_stop_with_message(self):
        for bad in ("abc", "80", "70000", str(panel.LOCK_PORT), "20.5"):
            os.environ["PANEL_PORT"] = bad
            with self.assertRaises(SystemExit) as e:
                panel.configured_port()
            self.assertIn("PANEL_PORT", str(e.exception))

    def test_binds_exactly_that_port_and_reports_busy(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
        s.close()
        os.environ["PANEL_PORT"] = str(free)
        srv, port = panel._open_server(panel._ports())
        self.assertEqual(port, free)
        try:
            srv2, port2 = panel._open_server(panel._ports())      # same port again: busy -> no silent fallback to another port
            self.assertIsNone(srv2)
        finally:
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
