# systemctl-mqtt - MQTT client triggering & reporting shutdown on systemd-based systems
#
# Copyright (C) 2024 Fabian Peter Hammerle <fabian@hammerle.me>
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

import logging
import jeepney
import systemctl_mqtt._dbus

_LOGGER = logging.getLogger(__name__)


class ServiceManager(jeepney.MessageGenerator):
    """
    https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.systemd1.html
    """

    # pylint: disable=too-few-public-methods

    interface = "org.freedesktop.systemd1.Manager"

    def __init__(self):
        super().__init__(
            object_path="/org/freedesktop/systemd1", bus_name="org.freedesktop.systemd1"
        )

    # pylint: disable=invalid-name

    def GetUnit(self, name: str) -> jeepney.low_level.Message:
        return jeepney.new_method_call(
            remote_obj=self, method="GetUnit", signature="s", body=(name,)
        )

    def StartUnit(self, name: str, mode: str) -> jeepney.low_level.Message:
        return jeepney.new_method_call(
            remote_obj=self,
            method="StartUnit",
            signature="ss",
            body=(
                name,
                mode,
            ),
        )

    def StopUnit(self, name: str, mode: str) -> jeepney.low_level.Message:
        return jeepney.new_method_call(
            remote_obj=self,
            method="StopUnit",
            signature="ss",
            body=(
                name,
                mode,
            ),
        )

    def RestartUnit(self, name: str, mode: str) -> jeepney.low_level.Message:
        return jeepney.new_method_call(
            remote_obj=self,
            method="RestartUnit",
            signature="ss",
            body=(
                name,
                mode,
            ),
        )


class Unit(systemctl_mqtt._dbus.Properties):  # pylint: disable=protected-access
    """
    https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.systemd1.html#Unit%20Objects
    """

    # pylint: disable=too-few-public-methods

    interface = "org.freedesktop.systemd1.Unit"

    def __init__(self, *, object_path: str):
        super().__init__(object_path=object_path, bus_name="org.freedesktop.systemd1")

    # pylint: disable=invalid-name


def start_unit(unit_name: str):
    proxy = get_service_manager_proxy()
    try:
        proxy.StartUnit(unit_name, "replace")
        _LOGGER.debug("Starting unit: %s", unit_name)
    # pylint: disable=broad-exception-caught
    except jeepney.wrappers.DBusErrorResponse as exc:
        _LOGGER.error("Failed to start unit: %s because %s ", unit_name, exc.name)


def stop_unit(unit_name: str):
    proxy = get_service_manager_proxy()
    try:
        proxy.StopUnit(unit_name, "replace")
        _LOGGER.debug("Stopping unit: %s", unit_name)
    # pylint: disable=broad-exception-caught
    except jeepney.wrappers.DBusErrorResponse as exc:
        _LOGGER.error("Failed to stop unit: %s because %s ", unit_name, exc.name)


def restart_unit(unit_name: str):
    proxy = get_service_manager_proxy()
    try:
        proxy.RestartUnit(unit_name, "replace")
        _LOGGER.debug("Restarting unit: %s", unit_name)
    # pylint: disable=broad-exception-caught
    except jeepney.wrappers.DBusErrorResponse as exc:
        _LOGGER.error("Failed to restart unit: %s because %s ", unit_name, exc.name)


def get_service_manager_proxy() -> jeepney.io.blocking.Proxy:
    # https://jeepney.readthedocs.io/en/latest/integrate.html
    # https://gitlab.com/takluyver/jeepney/-/blob/master/examples/aio_notify.py
    return jeepney.io.blocking.Proxy(
        msggen=ServiceManager(),
        connection=jeepney.io.blocking.open_dbus_connection(
            bus="SYSTEM",
        ),
    )
