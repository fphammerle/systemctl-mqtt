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
import getpass
import logging
import typing
import unittest.mock

import jeepney
import jeepney.low_level
import jeepney.wrappers
import pytest

import systemctl_mqtt._dbus.login_manager

# pylint: disable=protected-access


def test_get_login_manager_proxy():
    login_manager = systemctl_mqtt._dbus.login_manager.get_login_manager_proxy()
    assert isinstance(login_manager, jeepney.io.blocking.Proxy)
    assert login_manager._msggen.interface == "org.freedesktop.login1.Manager"
    # https://freedesktop.org/wiki/Software/systemd/logind/
    assert login_manager.CanPowerOff() in {("yes",), ("challenge",)}


def test_get_service_manager_proxy():
    service_manager = systemctl_mqtt._dbus.service_manager.get_service_manager_proxy()
    assert isinstance(service_manager, jeepney.io.blocking.Proxy)
    assert service_manager._msggen.interface == "org.freedesktop.systemd1.Manager"


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
        systemctl_mqtt._dbus.login_manager._log_shutdown_inhibitors(login_manager)
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
        systemctl_mqtt._dbus.login_manager._log_shutdown_inhibitors(login_manager)
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.DEBUG
    assert caplog.records[0].message == "no shutdown inhibitor locks found"


def test__log_shutdown_inhibitors_fail(caplog):
    login_manager = unittest.mock.MagicMock()
    login_manager.ListInhibitors.side_effect = DBusErrorResponseMock("error", "mocked")
    with caplog.at_level(logging.DEBUG):
        systemctl_mqtt._dbus.login_manager._log_shutdown_inhibitors(login_manager)
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
        "systemctl_mqtt._dbus.login_manager.get_login_manager_proxy",
        return_value=login_manager_mock,
    ):
        login_manager_mock.ListInhibitors.return_value = ([],)
        systemctl_mqtt._dbus.login_manager.schedule_shutdown(action=action, delay=delay)
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


@pytest.mark.parametrize(
    ("action", "error_name", "error_message", "log_message"),
    [
        (
            "poweroff",
            "test error",
            "test message",
            "[test error] ('test message',)",
        ),
        (
            "poweroff",
            "org.freedesktop.DBus.Error.InteractiveAuthorizationRequired",
            "Interactive authentication required.",
            """interactive authorization required

create /etc/polkit-1/rules.d/50-systemctl-mqtt.rules and insert the following rule:
polkit.addRule(function(action, subject) {
    if(action.id === "org.freedesktop.login1.power-off" && subject.user === "{{username}}") {
        return polkit.Result.YES;
    }
});
""".replace(
                "{{username}}", getpass.getuser()
            ),
        ),
        (
            "reboot",
            "org.freedesktop.DBus.Error.InteractiveAuthorizationRequired",
            "Interactive authentication required.",
            """interactive authorization required

create /etc/polkit-1/rules.d/50-systemctl-mqtt.rules and insert the following rule:
polkit.addRule(function(action, subject) {
    if(action.id === "org.freedesktop.login1.reboot" && subject.user === "{{username}}") {
        return polkit.Result.YES;
    }
});
""".replace(
                "{{username}}", getpass.getuser()
            ),
        ),
    ],
)
def test__schedule_shutdown_fail(
    caplog, action: str, error_name: str, error_message: str, log_message: str
) -> None:
    login_manager_mock = unittest.mock.MagicMock()
    login_manager_mock.ScheduleShutdown.side_effect = DBusErrorResponseMock(
        name=error_name,
        data=(error_message,),
    )
    login_manager_mock.ListInhibitors.return_value = ([],)
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.login_manager.get_login_manager_proxy",
        return_value=login_manager_mock,
    ), caplog.at_level(logging.DEBUG):
        systemctl_mqtt._dbus.login_manager.schedule_shutdown(
            action=action, delay=datetime.timedelta(seconds=21)
        )
    login_manager_mock.ScheduleShutdown.assert_called_once()
    assert len(caplog.records) == 3
    assert caplog.records[0].levelno == logging.INFO
    assert caplog.records[0].message.startswith(f"scheduling {action} for ")
    assert caplog.records[1].levelno == logging.ERROR
    assert caplog.records[1].message == f"failed to schedule {action}: {log_message}"
    assert "inhibitor" in caplog.records[2].message


