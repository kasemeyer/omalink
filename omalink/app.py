import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib

from .contacts import ContactBook
from .kdeconnect import KdeConnect
from .window import OmalinkWindow, load_css

APP_ID = "dev.kc.Omalink"


class OmalinkApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        win = self.get_active_window()
        if not win:
            load_css()
            kdec = KdeConnect()
            contacts = ContactBook(kdec.device_id)
            win = OmalinkWindow(app, kdec, contacts)
        win.present()


def main():
    GLib.set_application_name("Omalink")
    return OmalinkApp().run(sys.argv)
