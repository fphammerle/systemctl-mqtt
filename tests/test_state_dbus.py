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

import json
import logging
import unittest.mock

import dbus.types
import pytest

import systemctl_mqtt

# pylint: disable=protected-access


def test_shutdown_lock():
    lock_fd = unittest.mock.MagicMock()
    with unittest.mock.patch("systemctl_mqtt._dbus.get_login_manager"):
        state = systemctl_mqtt._State(
            mqtt_topic_prefix="any",
            homeassistant_discovery_prefix=None,
            homeassistant_node_id=None,
        )
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
    with unittest.mock.patch("systemctl_mqtt._dbus.get_login_manager"):
        state = systemctl_mqtt._State(
            mqtt_topic_prefix="any",
            homeassistant_discovery_prefix=None,
            homeassistant_node_id=None,
        )
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
        topic="any/preparing-for-shutdown",
        payload="true" if active else "false",
        retain=True,
    )
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.ERROR
    assert caplog.records[0].message.startswith(
        "failed to publish on any/preparing-for-shutdown"
    )


@pytest.mark.parametrize("active", [True, False])
def test_publish_preparing_for_shutdown(active):
    login_manager_mock = unittest.mock.MagicMock()
    login_manager_mock.Get.return_value = dbus.Boolean(active)
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager", return_value=login_manager_mock
    ):
        state = systemctl_mqtt._State(
            mqtt_topic_prefix="any",
            homeassistant_discovery_prefix=None,
            homeassistant_node_id=None,
        )
    assert state._login_manager == login_manager_mock
    mqtt_client_mock = unittest.mock.MagicMock()
    state.publish_preparing_for_shutdown(mqtt_client=mqtt_client_mock)
    login_manager_mock.Get.assert_called_once_with(
        "org.freedesktop.login1.Manager",
        "PreparingForShutdown",
        dbus_interface="org.freedesktop.DBus.Properties",
    )
    mqtt_client_mock.publish.assert_called_once_with(
        topic="any/preparing-for-shutdown",
        payload="true" if active else "false",
        retain=True,
    )


def test_publish_preparing_for_shutdown_get_fail(caplog):
    login_manager_mock = unittest.mock.MagicMock()
    login_manager_mock.Get.side_effect = dbus.DBusException("mocked")
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager", return_value=login_manager_mock
    ):
        state = systemctl_mqtt._State(
            mqtt_topic_prefix="any",
            homeassistant_discovery_prefix=None,
            homeassistant_node_id=None,
        )
    mqtt_client_mock = unittest.mock.MagicMock()
    state.publish_preparing_for_shutdown(mqtt_client=None)
    mqtt_client_mock.publish.assert_not_called()
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.ERROR
    assert (
        caplog.records[0].message
        == "failed to read logind's PreparingForShutdown property: mocked"
    )


@pytest.mark.parametrize("topic_prefix", ["systemctl/hostname", "hostname/systemctl"])
@pytest.mark.parametrize("discovery_prefix", ["homeassistant", "home/assistant"])
@pytest.mark.parametrize("node_id", ["node", "node-id"])
@pytest.mark.parametrize("hostname", ["hostname", "host-name"])
def test_publish_preparing_for_shutdown_homeassistant_config(
    topic_prefix, discovery_prefix, node_id, hostname,
):
    state = systemctl_mqtt._State(
        mqtt_topic_prefix=topic_prefix,
        homeassistant_discovery_prefix=discovery_prefix,
        homeassistant_node_id=node_id,
    )
    mqtt_client = unittest.mock.MagicMock()
    with unittest.mock.patch(
        "systemctl_mqtt._utils.get_hostname", return_value=hostname
    ):
        state.publish_preparing_for_shutdown_homeassistant_config(
            mqtt_client=mqtt_client
        )
    assert mqtt_client.publish.call_count == 1
    publish_args, publish_kwargs = mqtt_client.publish.call_args
    assert not publish_args
    assert publish_kwargs["retain"]
    assert (
        publish_kwargs["topic"]
        == discovery_prefix
        + "/binary_sensor/"
        + node_id
        + "/preparing-for-shutdown/config"
    )
    assert json.loads(publish_kwargs["payload"]) == {
        "unique_id": "systemctl-mqtt/" + node_id + "/logind/preparing-for-shutdown",
        "state_topic": topic_prefix + "/preparing-for-shutdown",
        "payload_on": "true",
        "payload_off": "false",
        "name": node_id + " preparing for shutdown",
    }