@pytest.mark.parametrize("action", ["poweroff"])
@pytest.mark.parametrize(
    ("error_name", "error_message", "log_message"),
    [
        (
            "org.freedesktop.DBus.Error.InteractiveAuthorizationRequired",
            "Interactive authentication required.",
            """interactive authorization required

create /etc/polkit-1/rules.d/50-systemctl-mqtt.rules and insert the following rule:
polkit.addRule(function(action, subject) {
    if(action.id === "org.freedesktop.login1.power-off" && subject.user === "USERNAME") {
        return polkit.Result.YES;
    }
});
""",
        ),
    ],
)
def test__schedule_shutdown_fail_no_username(
    caplog, action, error_name, error_message, log_message
):
    login_manager_mock = unittest.mock.MagicMock()
    login_manager_mock.ScheduleShutdown.side_effect = DBusErrorResponseMock(
        name=error_name,
        data=(error_message,),
    )
    login_manager_mock.ListInhibitors.return_value = ([],)
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.login_manager.get_login_manager_proxy",
        return_value=login_manager_mock,
    ), unittest.mock.patch(
        "getpass.getuser", side_effect=OSError("No username set in the environment")
    ), caplog.at_level(
        logging.ERROR
    ):
        systemctl_mqtt._dbus.login_manager.schedule_shutdown(
            action=action, delay=datetime.timedelta(seconds=21)
        )
    login_manager_mock.ScheduleShutdown.assert_called_once()
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.ERROR
    assert caplog.records[0].message == f"failed to schedule {action}: {log_message}"


def test_suspend(caplog):
    login_manager_mock = unittest.mock.MagicMock()
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.login_manager.get_login_manager_proxy",
        return_value=login_manager_mock,
    ), caplog.at_level(logging.INFO):
        systemctl_mqtt._dbus.login_manager.suspend()
    login_manager_mock.Suspend.assert_called_once_with(interactive=False)
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.INFO
    assert caplog.records[0].message == "suspending system"


def test_lock_all_sessions(caplog):
    login_manager_mock = unittest.mock.MagicMock()
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.login_manager.get_login_manager_proxy",
        return_value=login_manager_mock,
    ), caplog.at_level(logging.INFO):
        systemctl_mqtt._dbus.login_manager.lock_all_sessions()
    login_manager_mock.LockSessions.assert_called_once_with()
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.INFO
    assert caplog.records[0].message == "instruct all sessions to activate screen locks"


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
            """interactive authorization required

create /etc/polkit-1/rules.d/50-systemctl-mqtt.rules and insert the following rule:
polkit.addRule(function(action, subject) {
    if(action.id === "org.freedesktop.login1.lock-sessions" && subject.user === "{{username}}") {
        return polkit.Result.YES;
    }
});
""".replace(
                "{{username}}", getpass.getuser()
            ),
        ),
    ],
)
def test_lock_all_sessions_fail(
    caplog: pytest.LogCaptureFixture,
    error_name: str,
    error_message: str,
    log_message: str,
) -> None:
    login_manager_mock = unittest.mock.MagicMock()
    login_manager_mock.LockSessions.side_effect = DBusErrorResponseMock(
        name=error_name, data=(error_message,)
    )
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.login_manager.get_login_manager_proxy",
        return_value=login_manager_mock,
    ), caplog.at_level(logging.ERROR):
        systemctl_mqtt._dbus.login_manager.lock_all_sessions()
    login_manager_mock.LockSessions.assert_called_once()
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.ERROR
    assert caplog.records[0].message == f"failed to lock all sessions: {log_message}"


