#!/usr/bin/env python3
"""KDE Plasma 6.7 notification module for yt-dl."""

import os
import dbus
import logging

logger = logging.getLogger("yt-dl")

APP_NAME = "yt-dl"
APP_ICON = "applications-multimedia"

STATE_ICONS = {
    "queued": "appointment-soon",
    "downloading": "emblem-downloads",
    "done": "emblem-checked",
    "failed": "dialog-error",
    "cancelled": "dialog-close",
}

_retry_callback = None
_cancel_callback = None


def set_action_callbacks(retry_fn=None, cancel_fn=None):
    """Set callbacks for notification action buttons."""
    global _retry_callback, _cancel_callback
    _retry_callback = retry_fn
    _cancel_callback = cancel_fn


class NotificationManager:
    """KDE Plasma 6.7 compatible notification manager."""

    def __init__(self):
        self._active = {}
        self._popup_shown = {}
        self._bus = None
        self._iface = None
        self._init_dbus()

    def _init_dbus(self):
        try:
            self._bus = dbus.SessionBus()
            obj = self._bus.get_object(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications"
            )
            self._iface = dbus.Interface(obj, "org.freedesktop.Notifications")
            self._bus.add_signal_receiver(
                self._on_action,
                "ActionInvoked",
                "org.freedesktop.Notifications"
            )
            logger.info("D-Bus notifications initialized")
        except Exception as e:
            logger.warning(f"D-Bus init failed: {e}")
            self._iface = None

    def _on_action(self, notification_id, action_key):
        """Handle notification action clicks."""
        job_id = None
        for jid, nid in self._active.items():
            if nid == notification_id:
                job_id = jid
                break
        if not job_id:
            return

        logger.info(f"Action: {action_key} for job {job_id}")

        if action_key == "retry" and _retry_callback:
            _retry_callback(job_id)
        elif action_key == "cancel" and _cancel_callback:
            _cancel_callback(job_id)
        elif action_key == "open_folder":
            os.system('xdg-open "/mnt/storage/YouTube" &')
        elif action_key == "dismiss":
            self.close(job_id)

    def _make_hints(self, job_id, state, progress=None):
        hints = dbus.Dictionary({}, signature="sv")
        hints["desktop-entry"] = dbus.String("yt-dl")
        hints["category"] = dbus.String("transfer")
        hints["urgency"] = dbus.Byte(2 if state == "failed" else 1)
        hints["resident"] = dbus.Boolean(True)
        hints["x-kde-persistence"] = dbus.Boolean(True)
        if state == "downloading" and progress is not None:
            hints["value"] = dbus.Int32(int(progress))
        return hints

    def _make_actions(self, state):
        actions = dbus.Array([], signature="s")
        if state == "failed":
            actions.extend(["retry", "Retry", "dismiss", "Dismiss"])
        elif state == "done":
            actions.extend(["open_folder", "Open Folder", "dismiss", "Dismiss"])
        elif state == "downloading":
            actions.extend(["cancel", "Cancel", "open_folder", "Open Folder"])
        else:
            actions.extend(["dismiss", "Dismiss"])
        return actions

    def _notify(self, job_id, state, title, body, progress=None, force_popup=False):
        if not self._iface:
            return None
        try:
            replaces_id = dbus.UInt32(self._active.get(job_id, 0))
            icon = STATE_ICONS.get(state, APP_ICON)
            actions = self._make_actions(state)
            hints = self._make_hints(job_id, state, progress)
            timeout = dbus.Int32(0 if state == "failed" else 3000)

            nid = self._iface.Notify(
                APP_NAME, replaces_id, icon, f"yt-dl: {title}", body,
                actions, hints, timeout
            )
            self._active[job_id] = int(nid)
            return int(nid)
        except Exception as e:
            logger.warning(f"Notify error: {e}")
            return None

    def close(self, job_id):
        if job_id in self._active and self._iface:
            try:
                self._iface.CloseNotification(dbus.UInt32(self._active[job_id]))
                del self._active[job_id]
            except Exception:
                pass

    def show_queued(self, job_id, title, quality):
        self._notify(job_id, "queued", title or "YouTube Video",
                     f"Quality: {quality}\nQueued for download", force_popup=True)

    def update_downloading(self, job_id, title, quality, progress, speed, eta):
        body = f"Quality: {quality}\n{speed or '0 KiB/s'} | ETA: {eta or 'Unknown'}"
        if progress is not None:
            body += f"\nProgress: {progress:.1f}%"
        self._notify(job_id, "downloading", title or "YouTube Video", body, progress=progress)

    def show_done(self, job_id, title, quality, file_path):
        self._notify(job_id, "done", title or "YouTube Video",
                     f"Quality: {quality}\nSaved to: {file_path or 'Unknown'}", force_popup=True)

    def show_failed(self, job_id, title, quality, error):
        self._notify(job_id, "failed", title or "YouTube Video",
                     f"Error: {error or 'Unknown error'}", force_popup=True)

    def show_cancelled(self, job_id, title, quality):
        self._notify(job_id, "cancelled", title or "YouTube Video",
                     f"Quality: {quality}\nCancelled by user", force_popup=True)
