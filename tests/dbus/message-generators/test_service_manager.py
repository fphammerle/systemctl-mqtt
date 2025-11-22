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

import typing
import unittest.mock

import pytest
import jeepney.io.asyncio
import jeepney.low_level

import systemctl_mqtt

# pylint: disable=protected-access


class DBusErrorResponseMock(jeepney.wrappers.DBusErrorResponse):
    # pylint: disable=missing-class-docstring,super-init-not-called
    def __init__(self, name: str, data: typing.Any):
        self.name = name
        self.data = data


@pytest.mark.asyncio
async def test__get_unit_path() -> None:
    router_mock = unittest.mock.AsyncMock()
    reply_mock = unittest.mock.MagicMock()
    expected_path = "/org/freedesktop/systemd1/unit/ssh_2eservice"
    reply_mock.body = (expected_path,)
    router_mock.send_and_get_reply.return_value = reply_mock
    service_manager = jeepney.io.asyncio.Proxy(
        msggen=systemctl_mqtt._dbus.service_manager.ServiceManager(),
        router=router_mock,
    )
    assert (
        await systemctl_mqtt._get_unit_path(
            service_manager=service_manager, unit_name="ssh.service"
        )
        == expected_path
    )
    router_mock.send_and_get_reply.assert_awaited_once()
    (msg,), send_kwargs = router_mock.send_and_get_reply.await_args
    assert isinstance(msg, jeepney.low_level.Message)
    assert msg.header.fields == {
        jeepney.low_level.HeaderFields.path: "/org/freedesktop/systemd1",
        jeepney.low_level.HeaderFields.destination: "org.freedesktop.systemd1",
        jeepney.low_level.HeaderFields.interface: "org.freedesktop.systemd1.Manager",
        jeepney.low_level.HeaderFields.member: "GetUnit",
        jeepney.low_level.HeaderFields.signature: "s",
    }
    assert msg.body == ("ssh.service",)
    assert not send_kwargs


def test__get_unit_proxy():
    unit_proxy = unittest.mock.MagicMock()
    manager_proxy = unittest.mock.MagicMock()
    manager_proxy.LoadUnit.return_value = ("/unit/foo",)

    with unittest.mock.patch(
        "systemctl_mqtt._dbus.service_manager._get_connection", return_value=object()
    ), unittest.mock.patch(
        "jeepney.io.blocking.Proxy", side_effect=(manager_proxy, unit_proxy)
    ):
        assert (
            systemctl_mqtt._dbus.service_manager._get_unit_proxy("foo.service")
            is unit_proxy
        )

    manager_proxy.LoadUnit.assert_called_once_with(name="foo.service")


@pytest.mark.parametrize(
    ("function_name", "property_name", "propery_value", "return_value"),
    [
        ("is_isolate_unit_allowed", "AllowIsolate", True, True),
        ("is_isolate_unit_allowed", "AllowIsolate", False, False),
    ],
)
def test__unit_property(function_name, property_name, propery_value, return_value):
    mock_unit_proxy = unittest.mock.MagicMock()
    mock_unit_proxy.Get.return_value = ((None, propery_value),)
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.service_manager._get_unit_proxy",
        return_value=mock_unit_proxy,
    ):
        # call the wrapper function dynamically
        assert (
            getattr(systemctl_mqtt._dbus.service_manager, function_name)("foo.service")
            is return_value
        )
        mock_unit_proxy.Get.assert_called_once_with(property_name)


@pytest.mark.parametrize(
    "function_name",
    ["is_isolate_unit_allowed"],
)
def test__unit_property_with_exception_on_load_unit(function_name):
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.service_manager.ServiceManager.LoadUnit",
        side_effect=DBusErrorResponseMock("DBus error", ("mocked",)),
    ), unittest.mock.patch(
        "systemctl_mqtt._dbus.service_manager._LOGGER"
    ) as mock_logger:
        assert (
            getattr(systemctl_mqtt._dbus.service_manager, function_name)("foo.service")
            is False
        )
        mock_logger.error.assert_called_once_with(
            "Failed to load unit: %s because %s",
            "foo.service",
            "DBus error",
        )


@pytest.mark.parametrize(
    ("function_name", "property_name"),
    [("is_isolate_unit_allowed", "AllowIsolate")],
)
def test__unit_property_with_exception_on_get(function_name, property_name):
    mock_proxy = unittest.mock.MagicMock()
    mock_proxy.Get.side_effect = DBusErrorResponseMock("DBus error", ("mocked",))
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.service_manager._get_unit_proxy",
        return_value=mock_proxy,
    ), unittest.mock.patch(
        "systemctl_mqtt._dbus.service_manager._LOGGER"
    ) as mock_logger:
        assert (
            getattr(systemctl_mqtt._dbus.service_manager, function_name)("foo.service")
            is False
        )
        mock_logger.error.assert_called_once_with(
            f"Failed to get {property_name} property of unit %s because %s",
            "foo.service",
            "DBus error",
        )


@pytest.mark.parametrize(
    "action,method,mode",
    [
        ("start", "StartUnit", "replace"),
        ("stop", "StopUnit", "replace"),
        ("restart", "RestartUnit", "replace"),
        ("isolate", "StartUnit", "isolate"),
    ],
)
def test__unit_proxy(action, method, mode):
    mock_proxy = unittest.mock.MagicMock()
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.service_manager.get_service_manager_proxy",
        return_value=mock_proxy,
    ):
        # call the wrapper function dynamically
        getattr(systemctl_mqtt._dbus.service_manager, f"{action}_unit")("foo.service")
        getattr(mock_proxy, method).assert_called_once_with("foo.service", mode)


@pytest.mark.parametrize(
    "method",
    [
        "StartUnit",
        "StopUnit",
        "RestartUnit",
    ],
)
def test__unit_method_call(method):
    with unittest.mock.patch(
        "jeepney.new_method_call", return_value=unittest.mock.MagicMock()
    ) as mock_method_call:
        mgr = systemctl_mqtt._dbus.service_manager.ServiceManager()
        getattr(mgr, method)("foo.service", "replace")
        mock_method_call.assert_called_once_with(
            remote_obj=mgr,
            method=method,
            signature="ss",
            body=("foo.service", "replace"),
        )


@pytest.mark.parametrize(
    "action,method",
    [
        ("start", "StartUnit"),
        ("stop", "StopUnit"),
        ("restart", "RestartUnit"),
        ("isolate", "StartUnit"),
    ],
)
def test__unit_with_exception(action, method):
    mock_proxy = unittest.mock.MagicMock()
    getattr(mock_proxy, method).side_effect = DBusErrorResponseMock(
        "DBus error", ("mocked",)
    )

    with unittest.mock.patch(
        "systemctl_mqtt._dbus.service_manager.get_service_manager_proxy",
        return_value=mock_proxy,
    ), unittest.mock.patch(
        "systemctl_mqtt._dbus.service_manager._LOGGER"
    ) as mock_logger:
        getattr(systemctl_mqtt._dbus.service_manager, f"{action}_unit")(
            "example.service"
        )
        mock_logger.error.assert_called_once_with(
            f"Failed to {action} unit: %s because %s ",
            "example.service",
            "DBus error",
        )
