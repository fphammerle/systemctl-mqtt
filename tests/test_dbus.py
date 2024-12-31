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

import asyncio
import datetime
import logging
import typing
import unittest.mock

import jeepney
import jeepney.low_level
import jeepney.wrappers
import pytest

import systemctl_mqtt._dbus

# pylint: disable=protected-access


def test_get_login_manager_proxy():
    login_manager = systemctl_mqtt._dbus.get_login_manager_proxy()
    assert isinstance(login_manager, jeepney.io.blocking.Proxy)
    assert login_manager._msggen.interface == "org.freedesktop.login1.Manager"
    # https://freedesktop.org/wiki/Software/systemd/logind/
    assert login_manager.CanPowerOff() in {("yes",), ("challenge",)}


def test__log_shutdown_inhibitors_some(caplog):
    login_manager = unittest.mock.MagicMock()
    login_manager.ListInhibitors.return_value = (
        [
            (
                "shutdown:sleep",
                "Developer",
                "Haven't pushed my commits yet",
                "delay",
                1000,
                1234,
            ),
            ("shutdown", "Editor", "", "Unsafed files open", 0, 42),
        ],
    )
    with caplog.at_level(logging.DEBUG):
        systemctl_mqtt._dbus._log_shutdown_inhibitors(login_manager)
    assert len(caplog.records) == 2
    assert caplog.records[0].levelno == logging.DEBUG
    assert (
        caplog.records[0].message
        == "detected shutdown inhibitor Developer (pid=1234, uid=1000, mode=delay): "
        + "Haven't pushed my commits yet"
    )


def test__log_shutdown_inhibitors_none(caplog):
    login_manager = unittest.mock.MagicMock()
    login_manager.ListInhibitors.return_value = ([],)
    with caplog.at_level(logging.DEBUG):
        systemctl_mqtt._dbus._log_shutdown_inhibitors(login_manager)
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.DEBUG
    assert caplog.records[0].message == "no shutdown inhibitor locks found"


def test__log_shutdown_inhibitors_fail(caplog):
    login_manager = unittest.mock.MagicMock()
    login_manager.ListInhibitors.side_effect = DBusErrorResponseMock("error", "mocked")
    with caplog.at_level(logging.DEBUG):
        systemctl_mqtt._dbus._log_shutdown_inhibitors(login_manager)
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert (
        caplog.records[0].message
        == "failed to fetch shutdown inhibitors: [error] mocked"
    )


@pytest.mark.parametrize("action", ["poweroff", "reboot"])
@pytest.mark.parametrize("delay", [datetime.timedelta(0), datetime.timedelta(hours=1)])
def test__schedule_shutdown(action, delay):
    login_manager_mock = unittest.mock.MagicMock()
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager_proxy", return_value=login_manager_mock
    ):
        login_manager_mock.ListInhibitors.return_value = ([],)
        systemctl_mqtt._dbus.schedule_shutdown(action=action, delay=delay)
    login_manager_mock.ScheduleShutdown.assert_called_once()
    schedule_args, schedule_kwargs = login_manager_mock.ScheduleShutdown.call_args
    assert not schedule_args
    assert schedule_kwargs.pop("action") == action
    actual_delay = schedule_kwargs.pop("time") - datetime.datetime.now()
    assert actual_delay.total_seconds() == pytest.approx(delay.total_seconds(), abs=0.1)
    assert not schedule_kwargs


class DBusErrorResponseMock(jeepney.wrappers.DBusErrorResponse):
    # pylint: disable=missing-class-docstring,super-init-not-called
    def __init__(self, name: str, data: typing.Any):
        self.name = name
        self.data = data


