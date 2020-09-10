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
import threading
import time
import unittest.mock

import dbus
import paho.mqtt.client
import pytest
from paho.mqtt.client import MQTTMessage

import systemctl_mqtt

# pylint: disable=protected-access


@pytest.mark.parametrize("mqtt_host", ["mqtt-broker.local"])
@pytest.mark.parametrize("mqtt_port", [1833])
@pytest.mark.parametrize("mqtt_topic_prefix", ["systemctl/host", "system/command"])
@pytest.mark.parametrize("homeassistant_discovery_prefix", ["homeassistant"])
@pytest.mark.parametrize("homeassistant_node_id", ["host", "node"])
def test__run(
    caplog,
    mqtt_host,
    mqtt_port,
    mqtt_topic_prefix,
    homeassistant_discovery_prefix,
    homeassistant_node_id,
):
    # pylint: disable=too-many-locals,too-many-arguments
    caplog.set_level(logging.DEBUG)
    with unittest.mock.patch(
        "socket.create_connection"
    ) as create_socket_mock, unittest.mock.patch(
        "ssl.SSLContext.wrap_socket", autospec=True,
    ) as ssl_wrap_socket_mock, unittest.mock.patch(
        "paho.mqtt.client.Client.loop_forever", autospec=True,
    ) as mqtt_loop_forever_mock, unittest.mock.patch(
        "gi.repository.GLib.MainLoop.run"
    ) as glib_loop_mock, unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager"
    ) as get_login_manager_mock:
        ssl_wrap_socket_mock.return_value.send = len
        get_login_manager_mock.return_value.Get.return_value = dbus.Boolean(False)
        systemctl_mqtt._run(
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_username=None,
            mqtt_password=None,
            mqtt_topic_prefix=mqtt_topic_prefix,
            homeassistant_discovery_prefix=homeassistant_discovery_prefix,
            homeassistant_node_id=homeassistant_node_id,
        )
    assert caplog.records[0].levelno == logging.INFO
    assert caplog.records[0].message == (
        "connecting to MQTT broker {}:{} (TLS enabled)".format(mqtt_host, mqtt_port)
    )
    # correct remote?
    assert create_socket_mock.call_count == 1
    create_socket_args, _ = create_socket_mock.call_args
    assert create_socket_args[0] == (mqtt_host, mqtt_port)
    # ssl enabled?
    assert ssl_wrap_socket_mock.call_count == 1
    ssl_context = ssl_wrap_socket_mock.call_args[0][0]  # self
    assert ssl_context.check_hostname is True
    assert ssl_wrap_socket_mock.call_args[1]["server_hostname"] == mqtt_host
    # loop started?
    while threading.active_count() > 1:
        time.sleep(0.01)
    assert mqtt_loop_forever_mock.call_count == 1
    (mqtt_client,) = mqtt_loop_forever_mock.call_args[0]
    assert mqtt_client._tls_insecure is False
    # credentials
    assert mqtt_client._username is None
    assert mqtt_client._password is None
    # connect callback
    caplog.clear()
    mqtt_client.socket().getpeername.return_value = (mqtt_host, mqtt_port)
    with unittest.mock.patch(
        "paho.mqtt.client.Client.subscribe"
    ) as mqtt_subscribe_mock:
        mqtt_client.on_connect(mqtt_client, mqtt_client._userdata, {}, 0)
    state = mqtt_client._userdata
    assert (
        state._login_manager.connect_to_signal.call_args[1]["signal_name"]
        == "PrepareForShutdown"
    )
    mqtt_subscribe_mock.assert_called_once_with(mqtt_topic_prefix + "/poweroff")
    assert mqtt_client.on_message is None
    assert (  # pylint: disable=comparison-with-callable
        mqtt_client._on_message_filtered[mqtt_topic_prefix + "/poweroff"]
        == systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING[
            "poweroff"
        ].mqtt_message_callback
    )
    assert caplog.records[0].levelno == logging.DEBUG
    assert caplog.records[0].message == "connected to MQTT broker {}:{}".format(
        mqtt_host, mqtt_port
    )
    assert caplog.records[1].levelno == logging.DEBUG
    assert caplog.records[1].message == "acquired shutdown inhibitor lock"
    assert caplog.records[2].levelno == logging.INFO
    assert caplog.records[2].message == "publishing 'false' on {}".format(
        mqtt_topic_prefix + "/preparing-for-shutdown"
    )
    assert caplog.records[3].levelno == logging.DEBUG
    assert (
        caplog.records[3].message
        == "publishing home assistant config on "
        + homeassistant_discovery_prefix
        + "/binary_sensor/"
        + homeassistant_node_id
        + "/preparing-for-shutdown/config"
    )
    assert caplog.records[4].levelno == logging.INFO
    assert caplog.records[4].message == "subscribing to {}".format(
        mqtt_topic_prefix + "/poweroff"
    )
    assert caplog.records[5].levelno == logging.DEBUG
    assert caplog.records[5].message == "registered MQTT callback for topic {}".format(
        mqtt_topic_prefix + "/poweroff"
    ) + " triggering {}".format(
        systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING["poweroff"].action
    )
    # dbus loop started?
    glib_loop_mock.assert_called_once_with()
    # waited for mqtt loop to stop?
    assert mqtt_client._thread_terminate
    assert mqtt_client._thread is None


