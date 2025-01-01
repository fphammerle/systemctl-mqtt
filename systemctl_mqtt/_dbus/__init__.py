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

import abc

import jeepney


class Properties(jeepney.MessageGenerator):
    """
    https://dbus.freedesktop.org/doc/dbus-specification.html#standard-interfaces-properties
    """

    # pylint: disable=too-few-public-methods

    interface = "org.freedesktop.DBus.Properties"  # overwritten

    # pylint: disable=invalid-name

    def Get(self, property_name: str) -> jeepney.low_level.Message:
        return jeepney.new_method_call(
            remote_obj=jeepney.DBusAddress(
                object_path=self.object_path,
                bus_name=self.bus_name,
                interface="org.freedesktop.DBus.Properties",
            ),
            method="Get",
            signature="ss",
            body=(self.interface, property_name),
        )
