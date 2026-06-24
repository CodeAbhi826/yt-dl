#!/usr/bin/env python3
"""KDE Plasma notification module for yt-dl (currently disabled)."""

import os
import time
import logging

try:
    import dbus as _dbus_module
    _dbus_import_ok = True
except ImportError:
    _dbus_module = None
    _dbus_import_ok = False

from models import load_config

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
_extension_last_seen = 0.0
_dbus_available = False


def set_action_callbacks(retry_fn=None, cancel_fn=None):
    global _retry_callback, _cancel_callback
    _retry_callback = retry_fn
    _cancel_callback = cancel_fn


def set_extension_heartbeat():
    global _extension_last_seen
    _extension_last_seen = time.time()


def clear_extension_heartbeat():
    global _extension_last_seen
    _extension_last_seen = 0.0


def is_extension_alive():
    return time.time() - _extension_last_seen < 120


def dbus_available():
    return _dbus_available


class NotificationManager:
    def __init__(self):
        self._active = {}
        self._popup_shown = {}
        self._bus = None
        self._iface = None
        global _dbus_available
        _dbus_available = False

    def _init_dbus(self):
        if not _dbus_import_ok:
            logger.info("dbus-python not installed — D-Bus disabled")
            self._iface = None
            return
        try:
            self._bus = _dbus_module.SessionBus()
            obj = self._bus.get_object(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications"
            )
            self._iface = _dbus_module.Interface(obj, "org.freedesktop.Notifications")
            try:
                self._bus.add_signal_receiver(
                    self._on_action,
                    "ActionInvoked",
                    "org.freedesktop.Notifications"
                )
            except Exception as e:
                logger.warning(f"D-Bus action signals not available: {e}")
            _dbus_available = True
            logger.info("D-Bus notifications initialized")
        except Exception as e:
            logger.warning(f"D-Bus unavailable: {e}")
            self._iface = None

    def _on_action(self, notification_id, action_key):
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
            folder = load_config().get("download_dir", "/mnt/storage/YouTube")
            os.system(f'xdg-open "{folder}" &')
        elif action_key == "dismiss":
            self.close(job_id)

    def _make_hints(self, job_id, state, progress=None):
        hints = _dbus_module.Dictionary({}, signature="sv")
        hints["desktop-entry"] = _dbus_module.String("yt-dl")
        hints["category"] = _dbus_module.String("transfer")
        hints["urgency"] = _dbus_module.Byte(2 if state == "failed" else 1)
        if state in ("downloading", "queued"):
            hints["resident"] = _dbus_module.Boolean(True)
            hints["x-kde-persistence"] = _dbus_module.Boolean(True)
        if state == "downloading" and progress is not None:
            hints["value"] = _dbus_module.Int32(int(progress))
        return hints

    def _make_actions(self, state):
        actions = _dbus_module.Array([], signature="s")
        if state == "failed":
            actions.extend(["retry", "Retry", "dismiss", "Dismiss"])
        elif state == "done":
            actions.extend(["open_folder", "Open Folder", "dismiss", "Dismiss"])
        elif state == "downloading":
            actions.extend(["cancel", "Cancel", "open_folder", "Open Folder"])
        else:
            actions.extend(["dismiss", "Dismiss"])
        return actions

    def _notify(self, job_id, state, title, body, progress=None, timeout=3000):
        if not self._iface:
            return None
        try:
            replaces_id = _dbus_module.UInt32(self._active.get(job_id, 0))
            icon = STATE_ICONS.get(state, APP_ICON)
            actions = self._make_actions(state)
            hints = self._make_hints(job_id, state, progress)

            nid = self._iface.Notify(
                APP_NAME, replaces_id, icon, f"yt-dl: {title}", body,
                actions, hints, _dbus_module.Int32(timeout)
            )
            self._active[job_id] = int(nid)
            return int(nid)
        except Exception as e:
            logger.warning(f"Notify error: {e}")
            return None

    def close(self, job_id):
        if job_id in self._active and self._iface:
            try:
                self._iface.CloseNotification(_dbus_module.UInt32(self._active[job_id]))
                del self._active[job_id]
            except Exception:
                pass

    def show_queued(self, job_id, title, quality):
        if is_extension_alive():
            return

    def update_downloading(self, job_id, title, quality, progress, speed, eta):
        if is_extension_alive():
            return

    def show_done(self, job_id, title, quality, file_path):
        if is_extension_alive():
            return

    def show_failed(self, job_id, title, quality, error):
        if is_extension_alive():
            return

    def show_cancelled(self, job_id, title, quality):
        if is_extension_alive():
            return
