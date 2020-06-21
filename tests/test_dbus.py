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
import unittest.mock

import dbus
import pytest

import systemctl_mqtt._dbus

_UTC = datetime.timezone(offset=datetime.timedelta(seconds=0))

# pylint: disable=protected-access


def test_get_login_manager():
    login_manager = systemctl_mqtt._dbus.get_login_manager()
    assert isinstance(login_manager, dbus.proxies.Interface)
    assert login_manager.dbus_interface == "org.freedesktop.login1.Manager"
    # https://freedesktop.org/wiki/Software/systemd/logind/
    assert isinstance(login_manager.CanPowerOff(), dbus.String)


def test__log_shutdown_inhibitors_some(caplog):
    login_manager = unittest.mock.MagicMock()
    login_manager.ListInhibitors.return_value = dbus.Array(
        [
            dbus.Struct(
                (
                    dbus.String("shutdown:sleep"),
                    dbus.String("Developer"),
                    dbus.String("Haven't pushed my commits yet"),
                    dbus.String("delay"),
                    dbus.UInt32(1000),
                    dbus.UInt32(1234),
                ),
                signature=None,
            ),
            dbus.Struct(
                (
                    dbus.String("shutdown"),
                    dbus.String("Editor"),
                    dbus.String(""),
                    dbus.String("Unsafed files open"),
                    dbus.UInt32(0),
                    dbus.UInt32(42),
                ),
                signature=None,
            ),
        ],
        signature=dbus.Signature("(ssssuu)"),
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
    login_manager.ListInhibitors.return_value = dbus.Array([])
    with caplog.at_level(logging.DEBUG):
        systemctl_mqtt._dbus._log_shutdown_inhibitors(login_manager)
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.DEBUG
    assert caplog.records[0].message == "no shutdown inhibitor locks found"


def test__log_shutdown_inhibitors_fail(caplog):
    login_manager = unittest.mock.MagicMock()
    login_manager.ListInhibitors.side_effect = dbus.DBusException("mocked")
    with caplog.at_level(logging.DEBUG):
        systemctl_mqtt._dbus._log_shutdown_inhibitors(login_manager)
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert caplog.records[0].message == "failed to fetch shutdown inhibitors: mocked"


@pytest.mark.parametrize("action", ["poweroff", "reboot"])
def test__schedule_shutdown(action):
    login_manager_mock = unittest.mock.MagicMock()
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager", return_value=login_manager_mock,
    ):
        systemctl_mqtt._dbus.schedule_shutdown(action=action)
    assert login_manager_mock.ScheduleShutdown.call_count == 1
    schedule_args, schedule_kwargs = login_manager_mock.ScheduleShutdown.call_args
    assert len(schedule_args) == 2
    assert schedule_args[0] == action
    assert isinstance(schedule_args[1], dbus.UInt64)
    shutdown_datetime = datetime.datetime.fromtimestamp(
        schedule_args[1] / 10 ** 6, tz=_UTC,
    )
    delay = shutdown_datetime - datetime.datetime.now(tz=_UTC)
    assert delay.total_seconds() == pytest.approx(
        systemctl_mqtt._dbus._SHUTDOWN_DELAY.total_seconds(), abs=0.1,
    )
    assert not schedule_kwargs


@pytest.mark.parametrize("action", ["poweroff"])
@pytest.mark.parametrize(
    ("exception_message", "log_message"),
    [
        ("test message", "test message"),
        (
            "Interactive authentication required.",
            "unauthorized; missing polkit authorization rules?",
        ),
    ],
)
def test__schedule_shutdown_fail(caplog, action, exception_message, log_message):
    login_manager_mock = unittest.mock.MagicMock()
    login_manager_mock.ScheduleShutdown.side_effect = dbus.DBusException(
        exception_message
    )
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager", return_value=login_manager_mock,
    ), caplog.at_level(logging.DEBUG):
        systemctl_mqtt._dbus.schedule_shutdown(action=action)
    assert login_manager_mock.ScheduleShutdown.call_count == 1
    assert len(caplog.records) == 3
    assert caplog.records[0].levelno == logging.INFO
    assert caplog.records[0].message.startswith("scheduling {} for ".format(action))
    assert caplog.records[1].levelno == logging.ERROR
    assert caplog.records[1].message == "failed to schedule {}: {}".format(
        action, log_message
    )
    assert "inhibitor" in caplog.records[2].message


@pytest.mark.parametrize(
    ("topic_suffix", "expected_action_arg"), [("poweroff", "poweroff")]
)
def test_mqtt_topic_suffix_action_mapping(topic_suffix, expected_action_arg):
    mqtt_action = systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING[topic_suffix]
    login_manager_mock = unittest.mock.MagicMock()
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager", return_value=login_manager_mock,
    ):
        mqtt_action.action()
    assert login_manager_mock.ScheduleShutdown.call_count == 1
    schedule_args, schedule_kwargs = login_manager_mock.ScheduleShutdown.call_args
    assert len(schedule_args) == 2
    assert schedule_args[0] == expected_action_arg
    assert not schedule_kwargs