@pytest.mark.parametrize("mqtt_host", ["mqtt-broker.local"])
@pytest.mark.parametrize("mqtt_port", [1833])
@pytest.mark.parametrize("mqtt_disable_tls", [True, False])
def test__run_tls(caplog, mqtt_host, mqtt_port, mqtt_disable_tls):
    caplog.set_level(logging.INFO)
    with unittest.mock.patch(
        "paho.mqtt.client.Client"
    ) as mqtt_client_class, unittest.mock.patch("gi.repository.GLib.MainLoop.run"):
        systemctl_mqtt._run(
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_disable_tls=mqtt_disable_tls,
            mqtt_username=None,
            mqtt_password=None,
            mqtt_topic_prefix="systemctl/hosts",
            homeassistant_discovery_prefix="homeassistant",
            homeassistant_node_id="host",
        )
    assert caplog.records[0].levelno == logging.INFO
    assert caplog.records[0].message == (
        "connecting to MQTT broker {}:{} (TLS {})".format(
            mqtt_host, mqtt_port, "disabled" if mqtt_disable_tls else "enabled"
        )
    )
    if mqtt_disable_tls:
        mqtt_client_class().tls_set.assert_not_called()
    else:
        mqtt_client_class().tls_set.assert_called_once_with(ca_certs=None)


def test__run_tls_default():
    with unittest.mock.patch(
        "paho.mqtt.client.Client"
    ) as mqtt_client_class, unittest.mock.patch("gi.repository.GLib.MainLoop.run"):
        systemctl_mqtt._run(
            mqtt_host="mqtt-broker.local",
            mqtt_port=1833,
            # mqtt_disable_tls default,
            mqtt_username=None,
            mqtt_password=None,
            mqtt_topic_prefix="systemctl/hosts",
            homeassistant_discovery_prefix="homeassistant",
            homeassistant_node_id="host",
        )
    # enabled by default
    mqtt_client_class().tls_set.assert_called_once_with(ca_certs=None)


@pytest.mark.parametrize("mqtt_host", ["mqtt-broker.local"])
@pytest.mark.parametrize("mqtt_port", [1833])
@pytest.mark.parametrize("mqtt_username", ["me"])
@pytest.mark.parametrize("mqtt_password", [None, "secret"])
@pytest.mark.parametrize("mqtt_topic_prefix", ["systemctl/host"])
def test__run_authentication(
    mqtt_host, mqtt_port, mqtt_username, mqtt_password, mqtt_topic_prefix
):
    with unittest.mock.patch("socket.create_connection"), unittest.mock.patch(
        "ssl.SSLContext.wrap_socket"
    ) as ssl_wrap_socket_mock, unittest.mock.patch(
        "paho.mqtt.client.Client.loop_forever", autospec=True,
    ) as mqtt_loop_forever_mock, unittest.mock.patch(
        "gi.repository.GLib.MainLoop.run"
    ), unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager"
    ):
        ssl_wrap_socket_mock.return_value.send = len
        systemctl_mqtt._run(
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_username=mqtt_username,
            mqtt_password=mqtt_password,
            mqtt_topic_prefix=mqtt_topic_prefix,
            homeassistant_discovery_prefix="discovery-prefix",
            homeassistant_node_id="node-id",
        )
    assert mqtt_loop_forever_mock.call_count == 1
    (mqtt_client,) = mqtt_loop_forever_mock.call_args[0]
    assert mqtt_client._username.decode() == mqtt_username
    if mqtt_password:
        assert mqtt_client._password.decode() == mqtt_password
    else:
        assert mqtt_client._password is None


def _initialize_mqtt_client(
    mqtt_host, mqtt_port, mqtt_topic_prefix
) -> paho.mqtt.client.Client:
    with unittest.mock.patch("socket.create_connection"), unittest.mock.patch(
        "ssl.SSLContext.wrap_socket",
    ) as ssl_wrap_socket_mock, unittest.mock.patch(
        "paho.mqtt.client.Client.loop_forever", autospec=True,
    ) as mqtt_loop_forever_mock, unittest.mock.patch(
        "gi.repository.GLib.MainLoop.run"
    ), unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager"
    ) as get_login_manager_mock:
        ssl_wrap_socket_mock.return_value.send = len
        get_login_manager_mock.return_value.Get.return_value = dbus.Boolean(False)
        systemctl_mqtt._run(
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_username=None,
            mqtt_password=None,
            mqtt_topic_prefix=mqtt_topic_prefix,
            homeassistant_discovery_prefix="discovery-prefix",
            homeassistant_node_id="node-id",
        )
    while threading.active_count() > 1:
        time.sleep(0.01)
    assert mqtt_loop_forever_mock.call_count == 1
    (mqtt_client,) = mqtt_loop_forever_mock.call_args[0]
    mqtt_client.socket().getpeername.return_value = (mqtt_host, mqtt_port)
    mqtt_client.on_connect(mqtt_client, mqtt_client._userdata, {}, 0)
    return mqtt_client


