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
import getpass
import json
import logging
import typing

import jeepney
import jeepney.io.blocking

import systemctl_mqtt._dbus

_LOGGER = logging.getLogger(__name__)

_LOGIN_MANAGER_OBJECT_PATH = "/org/freedesktop/login1"
_LOGIN_MANAGER_INTERFACE = "org.freedesktop.login1.Manager"


def _get_username() -> typing.Optional[str]:
    try:
        return getpass.getuser()
    except OSError:
        # > Traceback (most recent call last):
        # >   File "/usr/local/lib/python3.13/getpass.py", line 173, in getuser
        # >     return pwd.getpwuid(os.getuid())[0]
        # >            ~~~~~~~~~~~~^^^^^^^^^^^^^
        # > KeyError: 'getpwuid(): uid not found: 100'
        #
        # > The above exception was the direct cause of the following exception:
        # > …
        # > OSError: No username set in the environment
        return None


def _log_interactive_authorization_required(
    *, action_label: str, action_id: str
) -> None:
    _LOGGER.error(
        """failed to %s: interactive authorization required

create %s and insert the following rule:
polkit.addRule(function(action, subject) {
    if(action.id === %s && subject.user === %s) {
        return polkit.Result.YES;
    }
});
""",
        action_label,
        "/etc/polkit-1/rules.d/50-systemctl-mqtt.rules",
        json.dumps(action_id),
        json.dumps(_get_username() or "USERNAME"),
    )


def get_login_manager_signal_match_rule(member: str) -> jeepney.MatchRule:
    return jeepney.MatchRule(
        type="signal",
        interface=_LOGIN_MANAGER_INTERFACE,
        member=member,
        path=_LOGIN_MANAGER_OBJECT_PATH,
    )


class LoginManager(systemctl_mqtt._dbus.Properties):  # pylint: disable=protected-access
    """
    https://freedesktop.org/wiki/Software/systemd/logind/

    $ python3 -m jeepney.bindgen \
        --bus unix:path=/var/run/dbus/system_bus_socket \
        --name org.freedesktop.login1 --path /org/freedesktop/login1
    """

    interface = _LOGIN_MANAGER_INTERFACE

    def __init__(self):
        super().__init__(
            object_path=_LOGIN_MANAGER_OBJECT_PATH, bus_name="org.freedesktop.login1"
        )

    # pylint: disable=invalid-name; inherited method names from Manager object

    def ListInhibitors(self) -> jeepney.low_level.Message:
        return jeepney.new_method_call(remote_obj=self, method="ListInhibitors")

    def LockSessions(self) -> jeepney.low_level.Message:
        return jeepney.new_method_call(remote_obj=self, method="LockSessions")

    def CanPowerOff(self) -> jeepney.low_level.Message:
        return jeepney.new_method_call(remote_obj=self, method="CanPowerOff")

    def ScheduleShutdown(
        self, *, action: str, time: datetime.datetime
    ) -> jeepney.low_level.Message:
        return jeepney.new_method_call(
            remote_obj=self,
            method="ScheduleShutdown",
            signature="st",
            body=(action, int(time.timestamp() * 1e6)),  # (type, usec)
        )

    def Suspend(self, *, interactive: bool) -> jeepney.low_level.Message:
        return jeepney.new_method_call(
            remote_obj=self, method="Suspend", signature="b", body=(interactive,)
        )

    def Inhibit(
        self, *, what: str, who: str, why: str, mode: str
    ) -> jeepney.low_level.Message:
        return jeepney.new_method_call(
            remote_obj=self,
            method="Inhibit",
            signature="ssss",
            body=(what, who, why, mode),
        )


def get_login_manager_proxy() -> jeepney.io.blocking.Proxy:
    # https://jeepney.readthedocs.io/en/latest/integrate.html
    # https://gitlab.com/takluyver/jeepney/-/blob/master/examples/aio_notify.py
    return jeepney.io.blocking.Proxy(
        msggen=LoginManager(),
        connection=jeepney.io.blocking.open_dbus_connection(
            bus="SYSTEM",
            # > dbus-broker[…]: Peer :1.… is being disconnected as it does not
            # . support receiving file descriptors it requested.
            enable_fds=True,
        ),
    )


def _log_shutdown_inhibitors(login_manager_proxy: jeepney.io.blocking.Proxy) -> None:
    if _LOGGER.getEffectiveLevel() > logging.DEBUG:
        return
    found_inhibitor = False
    try:
        # https://www.freedesktop.org/wiki/Software/systemd/inhibit/
        (inhibitors,) = login_manager_proxy.ListInhibitors()
        for what, who, why, mode, uid, pid in inhibitors:
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
    except jeepney.wrappers.DBusErrorResponse as exc:
        _LOGGER.warning("failed to fetch shutdown inhibitors: %s", exc)
        return
    if not found_inhibitor:
        _LOGGER.debug("no shutdown inhibitor locks found")


def schedule_shutdown(*, action: str, delay: datetime.timedelta) -> None:
    # https://github.com/systemd/systemd/blob/v237/src/systemctl/systemctl.c#L8553
    assert action in ["poweroff", "reboot"], action
    time = datetime.datetime.now() + delay
    # datetime.datetime.isoformat(timespec=) not available in python3.5
    # https://github.com/python/cpython/blob/v3.5.9/Lib/datetime.py#L1552
    _LOGGER.info("scheduling %s for %s", action, time.strftime("%Y-%m-%d %H:%M:%S"))
    login_manager = get_login_manager_proxy()
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
        login_manager.ScheduleShutdown(action=action, time=time)
    except jeepney.wrappers.DBusErrorResponse as exc:
        if exc.name == "org.freedesktop.DBus.Error.InteractiveAuthorizationRequired":
            _log_interactive_authorization_required(
                action_label="schedule " + action,
                action_id="org.freedesktop.login1."
                + {"poweroff": "power-off"}.get(action, action),
            )
        else:
            _LOGGER.error("failed to schedule %s: %s", action, exc)
    _log_shutdown_inhibitors(login_manager)


def suspend() -> None:
    _LOGGER.info("suspending system")
    get_login_manager_proxy().Suspend(interactive=False)


def lock_all_sessions() -> None:
    """
    $ loginctl lock-sessions
    """
    _LOGGER.info("instruct all sessions to activate screen locks")
    login_manager = get_login_manager_proxy()
    try:
        login_manager.LockSessions()
    except jeepney.wrappers.DBusErrorResponse as exc:
        if exc.name == "org.freedesktop.DBus.Error.InteractiveAuthorizationRequired":
            _log_interactive_authorization_required(
                action_label="lock all sessions",
                action_id="org.freedesktop.login1.lock-sessions",
            )
        else:
            _LOGGER.error("failed to lock all sessions: %s", exc)
