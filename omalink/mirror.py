"""Guided scrcpy screen mirroring.

scrcpy mirrors over ADB, which KDE Connect doesn't provide, so the first
run usually needs wireless debugging set up. This module checks each
prerequisite and walks the user through the missing one:

  scrcpy/adb installed -> `adb devices` shows a device -> launch scrcpy
                       -> unauthorized: accept the prompt on the phone
                       -> none: pairing dialog (adb pair, adb connect)

The phone's IP is prefilled from KDE Connect's reachableAddresses.
"""

import shutil
import subprocess

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk


def _run_async(argv, callback):
    """Run argv, then callback(ok, combined_output) on the main loop."""
    try:
        proc = Gio.Subprocess.new(
            argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE
        )
    except GLib.Error as e:
        callback(False, e.message)
        return

    def done(p, result):
        try:
            _, out, _ = p.communicate_utf8_finish(result)
        except GLib.Error as e:
            callback(False, e.message)
            return
        callback(p.get_successful(), out or "")

    proc.communicate_utf8_async(None, None, done)


def _launch_scrcpy(toasts):
    subprocess.Popen(["scrcpy"], start_new_session=True)
    toasts.add_toast(Adw.Toast(title="Starting scrcpy…", timeout=3))


def _discover_connect_port(ip, callback):
    """Find the phone's current wireless-debugging port via mDNS.

    The port changes every time wireless debugging toggles (and often
    right after pairing), so never trust a remembered one. Arch's adb is
    built without mdns support; avahi-browse covers it.

    callback(port or None).
    """
    if not shutil.which("avahi-browse"):
        callback(None)
        return

    def done(ok, out):
        for line in out.splitlines():
            f = line.split(";")
            if len(f) > 8 and f[0] == "=" and f[7] == ip:
                callback(f[8])
                return
        callback(None)

    _run_async(["avahi-browse", "-rpt", "_adb-tls-connect._tcp"], done)


def _try_autoconnect(ip, toasts, on_fail):
    """Discover the current port and adb connect; scrcpy on success."""
    def with_port(port):
        if not port:
            on_fail()
            return

        def connected(ok, out):
            if "connected" in out and "cannot" not in out and "failed" not in out:
                _launch_scrcpy(toasts)
            else:
                on_fail()

        _run_async(["adb", "connect", f"{ip}:{port}"], connected)

    _discover_connect_port(ip, with_port)


def start(window, kdec, toasts):
    """Entry point for the mirror button."""
    missing = [t for t in ("scrcpy", "adb") if not shutil.which(t)]
    if missing:
        dialog = Adw.AlertDialog(
            heading="Install scrcpy",
            body=("Screen mirroring uses scrcpy, which is not installed.\n"
                  "Run this, then try again:"),
        )
        cmd = Gtk.Label(label="sudo pacman -S scrcpy android-tools",
                        selectable=True, margin_top=6)
        cmd.add_css_class("monospace")
        dialog.set_extra_child(cmd)
        dialog.add_response("ok", "Close")
        dialog.present(window)
        return

    def devices_done(ok, out):
        lines = [l for l in out.splitlines()[1:] if l.strip()]
        if any("\tdevice" in l for l in lines):
            _launch_scrcpy(toasts)
        elif any("unauthorized" in l for l in lines):
            d = Adw.AlertDialog(
                heading="Authorize this computer",
                body=("Your phone is reachable but hasn't authorized ADB.\n"
                      "Accept the debugging prompt on the phone, then try again."),
            )
            d.add_response("ok", "OK")
            d.present(window)
        else:
            # Paired earlier? The port has probably just moved — find the
            # current one over mDNS before bothering the user.
            ip = kdec.reachable_address
            if ip:
                _try_autoconnect(
                    ip, toasts,
                    on_fail=lambda: _PairDialog(window, kdec, toasts).present(window))
            else:
                _PairDialog(window, kdec, toasts).present(window)

    _run_async(["adb", "devices"], devices_done)


class _PairDialog(Adw.Dialog):
    def __init__(self, window, kdec, toasts):
        super().__init__(title="Set up wireless debugging", content_width=460)
        self.toasts = toasts

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       margin_top=6, margin_bottom=16, margin_start=16, margin_end=16)
        steps = Gtk.Label(xalign=0, wrap=True, label=(
            "On the phone: Settings → System → Developer options → "
            "Wireless debugging → turn it on.\n\n"
            "1. Tap \"Pair device with pairing code\" and copy the pairing "
            "port and 6-digit code below.\n"
            "2. Pair, then enter the port from the main Wireless debugging "
            "screen (\"IP address & port\") and connect."))
        steps.add_css_class("dim-label")
        page.append(steps)

        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        self.ip = Gtk.Entry(text=kdec.reachable_address, placeholder_text="Phone IP")
        self.pair_port = Gtk.Entry(placeholder_text="Pairing port", width_chars=8)
        self.pair_code = Gtk.Entry(placeholder_text="6-digit code", width_chars=10)
        self.conn_port = Gtk.Entry(placeholder_text="Connect port", width_chars=8)
        grid.attach(Gtk.Label(label="IP", xalign=0), 0, 0, 1, 1)
        grid.attach(self.ip, 1, 0, 2, 1)
        grid.attach(Gtk.Label(label="Pair", xalign=0), 0, 1, 1, 1)
        grid.attach(self.pair_port, 1, 1, 1, 1)
        grid.attach(self.pair_code, 2, 1, 1, 1)
        grid.attach(Gtk.Label(label="Connect", xalign=0), 0, 2, 1, 1)
        grid.attach(self.conn_port, 1, 2, 1, 1)
        page.append(grid)

        btns = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        pair_btn = Gtk.Button(label="Pair")
        pair_btn.connect("clicked", self._on_pair)
        btns.append(pair_btn)
        connect_btn = Gtk.Button(label="Connect and mirror")
        connect_btn.add_css_class("suggested-action")
        connect_btn.connect("clicked", self._on_connect)
        btns.append(connect_btn)
        page.append(btns)

        self.status = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.status.add_css_class("caption")
        page.append(self.status)

        view = Adw.ToolbarView(content=page)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)

    def _say(self, text):
        self.status.set_label(text.strip())

    def _on_pair(self, _btn):
        ip = self.ip.get_text().strip()
        port = self.pair_port.get_text().strip()
        code = self.pair_code.get_text().strip()
        if not (ip and port and code):
            self._say("Enter the IP, pairing port, and code first.")
            return
        self._say("Pairing…")

        def paired(ok, out):
            self._say(out)
            if "Successfully paired" in out:
                self._say(out + "\nConnecting automatically…")

                def failed():
                    self._say("Paired. Auto-connect didn't find the port — enter "
                              "the one from the Wireless debugging screen and "
                              "press Connect.")

                def with_port(p):
                    if not p:
                        failed()
                        return
                    self.conn_port.set_text(p)
                    self._do_connect(ip, p, failed)

                _discover_connect_port(ip, with_port)

        _run_async(["adb", "pair", f"{ip}:{port}", code], paired)

    def _on_connect(self, _btn):
        ip = self.ip.get_text().strip()
        port = self.conn_port.get_text().strip()
        if not (ip and port):
            self._say("Enter the IP and connect port first.")
            return
        self._say("Connecting…")
        self._do_connect(ip, port, lambda: None)

    def _do_connect(self, ip, port, on_fail):
        def done(ok, out):
            self._say(out)
            if "connected" in out and "cannot" not in out and "failed" not in out:
                self.close()
                _launch_scrcpy(self.toasts)
            else:
                on_fail()

        _run_async(["adb", "connect", f"{ip}:{port}"], done)