@pytest.mark.parametrize("mqtt_host", ["mqtt-broker.local"])
@pytest.mark.parametrize("mqtt_port", [1833])
@pytest.mark.parametrize("mqtt_topic_prefix", ["systemctl/host", "system/command"])
def test__client_handle_message(caplog, mqtt_host, mqtt_port, mqtt_topic_prefix):
    mqtt_client = _initialize_mqtt_client(
        mqtt_host=mqtt_host, mqtt_port=mqtt_port, mqtt_topic_prefix=mqtt_topic_prefix
    )
    caplog.clear()
    caplog.set_level(logging.DEBUG)
    poweroff_message = MQTTMessage(topic=mqtt_topic_prefix.encode() + b"/poweroff")
    with unittest.mock.patch.object(
        systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING["poweroff"], "action",
    ) as poweroff_action_mock:
        mqtt_client._handle_on_message(poweroff_message)
    poweroff_action_mock.assert_called_once_with()
    assert all(r.levelno == logging.DEBUG for r in caplog.records)
    assert caplog.records[0].message == "received topic={} payload=b''".format(
        poweroff_message.topic
    )
    assert caplog.records[1].message.startswith("executing action poweroff")
    assert caplog.records[2].message.startswith("completed action poweroff")


@pytest.mark.parametrize("mqtt_host", ["mqtt-broker.local"])
@pytest.mark.parametrize("mqtt_port", [1833])
@pytest.mark.parametrize("mqtt_password", ["secret"])
def test__run_authentication_missing_username(mqtt_host, mqtt_port, mqtt_password):
    with unittest.mock.patch("paho.mqtt.client.Client"), unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager"
    ):
        with pytest.raises(ValueError):
            systemctl_mqtt._run(
                mqtt_host=mqtt_host,
                mqtt_port=mqtt_port,
                mqtt_username=None,
                mqtt_password=mqtt_password,
                mqtt_topic_prefix="prefix",
                homeassistant_discovery_prefix="discovery-prefix",
                homeassistant_node_id="node-id",
            )


@pytest.mark.parametrize("mqtt_topic", ["system/command/poweroff"])
@pytest.mark.parametrize("payload", [b"", b"junk"])
def test_mqtt_message_callback_poweroff(caplog, mqtt_topic: str, payload: bytes):
    message = MQTTMessage(topic=mqtt_topic.encode())
    message.payload = payload
    with unittest.mock.patch.object(
        systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING["poweroff"], "action",
    ) as action_mock, caplog.at_level(logging.DEBUG):
        systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING[
            "poweroff"
        ].mqtt_message_callback(
            None, None, message  # type: ignore
        )
    action_mock.assert_called_once_with()
    assert len(caplog.records) == 3
    assert caplog.records[0].levelno == logging.DEBUG
    assert caplog.records[0].message == (
        "received topic={} payload={!r}".format(mqtt_topic, payload)
    )
    assert caplog.records[1].levelno == logging.DEBUG
    assert caplog.records[1].message.startswith(
        "executing action {} ({!r})".format("poweroff", action_mock)
    )
    assert caplog.records[2].levelno == logging.DEBUG
    assert caplog.records[2].message.startswith(
        "completed action {} ({!r})".format("poweroff", action_mock)
    )


@pytest.mark.parametrize("mqtt_topic", ["system/command/poweroff"])
@pytest.mark.parametrize("payload", [b"", b"junk"])
def test_mqtt_message_callback_poweroff_retained(
    caplog, mqtt_topic: str, payload: bytes
):
    message = MQTTMessage(topic=mqtt_topic.encode())
    message.payload = payload
    message.retain = True
    with unittest.mock.patch.object(
        systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING["poweroff"], "action",
    ) as action_mock, caplog.at_level(logging.DEBUG):
        systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING[
            "poweroff"
        ].mqtt_message_callback(
            None, None, message  # type: ignore
        )
    action_mock.assert_not_called()
    assert len(caplog.records) == 2
    assert caplog.records[0].levelno == logging.DEBUG
    assert caplog.records[0].message == (
        "received topic={} payload={!r}".format(mqtt_topic, payload)
    )
    assert caplog.records[1].levelno == logging.INFO
    assert caplog.records[1].message == "ignoring retained message"
