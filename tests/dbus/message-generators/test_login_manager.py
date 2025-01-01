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

import contextlib
import datetime
import typing
import unittest.mock

import pytest
from jeepney.low_level import HeaderFields, Message

import systemctl_mqtt._dbus.login_manager

# pylint: disable=protected-access


@contextlib.contextmanager
def mock_open_dbus_connection() -> typing.Iterator[unittest.mock.MagicMock]:
    with unittest.mock.patch("jeepney.io.blocking.open_dbus_connection") as mock:
        yield mock.return_value


@pytest.mark.parametrize(
    ("member", "signature", "kwargs", "body"),
    [
        ("ListInhibitors", None, {}, ()),
        ("LockSessions", None, {}, ()),
        ("CanPowerOff", None, {}, ()),
        (
            "ScheduleShutdown",
            "st",
            {
                "action": "poweroff",
                "time": datetime.datetime(
                    1970, 1, 1, 0, 0, tzinfo=datetime.timezone.utc
                ),
            },
            ("poweroff", 0),
        ),
        ("Suspend", "b", {"interactive": True}, (True,)),
        (
            "Inhibit",
            "ssss",
            {"what": "poweroff", "who": "me", "why": "fixing bugs", "mode": "block"},
            ("poweroff", "me", "fixing bugs", "block"),
        ),
    ],
)
def test_method(
    member: str,
    signature: typing.Optional[str],
    kwargs: typing.Dict[str, typing.Any],
    body: typing.Tuple[typing.Any],
) -> None:
    with mock_open_dbus_connection() as dbus_connection_mock:
        proxy = systemctl_mqtt._dbus.login_manager.get_login_manager_proxy()
    getattr(proxy, member)(**kwargs)
    dbus_connection_mock.send_and_get_reply.assert_called_once()
    message: Message = dbus_connection_mock.send_and_get_reply.call_args[0][0]
    if signature:
        assert message.header.fields.pop(HeaderFields.signature) == signature
    assert message.header.fields == {
        HeaderFields.path: "/org/freedesktop/login1",
        HeaderFields.destination: "org.freedesktop.login1",
        HeaderFields.interface: "org.freedesktop.login1.Manager",
        HeaderFields.member: member,
    }
    assert message.body == body


@pytest.mark.parametrize("property_name", ["HandlePowerKey", "Docked"])
def test_get(property_name: str) -> None:
    with mock_open_dbus_connection() as dbus_connection_mock:
        proxy = systemctl_mqtt._dbus.login_manager.get_login_manager_proxy()
    proxy.Get(property_name=property_name)
    dbus_connection_mock.send_and_get_reply.assert_called_once()
    message: Message = dbus_connection_mock.send_and_get_reply.call_args[0][0]
    assert message.header.fields == {
        HeaderFields.path: "/org/freedesktop/login1",
        HeaderFields.destination: "org.freedesktop.login1",
        HeaderFields.interface: "org.freedesktop.DBus.Properties",
        HeaderFields.member: "Get",
        HeaderFields.signature: "ss",
    }
    assert message.body == ("org.freedesktop.login1.Manager", property_name)
