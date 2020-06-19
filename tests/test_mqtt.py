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

import logging
import unittest.mock

import pytest
from paho.mqtt.client import MQTTMessage

import systemctl_mqtt

# pylint: disable=protected-access


@pytest.mark.parametrize("mqtt_host", ["mqtt-broker.local"])
@pytest.mark.parametrize("mqtt_port", [1833])
@pytest.mark.parametrize("mqtt_topic_prefix", ["systemctl/host", "system/command"])
def test__run(mqtt_host, mqtt_port, mqtt_topic_prefix):
    with unittest.mock.patch(
        "socket.create_connection"
    ) as create_socket_mock, unittest.mock.patch(
        "ssl.SSLContext.wrap_socket", autospec=True,
    ) as ssl_wrap_socket_mock, unittest.mock.patch(
        "paho.mqtt.client.Client.loop_forever", autospec=True,
    ) as mqtt_loop_forever_mock, unittest.mock.patch(
        "systemctl_mqtt._mqtt_on_message"
    ) as message_handler_mock:
        ssl_wrap_socket_mock.return_value.send = len
        systemctl_mqtt._run(
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_username=None,
            mqtt_password=None,
            mqtt_topic_prefix=mqtt_topic_prefix,
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
    assert mqtt_loop_forever_mock.call_count == 1
    (mqtt_client,) = mqtt_loop_forever_mock.call_args[0]
    assert mqtt_client._tls_insecure is False
    # credentials
    assert mqtt_client._username is None
    assert mqtt_client._password is None
    # connect callback
    mqtt_client.socket().getpeername.return_value = (mqtt_host, mqtt_port)
    with unittest.mock.patch(
        "paho.mqtt.client.Client.subscribe"
    ) as mqtt_subscribe_mock:
        mqtt_client.on_connect(mqtt_client, mqtt_client._userdata, {}, 0)
    mqtt_subscribe_mock.assert_called_once_with(mqtt_topic_prefix + "/poweroff")
    # message callback
    test_message = MQTTMessage(topic=b"test")
    message_handler_mock.assert_not_called()
    mqtt_client._handle_on_message(test_message)
    message_handler_mock.assert_called_once_with(
        mqtt_client, mqtt_client._userdata, test_message
    )


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
    ) as mqtt_loop_forever_mock:
        ssl_wrap_socket_mock.return_value.send = len
        systemctl_mqtt._run(
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_username=mqtt_username,
            mqtt_password=mqtt_password,
            mqtt_topic_prefix=mqtt_topic_prefix,
        )
    assert mqtt_loop_forever_mock.call_count == 1
    (mqtt_client,) = mqtt_loop_forever_mock.call_args[0]
    assert mqtt_client._username.decode() == mqtt_username
    if mqtt_password:
        assert mqtt_client._password.decode() == mqtt_password
    else:
        assert mqtt_client._password is None


@pytest.mark.parametrize("mqtt_host", ["mqtt-broker.local"])
@pytest.mark.parametrize("mqtt_port", [1833])
@pytest.mark.parametrize("mqtt_password", ["secret"])
def test__run_authentication_missing_username(mqtt_host, mqtt_port, mqtt_password):
    with unittest.mock.patch("paho.mqtt.client.Client"):
        with pytest.raises(ValueError):
            systemctl_mqtt._run(
                mqtt_host=mqtt_host,
                mqtt_port=mqtt_port,
                mqtt_username=None,
                mqtt_password=mqtt_password,
                mqtt_topic_prefix="prefix",
            )


@pytest.mark.parametrize("mqtt_topic_prefix", ["system/command"])
@pytest.mark.parametrize("payload", [b"", b"junk"])
def test__mqtt_on_message_poweroff(caplog, mqtt_topic_prefix: str, payload: bytes):
    mqtt_topic = mqtt_topic_prefix + "/poweroff"
    message = MQTTMessage(topic=mqtt_topic.encode())
    message.payload = payload
    settings = systemctl_mqtt._Settings(mqtt_topic_prefix=mqtt_topic_prefix)
    action_mock = unittest.mock.MagicMock()
    settings.mqtt_topic_action_mapping[mqtt_topic] = action_mock  # functools.partial
    with caplog.at_level(logging.DEBUG):
        systemctl_mqtt._mqtt_on_message(
            None, settings, message,
        )
    assert len(caplog.records) == 3
    assert caplog.records[0].levelno == logging.DEBUG
    assert caplog.records[0].message == (
        "received topic={} payload={!r}".format(mqtt_topic, payload)
    )
    assert caplog.records[1].levelno == logging.DEBUG
    assert caplog.records[1].message.startswith(
        "executing action {!r}".format(action_mock)
    )
    assert caplog.records[2].levelno == logging.DEBUG
    assert caplog.records[2].message.startswith(
        "completed action {!r}".format(action_mock)
    )
    action_mock.assert_called_once_with()


@pytest.mark.parametrize(
    ("topic", "payload"), [("system/poweroff", b""), ("system/poweroff", "payload"),],
)
def test__mqtt_on_message_ignored(
    caplog, topic: str, payload: bytes,
):
    message = MQTTMessage(topic=topic.encode())
    message.payload = payload
    settings = systemctl_mqtt._Settings(mqtt_topic_prefix="system/command")
    settings.mqtt_topic_action_mapping = {}  # provoke KeyError on access
    with caplog.at_level(logging.DEBUG):
        systemctl_mqtt._mqtt_on_message(
            None, settings, message,
        )
    assert len(caplog.records) == 2
    assert caplog.records[0].levelno == logging.DEBUG
    assert caplog.records[0].message == (
        "received topic={} payload={!r}".format(topic, payload)
    )
    assert caplog.records[1].levelno == logging.WARNING
    assert caplog.records[1].message == "unexpected topic {}".format(topic)


@pytest.mark.parametrize(
    ("topic", "payload"), [("system/command/poweroff", b"")],
)
def test__mqtt_on_message_ignored_retained(
    caplog, topic: str, payload: bytes,
):
    message = MQTTMessage(topic=topic.encode())
    message.payload = payload
    message.retain = True
    settings = systemctl_mqtt._Settings(mqtt_topic_prefix="system/command")
    settings.mqtt_topic_action_mapping = {}  # provoke KeyError on access
    with caplog.at_level(logging.DEBUG):
        systemctl_mqtt._mqtt_on_message(
            None, settings, message,
        )
    assert len(caplog.records) == 2
    assert caplog.records[0].levelno == logging.DEBUG
    assert caplog.records[0].message == (
        "received topic={} payload={!r}".format(topic, payload)
    )
    assert caplog.records[1].levelno == logging.INFO
    assert caplog.records[1].message == "ignoring retained message"
