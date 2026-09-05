"""Follow the active Omarchy theme.

Omarchy renders ~/.config/omarchy/themed/omalink.css.tpl to
~/.local/state/omarchy/current/theme/omalink.css on every theme change
(install the template with bin/omalink-setup-theme). This module loads
that file as a GTK CSS provider scoped to this app, forces the matching
light/dark scheme, and watches the file so a theme switch restyles the
running app live.
"""

import os
import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

THEME_CSS = os.path.expanduser("~/.local/state/omarchy/current/theme/omalink.css")

_MODE_RE = re.compile(r"/\*\s*mode:\s*(\w+)\s*\*/")


class OmarchyTheme:
    def __init__(self):
        self.provider = Gtk.CssProvider()
        self._added = False
        self._reload_pending = False
        self.apply()
        self._monitor = Gio.File.new_for_path(THEME_CSS).monitor_file(
            Gio.FileMonitorFlags.NONE, None
        )
        self._monitor.connect("changed", self._on_changed)

    def apply(self):
        if not os.path.isfile(THEME_CSS):
            return
        try:
            with open(THEME_CSS, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            return
        m = _MODE_RE.search(content)
        style = Adw.StyleManager.get_default()
        if m and m.group(1) == "light":
            style.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:
            style.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        self.provider.load_from_string(content)
        if not self._added:
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), self.provider,
                Gtk.STYLE_PROVIDER_PRIORITY_USER,
            )
            self._added = True

    def _on_changed(self, *_args):
        if self._reload_pending:
            return
        self._reload_pending = True
        GLib.timeout_add(300, self._do_reload)

    def _do_reload(self):
        self._reload_pending = False
        self.apply()
        return False
