# systemctl-mqtt - MQTT client triggering shutdown on systemd-based systems
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

import systemctl_mqtt

_UTC = datetime.timezone(offset=datetime.timedelta(seconds=0))

# pylint: disable=protected-access


def test__get_login_manager():
    login_manager = systemctl_mqtt._get_login_manager()
    assert isinstance(login_manager, dbus.proxies.Interface)
    assert login_manager.dbus_interface == "org.freedesktop.login1.Manager"
    # https://freedesktop.org/wiki/Software/systemd/logind/
    assert isinstance(login_manager.CanPowerOff(), dbus.String)


@pytest.mark.parametrize("action", ["poweroff", "reboot"])
def test__schedule_shutdown(action):
    login_manager_mock = unittest.mock.MagicMock()
    with unittest.mock.patch(
        "systemctl_mqtt._get_login_manager", return_value=login_manager_mock
    ):
        systemctl_mqtt._schedule_shutdown(action=action)
    login_manager_mock.ScheduleShutdown.assert_called_once()
    schedule_args, schedule_kwargs = login_manager_mock.ScheduleShutdown.call_args
    assert len(schedule_args) == 2
    assert schedule_args[0] == action
    shutdown_datetime = datetime.datetime.fromtimestamp(
        schedule_args[1] / 10 ** 6, tz=_UTC,
    )
    delay = shutdown_datetime - datetime.datetime.now(tz=_UTC)
    assert delay.total_seconds() == pytest.approx(
        datetime.timedelta(seconds=4).total_seconds(), abs=0.1,
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
        "systemctl_mqtt._get_login_manager", return_value=login_manager_mock
    ), caplog.at_level(logging.DEBUG):
        systemctl_mqtt._schedule_shutdown(action=action)
    login_manager_mock.ScheduleShutdown.assert_called_once()
    assert len(caplog.records) == 2
    assert caplog.records[0].levelno == logging.INFO
    assert caplog.records[0].message.startswith("scheduling {} for ".format(action))
    assert caplog.records[1].levelno == logging.ERROR
    assert caplog.records[1].message == "failed to schedule {}: {}".format(
        action, log_message
    )


@pytest.mark.parametrize(
    ("topic_suffix", "expected_action_arg"), [("poweroff", "poweroff")]
)
def test_mqtt_topic_suffix_action_mapping(topic_suffix, expected_action_arg):
    action = systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING[topic_suffix]
    login_manager_mock = unittest.mock.MagicMock()
    with unittest.mock.patch(
        "systemctl_mqtt._get_login_manager", return_value=login_manager_mock
    ):
        action()
    login_manager_mock.ScheduleShutdown.assert_called_once()
    schedule_args, schedule_kwargs = login_manager_mock.ScheduleShutdown.call_args
    assert len(schedule_args) == 2
    assert schedule_args[0] == expected_action_arg
    assert not schedule_kwargs
