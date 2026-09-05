"""Main window — layout modeled on Microsoft Phone Link.

Left sidebar: device name, connection state, battery, phone notifications.
Main pane: Messages / Calls view switcher; Messages is a conversation
list plus a thread view with composer.
"""

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from .kdeconnect import MESSAGE_SENT


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
        self.kdec = kdec
        self.contacts = contacts
        self.current_thread = None
        self.call_log = []
        self._refresh_pending = False

        kdec.connect("device-state", lambda *a: self._refresh_device())
        kdec.connect("conversations-loaded", lambda *a: self._refresh_conversations())
        kdec.connect("message", self._on_message)
        kdec.connect("notifications-changed", lambda *a: self._refresh_notifications())
        kdec.connect("call-event", self._on_call_event)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        root.append(self._build_sidebar())
        root.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        root.append(self._build_main())

        toolbar = Adw.ToolbarView()
        toolbar.set_content(root)
        self.set_content(toolbar)

        self._refresh_device()
        self._refresh_notifications()
        if kdec.device_id:
            GLib.idle_add(lambda: kdec.load_conversations() or False)

    # -- sidebar ----------------------------------------------------------

    def _build_sidebar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=300)
        box.add_css_class("sidebar-pane")

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                       margin_top=18, margin_bottom=12, margin_start=18, margin_end=18)
        title_row = Gtk.Box(spacing=8)
        title_row.append(Gtk.Image.new_from_icon_name("phone-symbolic"))
        self.device_label = Gtk.Label(label="No device", xalign=0)
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
        name = self.kdec.device_name
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

    def _refresh_notifications(self):
        self.notif_list.remove_all()
        for n in self.kdec.active_notifications():
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
            outer.append(inner)
            if n.dismissable:
                x = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.START)
                x.add_css_class("flat")
                x.connect("clicked", self._on_dismiss_notification, n.nid)
                outer.append(x)
            row.set_child(outer)
            self.notif_list.append(row)

    def _on_dismiss_notification(self, _btn, nid):
        self.kdec.dismiss_notification(nid)
        GLib.timeout_add(300, lambda: self._refresh_notifications() or False)

    def _on_clear_notifications(self, _btn):
        for n in self.kdec.active_notifications():
            if n.dismissable:
                self.kdec.dismiss_notification(n.nid)
        GLib.timeout_add(400, lambda: self._refresh_notifications() or False)

    # -- main pane --------------------------------------------------------

    def _build_main(self):
        self.stack = Adw.ViewStack(hexpand=True)
        self.stack.add_titled_with_icon(
            self._build_messages(), "messages", "Messages", "chat-message-new-symbolic")
        self.stack.add_titled_with_icon(
            self._build_calls(), "calls", "Calls", "call-start-symbolic")

        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header = Adw.HeaderBar(title_widget=switcher)

        view = Adw.ToolbarView(hexpand=True)
        view.add_top_bar(header)
        view.set_content(self.stack)
        return view

    # -- messages ---------------------------------------------------------

    def _build_messages(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=330)
        head = Gtk.Box(margin_top=10, margin_bottom=6, margin_start=14, margin_end=10)
        lbl = Gtk.Label(label="Messages", xalign=0, hexpand=True)
        lbl.add_css_class("title-3")
        head.append(lbl)
        compose = Gtk.Button(icon_name="document-edit-symbolic",
                             tooltip_text="New message")
        compose.add_css_class("circular")
        compose.add_css_class("suggested-action")
        compose.connect("clicked", self._on_compose)
        head.append(compose)
        left.append(head)

        self.conv_list = Gtk.ListBox()
        self.conv_list.add_css_class("navigation-sidebar")
        self.conv_list.connect("row-selected", self._on_conversation_selected)
        sc = Gtk.ScrolledWindow(vexpand=True, child=self.conv_list)
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        left.append(sc)
        box.append(left)
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.thread_header = Gtk.Box(spacing=10, margin_top=10, margin_bottom=10,
                                     margin_start=16, margin_end=16)
        self.thread_avatar = Adw.Avatar(size=36, show_initials=True)
        self.thread_header.append(self.thread_avatar)
        tv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.thread_name = Gtk.Label(label="Select a conversation", xalign=0)
        self.thread_name.add_css_class("title-4")
        tv.append(self.thread_name)
        self.thread_sub = Gtk.Label(label="", xalign=0)
        self.thread_sub.add_css_class("dim-label")
        self.thread_sub.add_css_class("caption")
        tv.append(self.thread_sub)
        self.thread_header.append(tv)
        right.append(self.thread_header)
        right.append(Gtk.Separator())

        self.bubble_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                                  margin_top=12, margin_bottom=12,
                                  margin_start=16, margin_end=16, valign=Gtk.Align.END)
        self.thread_scroll = Gtk.ScrolledWindow(vexpand=True, child=self.bubble_box)
        self.thread_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        right.append(self.thread_scroll)

        composer = Gtk.Box(spacing=8, margin_top=8, margin_bottom=12,
                           margin_start=16, margin_end=16)
        self.entry = Gtk.Entry(hexpand=True, placeholder_text="Send a message")
        self.entry.connect("activate", self._on_send)
        composer.append(self.entry)
        send = Gtk.Button(icon_name="document-send-symbolic", tooltip_text="Send")
        send.add_css_class("suggested-action")
        send.add_css_class("circular")
        send.connect("clicked", self._on_send)
        composer.append(send)
        right.append(composer)
        box.append(right)
        return box

    def _on_message(self, _kdec, msg):
        if not self._refresh_pending:
            self._refresh_pending = True
            GLib.timeout_add(200, self._debounced_refresh)
        if msg.thread_id == self.current_thread:
            self._render_thread()

    def _debounced_refresh(self):
        self._refresh_pending = False
        self._refresh_conversations()
        return False

    def _refresh_conversations(self):
        selected = self.current_thread
        self.conv_list.remove_all()
        convs = sorted(
            (c for c in self.kdec.conversations.values() if c.last_message),
            key=lambda c: c.last_message.date, reverse=True,
        )[:150]
        for conv in convs:
            last = conv.last_message
            row = Gtk.ListBoxRow()
            row.thread_id = conv.thread_id
            outer = Gtk.Box(spacing=10, margin_top=8, margin_bottom=8,
                            margin_start=8, margin_end=8)
            avatar = Adw.Avatar(size=38, show_initials=True,
                                text=self.contacts.display(conv.addresses))
            outer.append(avatar)
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
            snippet = " ".join(
                (last.body or ("[attachment]" if last.has_attachments else "")).split()
            )
            sn = Gtk.Label(label=snippet, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                           single_line_mode=True)
            sn.add_css_class("dim-label")
            inner.append(sn)
            outer.append(inner)
            row.set_child(outer)
            self.conv_list.append(row)
            if conv.thread_id == selected:
                self.conv_list.select_row(row)

    def _on_conversation_selected(self, _list, row):
        if row is None:
            return
        self.current_thread = row.thread_id
        conv = self.kdec.conversations[row.thread_id]
        display = self.contacts.display(conv.addresses)
        self.thread_name.set_label(display)
        self.thread_avatar.set_text(display)
        raw = ", ".join(conv.addresses)
        self.thread_sub.set_label(raw if raw != display else "")
        self.kdec.request_conversation(row.thread_id)
        self._render_thread()

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
            wrap = Gtk.Box(halign=Gtk.Align.END if sent else Gtk.Align.START)
            body = msg.body or ("[attachment]" if msg.has_attachments else "")
            lbl = Gtk.Label(label=body, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                            xalign=0, selectable=True, max_width_chars=46,
                            tooltip_text=_fmt_time(msg.date))
            lbl.add_css_class("bubble-out" if sent else "bubble-in")
            wrap.append(lbl)
            self.bubble_box.append(wrap)
        GLib.idle_add(self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        adj = self.thread_scroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def _on_send(self, _widget):
        text = self.entry.get_text().strip()
        conv = self.kdec.conversations.get(self.current_thread)
        if not text or not conv:
            return
        self.kdec.reply_to_conversation(conv.thread_id, text)
        self.entry.set_text("")
        GLib.timeout_add(1500, lambda: self.kdec.request_conversation(self.current_thread) or False)

    def _on_compose(self, _btn):
        dialog = Adw.AlertDialog(heading="New message",
                                 body="Send an SMS to a phone number")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        number = Gtk.Entry(placeholder_text="Phone number")
        text = Gtk.Entry(placeholder_text="Message")
        box.append(number)
        box.append(text)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("send", "Send")
        dialog.set_response_appearance("send", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_compose_response, number, text)
        dialog.present(self)

    def _on_compose_response(self, _dialog, response, number, text):
        if response != "send":
            return
        num, body = number.get_text().strip(), text.get_text().strip()
        if num and body:
            self.kdec.send_new_sms([num], body)

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
        .success { color: @success_color; }
    """)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
