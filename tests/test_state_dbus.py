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

import logging
import unittest.mock

import dbus.types
import pytest

import systemctl_mqtt

# pylint: disable=protected-access


def test_shutdown_lock():
    lock_fd = unittest.mock.MagicMock()
    with unittest.mock.patch("systemctl_mqtt._get_login_manager"):
        state = systemctl_mqtt._State(mqtt_topic_prefix="any")
        state._login_manager.Inhibit.return_value = lock_fd
        state.acquire_shutdown_lock()
    state._login_manager.Inhibit.assert_called_once_with(
        "shutdown", "systemctl-mqtt", "Report shutdown via MQTT", "delay",
    )
    assert state._shutdown_lock == lock_fd
    # https://dbus.freedesktop.org/doc/dbus-python/dbus.types.html#dbus.types.UnixFd.take
    lock_fd.take.return_value = "fdnum"
    with unittest.mock.patch("os.close") as close_mock:
        state.release_shutdown_lock()
    close_mock.assert_called_once_with("fdnum")


@pytest.mark.parametrize("active", [True, False])
def test_prepare_for_shutdown_handler(caplog, active):
    with unittest.mock.patch("systemctl_mqtt._get_login_manager"):
        state = systemctl_mqtt._State(mqtt_topic_prefix="any")
    mqtt_client_mock = unittest.mock.MagicMock()
    state.register_prepare_for_shutdown_handler(mqtt_client=mqtt_client_mock)
    # pylint: disable=no-member,comparison-with-callable
    connect_to_signal_kwargs = state._login_manager.connect_to_signal.call_args[1]
    assert connect_to_signal_kwargs["signal_name"] == "PrepareForShutdown"
    handler_function = connect_to_signal_kwargs["handler_function"]
    assert handler_function.func == state._prepare_for_shutdown_handler
    with unittest.mock.patch.object(
        state, "acquire_shutdown_lock"
    ) as acquire_lock_mock, unittest.mock.patch.object(
        state, "release_shutdown_lock"
    ) as release_lock_mock:
        handler_function(dbus.types.Boolean(active))
    if active:
        acquire_lock_mock.assert_not_called()
        release_lock_mock.assert_called_once_with()
    else:
        acquire_lock_mock.assert_called_once_with()
        release_lock_mock.assert_not_called()
    mqtt_client_mock.publish.assert_called_once_with(
        topic="any/preparing-for-shutdown", payload="true" if active else "false",
    )
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.ERROR
    assert caplog.records[0].message.startswith(
        "failed to publish on any/preparing-for-shutdown"
    )