async def _get_unit_path_mock(  # pylint: disable=unused-argument
    *, service_manager: jeepney.io.asyncio.Proxy, unit_name: str
) -> str:
    return "/org/freedesktop/systemd1/unit/" + unit_name


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "monitored_system_unit_names", [[], ["foo.service", "bar.service"]]
)
async def test__dbus_signal_loop(monitored_system_unit_names: typing.List[str]) -> None:
    # pylint: disable=too-many-locals,too-many-arguments
    state_mock = unittest.mock.AsyncMock()
    with unittest.mock.patch(
        "jeepney.io.asyncio.open_dbus_router",
    ) as open_dbus_router_mock, unittest.mock.patch(
        "systemctl_mqtt._get_unit_path", _get_unit_path_mock
    ), unittest.mock.patch(
        "systemctl_mqtt._dbus_signal_loop_unit"
    ) as dbus_signal_loop_unit_mock:
        async with open_dbus_router_mock() as dbus_router_mock:
            pass
        add_match_reply = unittest.mock.Mock()
        add_match_reply.body = ()
        dbus_router_mock.send_and_get_reply.return_value = add_match_reply
        msg_queue: asyncio.Queue[jeepney.low_level.Message] = asyncio.Queue()
        await msg_queue.put(jeepney.low_level.Message(header=None, body=(False,)))
        await msg_queue.put(jeepney.low_level.Message(header=None, body=(True,)))
        await msg_queue.put(jeepney.low_level.Message(header=None, body=(False,)))
        dbus_router_mock.filter = unittest.mock.MagicMock()
        dbus_router_mock.filter.return_value.__enter__.return_value = msg_queue
        state_mock.monitored_system_unit_names = monitored_system_unit_names
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
    assert not any(args for args, _ in dbus_signal_loop_unit_mock.await_args_list)
    dbus_signal_loop_unit_kwargs = [
        kwargs for _, kwargs in dbus_signal_loop_unit_mock.await_args_list
    ]
    assert [(a["unit_name"], a["unit_path"]) for a in dbus_signal_loop_unit_kwargs] == [
        (n, f"/org/freedesktop/systemd1/unit/{n}") for n in monitored_system_unit_names
    ]


def _mock_get_active_state_reply(state: str) -> unittest.mock.MagicMock:
    reply_mock = unittest.mock.MagicMock()
    reply_mock.body = (("s", state),)
    return reply_mock


@pytest.mark.asyncio
async def test__dbus_signal_loop_unit() -> None:
    state = systemctl_mqtt._State(
        mqtt_topic_prefix="prefix",
        homeassistant_discovery_prefix="unused",
        homeassistant_discovery_object_id="unused",
        poweroff_delay=datetime.timedelta(),
        monitored_system_unit_names=[],
        controlled_system_unit_names=[],
    )
    mqtt_client_mock = unittest.mock.AsyncMock()
    dbus_router_mock = unittest.mock.AsyncMock()
    bus_proxy_mock = unittest.mock.AsyncMock()
    bus_proxy_mock.AddMatch.return_value = ()
    get_active_state_reply_mock = unittest.mock.MagicMock()
    get_active_state_reply_mock.body = (("s", "active"),)
    states = [
        "active",
        "deactivating",
        "inactive",
        "inactive",
        "activating",
        "active",
        "active",
        "active",
        "inactive",
    ]
    dbus_router_mock.send_and_get_reply.side_effect = [
        _mock_get_active_state_reply(s) for s in states
    ]
    msg_queue: asyncio.Queue[jeepney.low_level.Message] = asyncio.Queue()
    for _ in range(len(states) - 1):
        await msg_queue.put(jeepney.low_level.Message(header=None, body=()))
    dbus_router_mock.filter = unittest.mock.MagicMock()
    dbus_router_mock.filter.return_value.__enter__.return_value = msg_queue
    loop_task = asyncio.create_task(
        systemctl_mqtt._dbus_signal_loop_unit(
            state=state,
            mqtt_client=mqtt_client_mock,
            dbus_router=dbus_router_mock,
            bus_proxy=bus_proxy_mock,
            unit_name="foo.service",
            unit_path="/org/freedesktop/systemd1/unit/whatever_2eservice",
        )
    )

    async def _abort_after_msg_queue():
        await msg_queue.join()
        loop_task.cancel()

    with pytest.raises(asyncio.exceptions.CancelledError):
        await asyncio.gather(*(loop_task, _abort_after_msg_queue()))
    bus_proxy_mock.AddMatch.assert_awaited_once()
    ((match_rule,), add_match_kwargs) = bus_proxy_mock.AddMatch.await_args
    assert match_rule.header_fields["interface"] == "org.freedesktop.DBus.Properties"
    assert match_rule.header_fields["member"] == "PropertiesChanged"
    assert not add_match_kwargs
    assert mqtt_client_mock.publish.await_args_list == [
        unittest.mock.call(
            topic="prefix/unit/system/foo.service/active-state", payload=s
        )
        for s in [  # consecutive duplicates filtered
            "active",
            "deactivating",
            "inactive",
            "activating",
            "active",
            "inactive",
        ]
    ]
