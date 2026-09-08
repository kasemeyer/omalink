"""Main window — layout modeled on Microsoft Phone Link.

Left sidebar: device name, connection state, battery, phone notifications.
Main pane: Messages / Calls view switcher; Messages is a conversation
list plus a thread view with composer.

Adaptive for tiling WMs: the notifications sidebar collapses into an
overlay below 1100px, and the conversation list / thread split collapses
into back-button navigation below 760px.
"""

import json
import hashlib
import mimetypes
import os
import shutil
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango

from . import demo, mirror
from .kdeconnect import MESSAGE_SENT


_HIDDEN_FILE = os.path.expanduser("~/.config/omalink/hidden_threads.json")


def _load_hidden():
    try:
        with open(_HIDDEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def _fmt_time(epoch_ms):
    t = time.localtime(epoch_ms / 1000)
    now = time.localtime()
    if (t.tm_year, t.tm_yday) == (now.tm_year, now.tm_yday):
        return time.strftime("%H:%M", t)
    if t.tm_year == now.tm_year:
        return time.strftime("%b %d", t)
    return time.strftime("%Y-%m-%d", t)


class OmalinkWindow(Adw.ApplicationWindow):
    def __init__(self, app, kdec, contacts):
        super().__init__(application=app, title="Omalink")
        self.set_default_size(1180, 740)
        self.set_size_request(360, 420)
        self.kdec = kdec
        self.contacts = contacts
        self.current_thread = None
        self.call_log = []
        self._requested_threads = set()
        self._pending_downloads = {}
        self._hidden = _load_hidden()
        self._show_hidden = False
        self._multi_mode = False
        self._sel_anchor = None
        self._resynced_on_reach = False
        self._outgoing_attachments = []
        self._search_query = ""
        self._suppress_select = False
        self._conv_refresh_pending = False
        self._thread_render_pending = False
        self._notif_refresh_pending = False

        kdec.connect("device-state", lambda *a: self._refresh_device())
        kdec.connect("conversations-loaded", lambda *a: self._refresh_conversations())
        kdec.connect("message", self._on_message)
        kdec.connect("attachment-received", self._on_attachment_received)
        kdec.connect("contacts-synced", self._on_contacts_synced)
        kdec.connect("notifications-changed", lambda *a: self._queue_notif_refresh())
        kdec.connect("call-event", self._on_call_event)
        kdec.connect("media-changed", lambda *a: self._refresh_media())
        kdec.connect("sftp-mounted", self._on_sftp_mounted)

        self.split = Adw.OverlaySplitView(
            sidebar=self._build_sidebar(),
            content=self._build_main(),
            min_sidebar_width=280,
            max_sidebar_width=320,
        )
        self.toasts = Adw.ToastOverlay(child=self.split)
        self.set_content(self.toasts)

        bp_mid = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 1100sp"))
        bp_mid.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp_mid)
        bp_narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760sp"))
        bp_narrow.add_setter(self.split, "collapsed", True)
        bp_narrow.add_setter(self.msg_split, "collapsed", True)
        self.add_breakpoint(bp_narrow)

        self._refresh_device()
        self._refresh_notifications()
        self._refresh_media()
        if kdec.device_id:
            GLib.idle_add(lambda: kdec.load_conversations() or False)
            # The daemon cache never prunes archived/deleted threads, so
            # quietly re-sync against the phone's live list after launch.
            GLib.timeout_add(2500, lambda: kdec.refresh_conversations() or False)

    def _attachment_cache_path(self, part_name):
        name = self.kdec.device_name or ""
        return os.path.expanduser(f"~/.cache/kdeconnect.daemon/{name}/{part_name}")

    # -- sidebar ----------------------------------------------------------

    def _build_sidebar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("sidebar-pane")

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                       margin_top=18, margin_bottom=12, margin_start=18, margin_end=18)
        title_row = Gtk.Box(spacing=8)
        title_row.append(Gtk.Image.new_from_icon_name("phone-symbolic"))
        self.device_label = Gtk.Label(label="No device", xalign=0,
                                      ellipsize=Pango.EllipsizeMode.END)
        self.device_label.add_css_class("title-2")
        title_row.append(self.device_label)
        head.append(title_row)

        status_row = Gtk.Box(spacing=10)
        self.status_label = Gtk.Label(label="Disconnected", xalign=0)
        self.status_label.add_css_class("dim-label")
        status_row.append(self.status_label)
        self.battery_label = Gtk.Label(label="", xalign=0)
        self.battery_label.add_css_class("dim-label")
        status_row.append(self.battery_label)
        ring = Gtk.Button(icon_name="preferences-desktop-notification-bell-symbolic",
                          tooltip_text="Ring phone")
        ring.add_css_class("flat")
        ring.connect("clicked", lambda *a: self.kdec.ring_phone())
        status_row.append(ring)
        head.append(status_row)
        box.append(head)
        box.append(Gtk.Separator())

        self.media_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                                  margin_top=10, margin_bottom=4,
                                  margin_start=12, margin_end=12, visible=False)
        self.media_card.add_css_class("card")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                        margin_top=10, margin_bottom=8, margin_start=12, margin_end=12)
        self.media_player_label = Gtk.Label(xalign=0)
        self.media_player_label.add_css_class("caption")
        self.media_player_label.add_css_class("dim-label")
        inner.append(self.media_player_label)
        self.media_title_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.media_title_label.add_css_class("heading")
        inner.append(self.media_title_label)
        self.media_artist_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.media_artist_label.add_css_class("dim-label")
        inner.append(self.media_artist_label)
        controls = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER, margin_top=4)
        for icon, action in (("media-skip-backward-symbolic", "Previous"),
                             ("media-playback-start-symbolic", "PlayPause"),
                             ("media-skip-forward-symbolic", "Next")):
            b = Gtk.Button(icon_name=icon)
            b.add_css_class("flat")
            b.add_css_class("circular")
            b.connect("clicked", lambda _b, a=action: self.kdec.media_action(a))
            if action == "PlayPause":
                self.media_playpause_btn = b
            controls.append(b)
        inner.append(controls)
        self.media_card.append(inner)
        box.append(self.media_card)

        notif_head = Gtk.Box(margin_top=12, margin_start=18, margin_end=12)
        lbl = Gtk.Label(label="Notifications", xalign=0, hexpand=True)
        lbl.add_css_class("heading")
        notif_head.append(lbl)
        clear = Gtk.Button(label="Clear all")
        clear.add_css_class("flat")
        clear.connect("clicked", self._on_clear_notifications)
        notif_head.append(clear)
        box.append(notif_head)

        self.notif_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.notif_list.add_css_class("boxed-list-separate")
        self.notif_list.set_margin_top(8)
        self.notif_list.set_margin_start(12)
        self.notif_list.set_margin_end(12)
        sc = Gtk.ScrolledWindow(vexpand=True, child=self.notif_list)
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box.append(sc)
        return box

    def _refresh_device(self):
        name = "Pixel 9 Pro" if demo.ENABLED else self.kdec.device_name
        self.device_label.set_label(name or "No device")
        if self.kdec.is_reachable:
            self.status_label.set_label("● Connected")
            self.status_label.remove_css_class("dim-label")
            self.status_label.add_css_class("success")
        else:
            self.status_label.set_label("○ Disconnected")
            self.status_label.add_css_class("dim-label")
        charge = self.kdec.battery_charge
        if charge >= 0:
            bolt = "⚡" if self.kdec.battery_charging else ""
            self.battery_label.set_label(f"🔋 {charge}%{bolt}")
        else:
            self.battery_label.set_label("")
        # Reachability arrives shortly after launch; keep the empty-state
        # guidance in sync when nothing is loaded yet, and re-pull once the
        # phone is reachable in case SMS access was just granted.
        if not self.kdec.conversations:
            self._update_conv_empty_state(0)
            if self.kdec.is_reachable and not self._resynced_on_reach:
                self._resynced_on_reach = True
                GLib.timeout_add(500, lambda: self.kdec.load_conversations() or False)

    def _refresh_media(self):
        if demo.ENABLED:
            self.media_card.set_visible(True)
            self.media_player_label.set_label("Music")
            self.media_title_label.set_label("Midnight Avenue")
            self.media_artist_label.set_label("The Lantern Club")
            self.media_playpause_btn.set_icon_name("media-playback-pause-symbolic")
            return
        title = self.kdec.media_title
        self.media_card.set_visible(bool(title))
        if not title:
            return
        self.media_player_label.set_label(self.kdec.media_player)
        self.media_title_label.set_label(title)
        self.media_artist_label.set_label(self.kdec.media_artist)
        self.media_playpause_btn.set_icon_name(
            "media-playback-pause-symbolic" if self.kdec.media_is_playing
            else "media-playback-start-symbolic")

    def _queue_notif_refresh(self):
        if not self._notif_refresh_pending:
            self._notif_refresh_pending = True
            GLib.timeout_add(300, self._do_notif_refresh)

    def _do_notif_refresh(self):
        self._notif_refresh_pending = False
        self._refresh_notifications()
        return False

    def _refresh_notifications(self):
        self.notif_list.remove_all()
        notifs = demo.NOTIFICATIONS if demo.ENABLED else self.kdec.active_notifications()
        for n in notifs:
            row = Gtk.ListBoxRow(activatable=False)
            outer = Gtk.Box(spacing=8, margin_top=8, margin_bottom=8,
                            margin_start=10, margin_end=6)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
            app = Gtk.Label(label=n.app_name, xalign=0)
            app.add_css_class("caption")
            app.add_css_class("dim-label")
            inner.append(app)
            if n.title:
                t = Gtk.Label(label=n.title, xalign=0, wrap=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR)
                t.add_css_class("heading")
                inner.append(t)
            if n.text:
                body = Gtk.Label(label=n.text, xalign=0, wrap=True,
                                 wrap_mode=Pango.WrapMode.WORD_CHAR, lines=3,
                                 ellipsize=Pango.EllipsizeMode.END)
                inner.append(body)
            if n.reply_id:
                reply_box = Gtk.Box(spacing=6)
                reply_entry = Gtk.Entry(hexpand=True, placeholder_text="Reply",
                                        visible=False)
                reply_entry.connect("activate", self._on_notification_reply, n.nid)
                reply_btn = Gtk.Button(icon_name="mail-reply-sender-symbolic",
                                       tooltip_text="Reply", halign=Gtk.Align.START)
                reply_btn.add_css_class("flat")
                reply_btn.connect(
                    "clicked",
                    lambda _b, e=reply_entry: (e.set_visible(True), e.grab_focus()))
                reply_box.append(reply_btn)
                reply_box.append(reply_entry)
                inner.append(reply_box)
            outer.append(inner)
            if n.dismissable:
                x = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.START)
                x.add_css_class("flat")
                x.connect("clicked", self._on_dismiss_notification, n.nid)
                outer.append(x)
            row.set_child(outer)
            self.notif_list.append(row)

    def _on_notification_reply(self, entry, nid):
        text = entry.get_text().strip()
        if not text:
            return
        self.kdec.notification_reply(nid, text)
        entry.set_text("")
        entry.set_visible(False)
        self.toasts.add_toast(Adw.Toast(title="Reply sent", timeout=2))

    def _on_dismiss_notification(self, _btn, nid):
        self.kdec.dismiss_notification(nid)
        self._queue_notif_refresh()

    def _on_clear_notifications(self, _btn):
        for n in self.kdec.active_notifications():
            if n.dismissable:
                self.kdec.dismiss_notification(n.nid)
        self._queue_notif_refresh()

    # -- main pane --------------------------------------------------------

    def _build_main(self):
        self.stack = Adw.ViewStack(hexpand=True)
        self.stack.add_titled_with_icon(
            self._build_messages(), "messages", "Messages", "chat-message-new-symbolic")
        self.stack.add_titled_with_icon(
            self._build_calls(), "calls", "Calls", "call-start-symbolic")
        self.stack.add_titled_with_icon(
            self._build_photos(), "photos", "Photos", "image-x-generic-symbolic")

        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header = Adw.HeaderBar(title_widget=switcher)
        toggle = Gtk.Button(icon_name="sidebar-show-symbolic",
                            tooltip_text="Notifications")
        toggle.connect("clicked", self._on_toggle_sidebar)
        header.pack_start(toggle)
        mirror = Gtk.Button(icon_name="video-display-symbolic",
                            tooltip_text="Mirror phone screen (scrcpy)")
        mirror.connect("clicked", self._on_mirror)
        header.pack_end(mirror)

        view = Adw.ToolbarView(hexpand=True)
        view.add_top_bar(header)
        view.set_content(self.stack)
        return view

    def _on_toggle_sidebar(self, _btn):
        self.split.set_show_sidebar(not self.split.get_show_sidebar())

    def _on_mirror(self, _btn):
        mirror.start(self, self.kdec, self.toasts)

    # -- messages ---------------------------------------------------------

    def _build_messages(self):
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(margin_top=10, margin_bottom=6, margin_start=14, margin_end=10)
        lbl = Gtk.Label(label="Messages", xalign=0, hexpand=True)
        lbl.add_css_class("title-3")
        head.append(lbl)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic",
                             tooltip_text="Refresh from phone")
        refresh.add_css_class("flat")
        refresh.connect("clicked", self._on_refresh_conversations)
        head.append(refresh)
        show_hidden = Gtk.ToggleButton(icon_name="view-reveal-symbolic",
                                       tooltip_text="Show hidden conversations")
        show_hidden.add_css_class("flat")
        show_hidden.connect("toggled", self._on_toggle_show_hidden)
        head.append(show_hidden)
        compose = Gtk.Button(icon_name="document-edit-symbolic",
                             tooltip_text="New message")
        compose.add_css_class("circular")
        compose.add_css_class("suggested-action")
        compose.connect("clicked", self._on_compose)
        head.append(compose)
        left.append(head)

        # Search across conversations — matches contact/group name, number,
        # and the last-message text; also searches full history the app has
        # loaded so far.
        self.search_entry = Gtk.SearchEntry(
            placeholder_text="Search messages and contacts",
            margin_start=12, margin_end=12, margin_bottom=6)
        self.search_entry.connect("search-changed", self._on_search_changed)
        left.append(self.search_entry)
        self._search_query = ""

        self.conv_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.MULTIPLE)
        self.conv_list.add_css_class("navigation-sidebar")
        self.conv_list.connect("selected-rows-changed", self._on_selection_changed)
        range_gesture = Gtk.GestureClick()
        range_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        range_gesture.connect("pressed", self._on_list_pressed)
        self.conv_list.add_controller(range_gesture)
        sc = Gtk.ScrolledWindow(vexpand=True, child=self.conv_list)
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        # Empty / status state swapped in when there are no conversations —
        # a fresh pair shows nothing until the phone grants SMS access.
        self.conv_status = Adw.StatusPage(vexpand=True)
        self.conv_status.add_css_class("compact")
        self.conv_stack = Gtk.Stack(vexpand=True)
        self.conv_stack.add_named(sc, "list")
        self.conv_stack.add_named(self.conv_status, "status")
        left.append(self.conv_stack)

        self.sel_bar = Gtk.ActionBar(revealed=False)
        self.sel_label = Gtk.Label()
        self.sel_bar.pack_start(self.sel_label)
        sel_cancel = Gtk.Button(label="Cancel")
        sel_cancel.add_css_class("flat")
        sel_cancel.connect("clicked", lambda *a: self._exit_multi_select())
        self.sel_bar.pack_end(sel_cancel)
        self.sel_hide_btn = Gtk.Button(label="Hide")
        self.sel_hide_btn.add_css_class("suggested-action")
        self.sel_hide_btn.connect("clicked", self._on_bulk_hide)
        self.sel_bar.pack_end(self.sel_hide_btn)
        left.append(self.sel_bar)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.thread_header = Gtk.Box(spacing=10, margin_top=10, margin_bottom=10,
                                     margin_start=16, margin_end=16)
        # Back button — only meaningful when the list/thread split is
        # collapsed to single-pane navigation on a narrow window.
        self.thread_back = Gtk.Button(icon_name="go-previous-symbolic",
                                      tooltip_text="Back to conversations")
        self.thread_back.add_css_class("flat")
        self.thread_back.connect("clicked",
                                 lambda *a: self.msg_split.set_show_content(False))
        self.thread_header.append(self.thread_back)
        self.thread_avatar = Adw.Avatar(size=36, show_initials=True)
        self.thread_header.append(self.thread_avatar)
        tv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.thread_name = Gtk.Label(label="Select a conversation", xalign=0,
                                     ellipsize=Pango.EllipsizeMode.END)
        self.thread_name.add_css_class("title-4")
        tv.append(self.thread_name)
        self.thread_sub = Gtk.Label(label="", xalign=0,
                                    ellipsize=Pango.EllipsizeMode.END)
        self.thread_sub.add_css_class("dim-label")
        self.thread_sub.add_css_class("caption")
        tv.append(self.thread_sub)
        self.thread_header.append(tv)
        spacer = Gtk.Box(hexpand=True)
        self.thread_header.append(spacer)
        self.hide_btn = Gtk.Button(icon_name="view-conceal-symbolic",
                                   tooltip_text="Hide conversation", visible=False)
        self.hide_btn.add_css_class("flat")
        self.hide_btn.connect("clicked", self._on_toggle_hide)
        self.thread_header.append(self.hide_btn)
        right.append(self.thread_header)
        right.append(Gtk.Separator())

        self.bubble_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                                  margin_top=12, margin_bottom=12,
                                  margin_start=16, margin_end=16, valign=Gtk.Align.END)
        self.thread_scroll = Gtk.ScrolledWindow(vexpand=True, child=self.bubble_box)
        self.thread_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        right.append(self.thread_scroll)

        composer_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.attach_chip = Gtk.Box(spacing=6, margin_start=16, margin_end=16,
                                   margin_top=6, visible=False)
        self.attach_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.attach_label.add_css_class("dim-label")
        self.attach_chip.append(self.attach_label)
        clear_att = Gtk.Button(icon_name="window-close-symbolic")
        clear_att.add_css_class("flat")
        clear_att.connect("clicked", self._on_clear_attachments)
        self.attach_chip.append(clear_att)
        composer_col.append(self.attach_chip)

        composer = Gtk.Box(spacing=8, margin_top=8, margin_bottom=12,
                           margin_start=16, margin_end=16)
        attach = Gtk.Button(icon_name="mail-attachment-symbolic",
                            tooltip_text="Attach file")
        attach.add_css_class("flat")
        attach.connect("clicked", self._on_pick_attachment)
        composer.append(attach)
        self.entry = Gtk.Entry(hexpand=True, placeholder_text="Send a message")
        self.entry.connect("activate", self._on_send)
        composer.append(self.entry)
        send = Gtk.Button(icon_name="document-send-symbolic", tooltip_text="Send")
        send.add_css_class("suggested-action")
        send.add_css_class("circular")
        send.connect("clicked", self._on_send)
        composer.append(send)
        composer_col.append(composer)
        right.append(composer_col)

        self.msg_split = Adw.NavigationSplitView(
            sidebar=Adw.NavigationPage(child=left, title="Messages"),
            content=Adw.NavigationPage(child=right, title="Conversation"),
            min_sidebar_width=280,
            max_sidebar_width=360,
        )
        self.msg_split.bind_property(
            "collapsed", self.thread_back, "visible",
            GObject.BindingFlags.SYNC_CREATE)
        return self.msg_split

    def _clear_thread_pane(self):
        self.current_thread = None
        self.thread_name.set_label("Select a conversation")
        self.thread_sub.set_label("")
        self.thread_avatar.set_text("")
        self.hide_btn.set_visible(False)
        child = self.bubble_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.bubble_box.remove(child)
            child = nxt

    def _on_refresh_conversations(self, _btn):
        self._requested_threads.clear()
        self.conv_list.remove_all()
        self._clear_thread_pane()
        self.kdec.refresh_conversations()
        self.toasts.add_toast(Adw.Toast(title="Refreshing from phone…", timeout=3))

    def _save_hidden(self):
        os.makedirs(os.path.dirname(_HIDDEN_FILE), exist_ok=True)
        with open(_HIDDEN_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(self._hidden), f)

    def _on_toggle_show_hidden(self, btn):
        self._show_hidden = btn.get_active()
        self._refresh_conversations()

    def _on_toggle_hide(self, _btn):
        tid = self.current_thread
        if tid is None:
            return
        if tid in self._hidden:
            self._hidden.discard(tid)
            self.toasts.add_toast(Adw.Toast(title="Conversation unhidden", timeout=2))
            self.hide_btn.set_icon_name("view-conceal-symbolic")
            self.hide_btn.set_tooltip_text("Hide conversation")
        else:
            self._hidden.add(tid)
            self.toasts.add_toast(Adw.Toast(title="Conversation hidden", timeout=2))
            self._clear_thread_pane()
            self.msg_split.set_show_content(False)
        self._save_hidden()
        self._refresh_conversations()

    def _on_contacts_synced(self, _kdec):
        # vcards just landed for a fresh pair — reload names and redraw.
        self.contacts.reload(self.kdec.device_id)
        self._refresh_conversations()
        if self.current_thread is not None:
            conv = self.kdec.conversations.get(self.current_thread)
            if conv:
                self.thread_name.set_label(self.contacts.display(conv.addresses))

    def _on_search_changed(self, entry):
        self._search_query = entry.get_text().strip().lower()
        self._refresh_conversations()

    def _conv_matches(self, conv):
        """True if the search query matches this conversation's contact
        name, any address, or any message text the app has loaded."""
        q = self._search_query
        if not q:
            return True
        if q in self.contacts.display(conv.addresses).lower():
            return True
        if any(q in a.lower() for a in conv.addresses):
            return True
        return any(q in (m.body or "").lower() for m in conv.messages.values())

    def _on_message(self, _kdec, msg):
        # A genuinely new incoming message resurfaces a hidden thread
        # (like unarchiving). The age check keeps daemon re-syncs of old
        # messages from unhiding everything.
        if (msg.thread_id in self._hidden and msg.type != MESSAGE_SENT
                and msg.date / 1000 > time.time() - 300):
            self._hidden.discard(msg.thread_id)
            self._save_hidden()
        if not self._conv_refresh_pending:
            self._conv_refresh_pending = True
            GLib.timeout_add(250, self._do_conv_refresh)
        if msg.thread_id == self.current_thread and not self._thread_render_pending:
            self._thread_render_pending = True
            GLib.timeout_add(150, self._do_thread_render)

    def _do_conv_refresh(self):
        self._conv_refresh_pending = False
        self._refresh_conversations()
        return False

    def _do_thread_render(self):
        self._thread_render_pending = False
        self._render_thread()
        return False

    def _refresh_conversations(self):
        if self._multi_mode:
            return  # don't wipe an in-progress selection; catch up on exit
        selected = self.current_thread
        self._sel_anchor = None  # row indices change across rebuilds
        self._suppress_select = True
        try:
            self.conv_list.remove_all()
            convs = sorted(
                (c for c in self.kdec.conversations.values()
                 if c.last_message
                 and (self._show_hidden or c.thread_id not in self._hidden)
                 and self._conv_matches(c)),
                key=lambda c: c.last_message.date, reverse=True,
            )[:150]
            for conv in convs:
                last = conv.last_message
                row = Gtk.ListBoxRow()
                row.thread_id = conv.thread_id
                if conv.thread_id in self._hidden:
                    row.add_css_class("hidden-thread")
                outer = Gtk.Box(spacing=10, margin_top=8, margin_bottom=8,
                                margin_start=8, margin_end=8)
                avatar = Adw.Avatar(size=38, show_initials=True,
                                    text=self.contacts.display(conv.addresses))
                check = Gtk.Image.new_from_icon_name("object-select-symbolic")
                check.add_css_class("selection-check")
                check.set_visible(False)
                overlay = Gtk.Overlay(child=avatar)
                overlay.add_overlay(check)
                row.check = check
                row.avatar_overlay = overlay
                gesture = Gtk.GestureClick()
                gesture.connect("pressed", self._on_avatar_pressed, row)
                overlay.add_controller(gesture)
                outer.append(overlay)
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
                top = Gtk.Box()
                name = Gtk.Label(label=self.contacts.display(conv.addresses), xalign=0,
                                 hexpand=True, ellipsize=Pango.EllipsizeMode.END)
                name.add_css_class("heading")
                top.append(name)
                when = Gtk.Label(label=_fmt_time(last.date))
                when.add_css_class("caption")
                when.add_css_class("dim-label")
                top.append(when)
                inner.append(top)
                if demo.ENABLED:
                    snippet = demo.fake_snippet(conv.thread_id)
                else:
                    snippet = " ".join(
                        (last.body or ("[attachment]" if last.has_attachments else "")).split()
                    )
                sn = Gtk.Label(label=snippet, xalign=0,
                               ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
                sn.add_css_class("dim-label")
                inner.append(sn)
                outer.append(inner)
                row.set_child(outer)
                self.conv_list.append(row)
                if conv.thread_id == selected:
                    self.conv_list.select_row(row)
            self._update_conv_empty_state(len(convs))
        finally:
            self._suppress_select = False

    def _update_conv_empty_state(self, count):
        if count > 0:
            self.conv_stack.set_visible_child_name("list")
            return
        self.conv_stack.set_visible_child_name("status")
        if self._search_query:
            self.conv_status.set_icon_name("system-search-symbolic")
            self.conv_status.set_title("No matches")
            self.conv_status.set_description(
                "No conversations match your search. Try a different name, "
                "number, or word. Open a thread first to search deeper history.")
            return
        if not self.kdec.device_id:
            self.conv_status.set_icon_name("phone-disabled-symbolic")
            self.conv_status.set_title("No phone connected")
            self.conv_status.set_description(
                "Pair your phone in the KDE Connect app, then press refresh.")
        elif not self.kdec.is_reachable:
            self.conv_status.set_icon_name("network-cellular-offline-symbolic")
            self.conv_status.set_title("Phone unreachable")
            self.conv_status.set_description(
                "Make sure the phone is on the same Wi-Fi and awake, then refresh.")
        else:
            self.conv_status.set_icon_name("chat-message-new-symbolic")
            self.conv_status.set_title("No conversations")
            self.conv_status.set_description(
                "If this is a new pairing, open KDE Connect on the phone and grant "
                "SMS, Contacts, and Notification access — then press refresh.")

    # -- multi-select -----------------------------------------------------

    def _on_avatar_pressed(self, gesture, _n, _x, _y, row):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._multi_mode = True
        if gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK:
            self._select_range_to(row)
            return
        self._sel_anchor = row.get_index()
        if row.is_selected():
            self.conv_list.unselect_row(row)
        else:
            self.conv_list.select_row(row)

    def _on_list_pressed(self, gesture, _n, x, y):
        """All row clicks route through here (capture phase), because
        ListBox's MULTIPLE mode makes plain clicks additive — a second
        click would silently start a multi-selection. Plain click =
        replace selection (open thread); ctrl = toggle; shift = range.
        Clicks on the avatar circle fall through to its own gesture."""
        row = self.conv_list.get_row_at_y(int(y))
        if row is None:
            return
        overlay = getattr(row, "avatar_overlay", None)
        if overlay is not None:
            ok, rect = overlay.compute_bounds(self.conv_list)
            if (ok and rect.get_x() <= x <= rect.get_x() + rect.get_width()
                    and rect.get_y() <= y <= rect.get_y() + rect.get_height()):
                return
        state = gesture.get_current_event_state()
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if state & Gdk.ModifierType.SHIFT_MASK:
            self._multi_mode = True
            self._select_range_to(row)
        elif state & Gdk.ModifierType.CONTROL_MASK:
            self._multi_mode = True
            self._sel_anchor = row.get_index()
            if row.is_selected():
                self.conv_list.unselect_row(row)
            else:
                self.conv_list.select_row(row)
        elif self._multi_mode:
            # In selection mode a plain tap keeps toggling, phone-style.
            if row.is_selected():
                self.conv_list.unselect_row(row)
            else:
                self.conv_list.select_row(row)
        else:
            self.conv_list.unselect_all()
            self.conv_list.select_row(row)

    def _select_range_to(self, row):
        if self._sel_anchor is None:
            self._sel_anchor = row.get_index()
        lo, hi = sorted((self._sel_anchor, row.get_index()))
        for i in range(lo, hi + 1):
            r = self.conv_list.get_row_at_index(i)
            if r is not None:
                self.conv_list.select_row(r)

    def _on_selection_changed(self, listbox):
        if self._suppress_select:
            return
        rows = listbox.get_selected_rows()
        if len(rows) > 1:
            self._multi_mode = True
        if not rows:
            self._multi_mode = False
        child = listbox.get_first_child()
        while child:
            if hasattr(child, "check"):
                child.check.set_visible(self._multi_mode and child.is_selected())
            child = child.get_next_sibling()
        self.sel_bar.set_revealed(self._multi_mode)
        if self._multi_mode:
            n = len(rows)
            self.sel_label.set_label(f"{n} selected")
            all_hidden = all(r.thread_id in self._hidden for r in rows) if rows else False
            self.sel_hide_btn.set_label("Unhide" if all_hidden else "Hide")
        elif len(rows) == 1:
            self._open_thread(rows[0])

    def _exit_multi_select(self):
        self._multi_mode = False
        self._suppress_select = True
        self.conv_list.unselect_all()
        self._suppress_select = False
        self.sel_bar.set_revealed(False)
        child = self.conv_list.get_first_child()
        while child:
            if hasattr(child, "check"):
                child.check.set_visible(False)
            child = child.get_next_sibling()
        self._refresh_conversations()

    def _on_bulk_hide(self, _btn):
        rows = self.conv_list.get_selected_rows()
        if not rows:
            return
        tids = [r.thread_id for r in rows]
        if all(t in self._hidden for t in tids):
            self._hidden.difference_update(tids)
            msg = f"{len(tids)} conversations unhidden"
        else:
            self._hidden.update(tids)
            msg = f"{len(tids)} conversations hidden"
            if self.current_thread in self._hidden:
                self._clear_thread_pane()
        self._save_hidden()
        self._exit_multi_select()
        self.toasts.add_toast(Adw.Toast(title=msg, timeout=2))

    def _open_thread(self, row):
        self._sel_anchor = row.get_index()
        if row.thread_id == self.current_thread:
            return
        self.current_thread = row.thread_id
        conv = self.kdec.conversations.get(row.thread_id)
        if not conv:
            return
        display = self.contacts.display(conv.addresses)
        self.thread_name.set_label(display)
        self.thread_avatar.set_text(display)
        raw = (", ".join(demo.fake_number(a) for a in conv.addresses)
               if demo.ENABLED else ", ".join(conv.addresses))
        self.thread_sub.set_label(raw if raw != display else "")
        hidden = row.thread_id in self._hidden
        self.hide_btn.set_visible(True)
        self.hide_btn.set_icon_name(
            "view-reveal-symbolic" if hidden else "view-conceal-symbolic")
        self.hide_btn.set_tooltip_text(
            "Unhide conversation" if hidden else "Hide conversation")
        # Request history whenever the thread only has its one cached
        # message — gating on "was it ever requested" was wrong: if the
        # first request came back empty (cold daemon cache), the thread
        # got marked done and reopening never retried, so it was stuck
        # showing a single message. Gating on the actual message count
        # re-requests until history loads, then stops.
        conv = self.kdec.conversations.get(row.thread_id)
        if conv and len(conv.messages) <= 1:
            self._requested_threads.add(row.thread_id)
            self.kdec.request_conversation(row.thread_id)
            # Self-heal for a cold daemon cache: if history still hasn't
            # grown shortly, warm the whole cache and retry this thread.
            GLib.timeout_add(1500, self._ensure_thread_loaded, row.thread_id)
        self._render_thread()
        self.msg_split.set_show_content(True)

    def _ensure_thread_loaded(self, thread_id):
        if thread_id != self.current_thread:
            return False
        conv = self.kdec.conversations.get(thread_id)
        if conv and len(conv.messages) <= 1:
            self.kdec.load_conversations()  # warm the daemon cache
            GLib.timeout_add(
                1500, lambda: self.kdec.request_conversation(thread_id) or False)
        return False

    def _render_thread(self):
        child = self.bubble_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.bubble_box.remove(child)
            child = nxt
        conv = self.kdec.conversations.get(self.current_thread)
        if not conv:
            return
        for msg in conv.sorted_messages():
            sent = msg.type == MESSAGE_SENT
            wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                           halign=Gtk.Align.END if sent else Gtk.Align.START)
            for att in msg.attachments:
                wrap.append(self._attachment_widget(att))
            body = demo.fake_snippet(msg.uid) if demo.ENABLED else msg.body
            if body:
                lbl = Gtk.Label(label=body, wrap=True,
                                wrap_mode=Pango.WrapMode.WORD_CHAR,
                                xalign=0, selectable=True, max_width_chars=46,
                                tooltip_text=_fmt_time(msg.date))
                lbl.add_css_class("bubble-out" if sent else "bubble-in")
                lbl.set_halign(Gtk.Align.END if sent else Gtk.Align.START)
                wrap.append(lbl)
            if wrap.get_first_child() is not None:
                self.bubble_box.append(wrap)
        GLib.idle_add(self._scroll_to_bottom)

    def _attachment_widget(self, att):
        is_image = att.mime_type.startswith("image/")
        btn = Gtk.Button(tooltip_text=f"{att.mime_type} — click to open")
        btn.add_css_class("attachment")
        child = None
        cached = self._attachment_cache_path(att.part_name)
        if is_image and os.path.isfile(cached):
            # Full file already downloaded — render a proper-resolution preview.
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(cached, 340, 420, True)
                child = Gtk.Picture.new_for_paintable(Gdk.Texture.new_for_pixbuf(pb))
                child.set_content_fit(Gtk.ContentFit.CONTAIN)
                child.set_can_shrink(False)
                child.set_size_request(max(pb.get_width(), 140), pb.get_height())
            except GLib.Error:
                child = None
        if child is None and att.thumbnail_b64:
            try:
                data = GLib.base64_decode(att.thumbnail_b64)
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
                child = Gtk.Picture.new_for_paintable(texture)
                child.set_size_request(180, 180)
                child.set_content_fit(Gtk.ContentFit.COVER)
                child.set_can_shrink(False)
                if is_image:
                    # Fetch the real file so the preview upgrades itself.
                    self._download(att, action="preview")
            except GLib.Error:
                child = None
        if child is None:
            child = Gtk.Label(label=f"📎 {att.mime_type}")
        btn.set_child(child)
        btn.connect("clicked", self._on_attachment_clicked, att)
        return btn

    def _download(self, att, action):
        if att.part_name in self._pending_downloads:
            return
        self._pending_downloads[att.part_name] = (action, att.mime_type)
        self.kdec.request_attachment(att.part_id, att.part_name)

    def _on_attachment_clicked(self, _btn, att):
        cached = self._attachment_cache_path(att.part_name)
        if os.path.isfile(cached):
            self._open_attachment(cached, att.mime_type)
        else:
            self._download(att, action="open")
            self.toasts.add_toast(Adw.Toast(title="Downloading attachment…", timeout=2))

    def _on_attachment_received(self, _kdec, file_path, part_name):
        action, mime = self._pending_downloads.pop(part_name, (None, ""))
        if action == "open":
            self._open_attachment(file_path, mime)
        elif action == "preview" and not self._thread_render_pending:
            self._thread_render_pending = True
            GLib.timeout_add(200, self._do_thread_render)

    def _open_attachment(self, file_path, mime):
        if mime.startswith("image/") and self._show_image_viewer(file_path):
            return
        # kdeconnect caches attachments with no file extension (e.g.
        # PART_1787…), so the desktop can't resolve a default app and shows
        # the "open with" picker. Symlink to a name carrying the right
        # extension so it opens straight in the default video/media player.
        launch_path = self._path_with_extension(file_path, mime)
        Gtk.FileLauncher(file=Gio.File.new_for_path(launch_path)).launch(self, None, None)

    # Common phone/MMS types that Python's mimetypes doesn't know.
    _MIME_EXT_FALLBACK = {
        "video/3gpp": ".3gp", "video/3gpp2": ".3g2", "audio/amr": ".amr",
        "audio/aac": ".aac", "audio/x-caf": ".caf", "image/heic": ".heic",
        "image/heif": ".heif", "video/x-matroska": ".mkv",
    }

    def _path_with_extension(self, file_path, mime):
        if os.path.splitext(file_path)[1]:
            return file_path
        mime = (mime or "").split(";")[0].strip()
        ext = mimetypes.guess_extension(mime) or self._MIME_EXT_FALLBACK.get(mime)
        if not ext:
            return file_path
        link_dir = os.path.expanduser("~/.cache/omalink/open")
        try:
            os.makedirs(link_dir, exist_ok=True)
            link = os.path.join(link_dir, os.path.basename(file_path) + ext)
            if os.path.lexists(link):
                os.remove(link)
            os.symlink(file_path, link)
            return link
        except OSError:
            return file_path

    def _show_image_viewer(self, file_path):
        try:
            texture = Gdk.Texture.new_from_filename(file_path)
        except GLib.Error:
            return False  # e.g. HEIC without a pixbuf loader — hand off to the OS
        pic = Gtk.Picture.new_for_paintable(texture)
        pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        pic.set_hexpand(True)
        pic.set_vexpand(True)

        # A real toplevel window (not Adw.Dialog) so the tiling WM manages
        # it: Hyprland tiles it, focus borders apply, and the standard
        # close keybind works.
        win = Adw.Window(title=os.path.basename(file_path),
                         application=self.get_application())
        w, h = texture.get_width(), texture.get_height()
        scale = min(1.0, 1100 / max(w, 1), 800 / max(h, 1))
        win.set_default_size(max(int(w * scale), 420), max(int(h * scale), 320) + 46)

        header = Adw.HeaderBar()
        save = Gtk.Button(icon_name="document-save-symbolic", tooltip_text="Save as…")
        save.connect("clicked", self._on_save_image, file_path, win)
        header.pack_start(save)
        view = Adw.ToolbarView(content=pic)
        view.add_top_bar(header)
        win.set_content(view)

        keys = Gtk.EventControllerKey()
        keys.connect(
            "key-pressed",
            lambda _c, keyval, *_a: win.close() or True
            if keyval == Gdk.KEY_Escape else False)
        win.add_controller(keys)
        win.present()
        return True

    def _on_save_image(self, _btn, file_path, parent):
        def done(dialog, result):
            try:
                dest = dialog.save_finish(result)
            except GLib.Error:
                return
            shutil.copyfile(file_path, dest.get_path())
            self.toasts.add_toast(Adw.Toast(title="Image saved", timeout=2))

        fd = Gtk.FileDialog(initial_name=os.path.basename(file_path) + ".jpg")
        fd.save(parent, None, done)

    def _scroll_to_bottom(self):
        adj = self.thread_scroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def _on_pick_attachment(self, _btn):
        Gtk.FileDialog(title="Attach files").open_multiple(self, None, self._on_files_picked)

    def _on_files_picked(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error:
            return
        self._outgoing_attachments = [
            f.get_path() for f in files if f.get_path()
        ]
        self._update_attach_chip()

    def _on_clear_attachments(self, _btn):
        self._outgoing_attachments = []
        self._update_attach_chip()

    def _update_attach_chip(self):
        n = len(self._outgoing_attachments)
        self.attach_chip.set_visible(n > 0)
        if n:
            names = ", ".join(p.rsplit("/", 1)[-1] for p in self._outgoing_attachments)
            self.attach_label.set_label(f"📎 {names}")

    def _on_send(self, _widget):
        text = self.entry.get_text().strip()
        conv = self.kdec.conversations.get(self.current_thread)
        if not conv or (not text and not self._outgoing_attachments):
            return
        self.kdec.reply_to_conversation(conv.thread_id, text, self._outgoing_attachments)
        self.entry.set_text("")
        self._on_clear_attachments(None)

    def _on_compose(self, _btn):
        dialog = Adw.AlertDialog(heading="New message",
                                 body="Search a contact by name, or type a number.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        recipient = Gtk.Entry(placeholder_text="Name or phone number")
        box.append(recipient)

        # Live contact matches — pick one to fill the recipient. A number
        # typed directly is used as-is, so unknown numbers still work.
        results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        results.add_css_class("boxed-list")
        results_sc = Gtk.ScrolledWindow(child=results, min_content_height=160,
                                        max_content_height=220, propagate_natural_height=True)
        results_sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box.append(results_sc)

        text = Gtk.Entry(placeholder_text="Message")
        box.append(text)

        chosen = {"number": None}

        def fill_results(entry):
            results.remove_all()
            chosen["number"] = None
            for name, num in self.contacts.search(entry.get_text(), limit=30):
                row = Gtk.ListBoxRow()
                row.number = num
                r = Gtk.Box(spacing=8, margin_top=6, margin_bottom=6,
                            margin_start=10, margin_end=10)
                av = Adw.Avatar(size=28, show_initials=True, text=name)
                r.append(av)
                col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
                nm = Gtk.Label(label=name, xalign=0)
                nm.add_css_class("heading")
                col.append(nm)
                sub = Gtk.Label(label=num, xalign=0)
                sub.add_css_class("caption")
                sub.add_css_class("dim-label")
                col.append(sub)
                r.append(col)
                row.set_child(r)
                results.append(row)

        def on_row(_list, row):
            if row is not None:
                chosen["number"] = row.number
                text.grab_focus()

        recipient.connect("changed", fill_results)
        results.connect("row-selected", on_row)
        fill_results(recipient)

        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("send", "Send")
        dialog.set_response_appearance("send", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_compose_response, recipient, text, chosen)
        dialog.present(self)

    def _on_compose_response(self, _dialog, response, recipient, text, chosen):
        if response != "send":
            return
        # A picked contact wins; otherwise use whatever was typed (a number).
        num = chosen.get("number") or recipient.get_text().strip()
        body = text.get_text().strip()
        if num and body:
            self.kdec.send_new_sms([num], body)

    # -- photos -----------------------------------------------------------

    def _build_photos(self):
        self.photos_stack = Gtk.Stack()

        empty = Adw.StatusPage(
            icon_name="image-x-generic-symbolic", title="Photos",
            description="Browse the newest camera photos on your phone.")
        load_btn = Gtk.Button(label="Load photos", halign=Gtk.Align.CENTER)
        load_btn.add_css_class("pill")
        load_btn.add_css_class("suggested-action")
        load_btn.connect("clicked", self._on_load_photos)
        empty.set_child(load_btn)
        self.photos_stack.add_named(empty, "empty")

        spinner_page = Adw.StatusPage(title="Mounting phone…")
        spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32,
                              halign=Gtk.Align.CENTER)
        spinner_page.set_child(spinner)
        self.photos_stack.add_named(spinner_page, "loading")

        grid_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar = Gtk.Box(spacing=8, margin_top=10, margin_bottom=6,
                      margin_start=14, margin_end=14)
        lbl = Gtk.Label(label="Recent camera photos", xalign=0, hexpand=True)
        lbl.add_css_class("title-3")
        bar.append(lbl)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        refresh.add_css_class("flat")
        refresh.connect("clicked", self._on_load_photos)
        bar.append(refresh)
        folder = Gtk.Button(icon_name="folder-open-symbolic",
                            tooltip_text="Open folder in file manager")
        folder.add_css_class("flat")
        folder.connect("clicked", self._on_open_photo_folder)
        bar.append(folder)
        grid_box.append(bar)
        self.photo_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                      max_children_per_line=6, column_spacing=8,
                                      row_spacing=8, margin_start=14, margin_end=14,
                                      margin_bottom=14, homogeneous=True,
                                      valign=Gtk.Align.START)
        sc = Gtk.ScrolledWindow(vexpand=True, child=self.photo_flow)
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        grid_box.append(sc)
        self.photos_stack.add_named(grid_box, "grid")

        self._camera_dir = None
        return self.photos_stack

    def _on_load_photos(self, _btn):
        self.photos_stack.set_visible_child_name("loading")
        self.kdec.sftp_mount()

    def _on_sftp_mounted(self, _kdec, ok, mount_point):
        if not ok:
            self.photos_stack.set_visible_child_name("empty")
            err = self.kdec.sftp_mount_error() or "Couldn't mount the phone"
            if "sshfs" in err:
                err += " — install it: sudo pacman -S sshfs"
            self.toasts.add_toast(Adw.Toast(title=err, timeout=6))
            return
        camera = None
        for name, path in self.kdec.sftp_directories().items():
            if "camera" in name.lower() or path.rstrip("/").endswith("DCIM/Camera"):
                camera = path
                break
        if camera is None:
            candidate = os.path.join(mount_point, "storage/emulated/0/DCIM/Camera")
            camera = candidate if os.path.isdir(candidate) else None
        if camera is None or not os.path.isdir(camera):
            self.photos_stack.set_visible_child_name("empty")
            self.toasts.add_toast(Adw.Toast(title="Camera folder not found", timeout=3))
            return
        self._camera_dir = camera
        self._populate_photos(camera)

    def _populate_photos(self, camera):
        exts = (".jpg", ".jpeg", ".png", ".webp")
        try:
            entries = [e for e in os.scandir(camera)
                       if e.is_file() and e.name.lower().endswith(exts)]
        except OSError:
            self.photos_stack.set_visible_child_name("empty")
            return
        entries.sort(key=lambda e: e.name, reverse=True)
        self.photo_flow.remove_all()
        self.photos_stack.set_visible_child_name("grid")
        queue = [e.path for e in entries[:24]]

        def load_next():
            if not queue:
                return False
            path = queue.pop(0)
            pb = self._photo_thumbnail(path)
            if pb is None:
                return True
            pic = Gtk.Picture.new_for_paintable(Gdk.Texture.new_for_pixbuf(pb))
            pic.set_size_request(180, 180)
            pic.set_content_fit(Gtk.ContentFit.COVER)
            btn = Gtk.Button(child=pic, tooltip_text=os.path.basename(path))
            btn.add_css_class("attachment")
            btn.connect("clicked",
                        lambda _b, p=path: self._open_attachment(p, "image/jpeg"))
            self.photo_flow.append(btn)
            return True

        GLib.idle_add(load_next)

    def _photo_thumbnail(self, path):
        """Scaled thumbnail for a phone photo, cached locally. Reading a
        photo off the sshfs mount pulls the whole full-size file over the
        network, so without a cache every visit re-downloads everything.
        Cache key includes size+mtime so an edited/replaced photo refreshes."""
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = hashlib.md5(
            f"{path}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()
        cache = os.path.expanduser(f"~/.cache/omalink/thumbs/{key}.png")
        if os.path.exists(cache):
            try:
                return GdkPixbuf.Pixbuf.new_from_file(cache)
            except GLib.Error:
                pass
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 200, 200, True)
        except GLib.Error:
            return None
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            pb.savev(cache, "png", [], [])
        except (GLib.Error, OSError):
            pass
        return pb

    def _on_open_photo_folder(self, _btn):
        if self._camera_dir:
            Gtk.FileLauncher(
                file=Gio.File.new_for_path(self._camera_dir)).launch(self, None, None)

    # -- calls ------------------------------------------------------------

    def _build_calls(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.calls_status = Adw.StatusPage(
            icon_name="call-start-symbolic", title="Calls",
            description=("Incoming call events appear here while Omalink is running.\n"
                         "KDE Connect does not expose the call log or dialing."),
            vexpand=True,
        )
        self.calls_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                      visible=False, margin_top=12,
                                      margin_start=16, margin_end=16)
        self.calls_list.add_css_class("boxed-list")
        box.append(self.calls_status)
        box.append(self.calls_list)
        return box

    def _on_call_event(self, _kdec, event, number, contact_name):
        self.call_log.insert(0, (event, number, contact_name, time.time()))
        self.calls_status.set_visible(False)
        self.calls_list.set_visible(True)
        row = Adw.ActionRow(
            title=contact_name or self.contacts.lookup(number) or number,
            subtitle=f"{event} · {number} · {time.strftime('%H:%M')}",
        )
        row.add_prefix(Gtk.Image.new_from_icon_name(
            "call-missed-symbolic" if event == "missedCall" else "call-start-symbolic"))
        self.calls_list.prepend(row)


def load_css():
    css = Gtk.CssProvider()
    css.load_from_string("""
        .bubble-out {
            background-color: @accent_bg_color;
            color: @accent_fg_color;
            border-radius: 14px;
            padding: 8px 12px;
        }
        .bubble-in {
            background-color: alpha(@card_fg_color, 0.08);
            border-radius: 14px;
            padding: 8px 12px;
        }
        .sidebar-pane { background-color: @sidebar_bg_color; }
        .attachment {
            padding: 0;
            border-radius: 14px;
        }
        .attachment label { padding: 8px 12px; }
        .success { color: @success_color; }
        .hidden-thread { opacity: 0.55; }
        .selection-check {
            background-color: alpha(@accent_bg_color, 0.85);
            color: @accent_fg_color;
            border-radius: 9999px;
            min-width: 38px;
            min-height: 38px;
        }
    """)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
