import logging
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib

from .contacts import ContactBook
from .kdeconnect import KdeConnect
from .theming import OmarchyTheme
from .window import OmalinkWindow, load_css

APP_ID = "dev.kc.Omalink"

LOG_PATH = os.path.expanduser("~/.local/state/omalink/omalink.log")


def _setup_logging():
    """Persist tracebacks so a crash is diagnosable next time. Logs both
    Python exceptions and GLib/GTK warnings to a rotating-ish file."""
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    # Truncate if it grows past ~1 MB so it never balloons.
    try:
        if os.path.getsize(LOG_PATH) > 1_000_000:
            open(LOG_PATH, "w").close()
    except OSError:
        pass
    logging.basicConfig(
        filename=LOG_PATH, level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )
    log = logging.getLogger("omalink")

    def excepthook(exc_type, exc, tb):
        log.error("uncaught exception", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = excepthook
    log.info("omalink starting (pid %s)", os.getpid())
    return log


class OmalinkApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        win = self.get_active_window()
        if not win:
            load_css()
            self.theme = OmarchyTheme()
            kdec = KdeConnect()
            contacts = ContactBook(kdec.device_id)
            win = OmalinkWindow(app, kdec, contacts)
        win.present()


def main():
    log = _setup_logging()
    GLib.set_application_name("Omalink")
    try:
        return OmalinkApp().run(sys.argv)
    except Exception:
        log.exception("fatal error in main loop")
        raise
