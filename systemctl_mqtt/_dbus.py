# systemctl-mqtt - MQTT client triggering & reporting shutdown on systemd-based systems
#
# Copyright (C) 2020 Fabian Peter Hammerle <fabian@hammerle.me>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import datetime
import logging

import dbus

_LOGGER = logging.getLogger(__name__)


def get_login_manager() -> dbus.proxies.Interface:
    # https://dbus.freedesktop.org/doc/dbus-python/tutorial.html
    bus = dbus.SystemBus()
    proxy = bus.get_object(
        bus_name="org.freedesktop.login1", object_path="/org/freedesktop/login1"
    )  # type: dbus.proxies.ProxyObject
    # https://freedesktop.org/wiki/Software/systemd/logind/
    return dbus.Interface(object=proxy, dbus_interface="org.freedesktop.login1.Manager")


def _log_shutdown_inhibitors(login_manager: dbus.proxies.Interface) -> None:
    if _LOGGER.getEffectiveLevel() > logging.DEBUG:
        return
    found_inhibitor = False
    try:
        # https://www.freedesktop.org/wiki/Software/systemd/inhibit/
        for what, who, why, mode, uid, pid in login_manager.ListInhibitors():
            if "shutdown" in what:
                found_inhibitor = True
                _LOGGER.debug(
                    "detected shutdown inhibitor %s (pid=%u, uid=%u, mode=%s): %s",
                    who,
                    pid,
                    uid,
                    mode,
                    why,
                )
    except dbus.DBusException as exc:
        _LOGGER.warning(
            "failed to fetch shutdown inhibitors: %s", exc.get_dbus_message()
        )
        return
    if not found_inhibitor:
        _LOGGER.debug("no shutdown inhibitor locks found")


def schedule_shutdown(*, action: str, delay: datetime.timedelta) -> None:
    # https://github.com/systemd/systemd/blob/v237/src/systemctl/systemctl.c#L8553
    assert action in ["poweroff", "reboot"], action
    shutdown_datetime = datetime.datetime.now() + delay
    # datetime.datetime.isoformat(timespec=) not available in python3.5
    # https://github.com/python/cpython/blob/v3.5.9/Lib/datetime.py#L1552
    _LOGGER.info(
        "scheduling %s for %s", action, shutdown_datetime.strftime("%Y-%m-%d %H:%M:%S")
    )
    # https://dbus.freedesktop.org/doc/dbus-python/tutorial.html?highlight=signature#basic-types
    shutdown_epoch_usec = dbus.UInt64(shutdown_datetime.timestamp() * 10**6)
    login_manager = get_login_manager()
    try:
        # $ gdbus introspect --system --dest org.freedesktop.login1 \
        #       --object-path /org/freedesktop/login1 | grep -A 1 ScheduleShutdown
        # ScheduleShutdown(in  s arg_0,
        #                  in  t arg_1);
        # $ gdbus call --system --dest org.freedesktop.login1 \
        #       --object-path /org/freedesktop/login1 \
        #       --method org.freedesktop.login1.Manager.ScheduleShutdown \
        #       poweroff "$(date --date=10min +%s)000000"
        # $ dbus-send --type=method_call --print-reply --system --dest=org.freedesktop.login1 \
        #       /org/freedesktop/login1 \
        #       org.freedesktop.login1.Manager.ScheduleShutdown \
        #       string:poweroff "uint64:$(date --date=10min +%s)000000"
        login_manager.ScheduleShutdown(action, shutdown_epoch_usec)
    except dbus.DBusException as exc:
        exc_msg = exc.get_dbus_message()
        if "authentication required" in exc_msg.lower():
            _LOGGER.error(
                "failed to schedule %s: unauthorized; missing polkit authorization rules?",
                action,
            )
        else:
            _LOGGER.error("failed to schedule %s: %s", action, exc_msg)
    _log_shutdown_inhibitors(login_manager)


def lock_all_sessions() -> None:
    """
    $ loginctl lock-sessions
    """
    _LOGGER.info("instruct all sessions to activate screen locks")
    get_login_manager().LockSessions()
