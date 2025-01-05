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

# Gosteli
# This file allow for a MVP of --control-system-unit [unit_name]

import jeepney
import jeepney.io.blocking

import logging
import systemctl_mqtt._dbus

_LOGGER = logging.getLogger(__name__)

class SystemdManager(systemctl_mqtt._dbus.Properties):
    """
    https://www.freedesktop.org/wiki/Software/systemd/dbus/  
    """

    # pylint: disable=too-few-public-methods

    interface = "org.freedesktop.systemd1.Manager"

    def __init__(self):
        super().__init__(
            object_path="/org/freedesktop/systemd1", bus_name="org.freedesktop.systemd1"
        )

    # pylint: disable=invalid-name

    def RestartUnit(self, name: str, mode: str) -> jeepney.low_level.Message:
        return jeepney.new_method_call(
            remote_obj=self, method="RestartUnit", signature="ss", body=(name,mode)
        )

def restart_unit(unit_name: str):
    proxy = get_systemd_manager_proxy()
    try:
        reply = proxy.RestartUnit(unit_name, "replace")
        _LOGGER.info(f"called successfully: {reply}")
    except Exception as e:
        _LOGGER.error(f"Failed to call: {e}")

def get_systemd_manager_proxy() -> jeepney.io.blocking.Proxy:
    # https://jeepney.readthedocs.io/en/latest/integrate.html
    # https://gitlab.com/takluyver/jeepney/-/blob/master/examples/aio_notify.py
    return jeepney.io.blocking.Proxy(
        msggen=SystemdManager(),
        connection=jeepney.io.blocking.open_dbus_connection(
            bus="SYSTEM",
            # > dbus-broker[…]: Peer :1.… is being disconnected as it does not
            # . support receiving file descriptors it requested.
            enable_fds=True,
        ),
    )