@pytest.mark.parametrize("action", ["poweroff"])
@pytest.mark.parametrize(
    ("error_name", "error_message", "log_message"),
    [
        (
            "test error",
            "test message",
            "[test error] ('test message',)",
        ),
        (
            "org.freedesktop.DBus.Error.InteractiveAuthorizationRequired",
            "Interactive authentication required.",
            "unauthorized; missing polkit authorization rules?",
        ),
    ],
)
def test__schedule_shutdown_fail(
    caplog, action, error_name, error_message, log_message
):
    login_manager_mock = unittest.mock.MagicMock()
    login_manager_mock.ScheduleShutdown.side_effect = DBusErrorResponseMock(
        name=error_name,
        data=(error_message,),
    )
    login_manager_mock.ListInhibitors.return_value = ([],)
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager_proxy", return_value=login_manager_mock
    ), caplog.at_level(logging.DEBUG):
        systemctl_mqtt._dbus.schedule_shutdown(
            action=action, delay=datetime.timedelta(seconds=21)
        )
    login_manager_mock.ScheduleShutdown.assert_called_once()
    assert len(caplog.records) == 3
    assert caplog.records[0].levelno == logging.INFO
    assert caplog.records[0].message.startswith(f"scheduling {action} for ")
    assert caplog.records[1].levelno == logging.ERROR
    assert caplog.records[1].message == f"failed to schedule {action}: {log_message}"
    assert "inhibitor" in caplog.records[2].message


def test_suspend(caplog):
    login_manager_mock = unittest.mock.MagicMock()
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager_proxy", return_value=login_manager_mock
    ), caplog.at_level(logging.INFO):
        systemctl_mqtt._dbus.suspend()
    login_manager_mock.Suspend.assert_called_once_with(interactive=False)
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.INFO
    assert caplog.records[0].message == "suspending system"


def test_lock_all_sessions(caplog):
    login_manager_mock = unittest.mock.MagicMock()
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager_proxy", return_value=login_manager_mock
    ), caplog.at_level(logging.INFO):
        systemctl_mqtt._dbus.lock_all_sessions()
    login_manager_mock.LockSessions.assert_called_once_with()
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.INFO
    assert caplog.records[0].message == "instruct all sessions to activate screen locks"


@pytest.mark.asyncio
async def test__dbus_signal_loop():
    # pylint: disable=too-many-locals,too-many-arguments
    state_mock = unittest.mock.AsyncMock()
    with unittest.mock.patch(
        "jeepney.io.asyncio.open_dbus_router",
    ) as open_dbus_router_mock:
        async with open_dbus_router_mock() as dbus_router_mock:
            pass
        add_match_reply = unittest.mock.Mock()
        add_match_reply.body = ()
        dbus_router_mock.send_and_get_reply.return_value = add_match_reply
        msg_queue = asyncio.Queue()
        await msg_queue.put(jeepney.low_level.Message(header=None, body=(False,)))
        await msg_queue.put(jeepney.low_level.Message(header=None, body=(True,)))
        await msg_queue.put(jeepney.low_level.Message(header=None, body=(False,)))
        dbus_router_mock.filter = unittest.mock.MagicMock()
        dbus_router_mock.filter.return_value.__enter__.return_value = msg_queue
        # asyncio.TaskGroup added in python3.11
        loop_task = asyncio.create_task(
            systemctl_mqtt._dbus_signal_loop(
                state=state_mock, mqtt_client=unittest.mock.MagicMock()
            )
        )

        async def _abort_after_msg_queue():
            await msg_queue.join()
            loop_task.cancel()

        with pytest.raises(asyncio.exceptions.CancelledError):
            await asyncio.gather(*(loop_task, _abort_after_msg_queue()))
    assert unittest.mock.call(bus="SYSTEM") in open_dbus_router_mock.call_args_list
    dbus_router_mock.filter.assert_called_once()
    (filter_match_rule,) = dbus_router_mock.filter.call_args[0]
    assert (
        filter_match_rule.header_fields["interface"] == "org.freedesktop.login1.Manager"
    )
    assert filter_match_rule.header_fields["member"] == "PrepareForShutdown"
    add_match_msg = dbus_router_mock.send_and_get_reply.call_args[0][0]
    assert (
        add_match_msg.header.fields[jeepney.low_level.HeaderFields.member] == "AddMatch"
    )
    assert add_match_msg.body == (
        "interface='org.freedesktop.login1.Manager',member='PrepareForShutdown'"
        ",path='/org/freedesktop/login1',type='signal'",
    )
    assert [
        c[1]["active"] for c in state_mock.preparing_for_shutdown_handler.call_args_list
    ] == [False, True, False]
