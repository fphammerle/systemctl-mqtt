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

import contextlib
import datetime
import logging
import threading
import time
import typing
import unittest.mock

import jeepney.fds
import jeepney.low_level
import paho.mqtt.client
import pytest
from paho.mqtt.client import MQTTMessage

import systemctl_mqtt

# pylint: disable=protected-access,too-many-positional-arguments


@contextlib.contextmanager
def mock_open_dbus_connection() -> typing.Iterator[unittest.mock.MagicMock]:
    with unittest.mock.patch("jeepney.io.blocking.open_dbus_connection") as mock:
        add_match_reply = unittest.mock.Mock()
        add_match_reply.body = ()
        mock.return_value.send_and_get_reply.return_value = add_match_reply
        mock.return_value.recv_until_filtered.side_effect = []
        yield mock


@pytest.mark.parametrize("mqtt_host", ["mqtt-broker.local"])
@pytest.mark.parametrize("mqtt_port", [1833])
@pytest.mark.parametrize("mqtt_topic_prefix", ["systemctl/host", "system/command"])
@pytest.mark.parametrize("homeassistant_discovery_prefix", ["homeassistant"])
@pytest.mark.parametrize("homeassistant_discovery_object_id", ["host", "node"])
def test__run(
    caplog,
    mqtt_host,
    mqtt_port,
    mqtt_topic_prefix,
    homeassistant_discovery_prefix,
    homeassistant_discovery_object_id,
):
    # pylint: disable=too-many-locals,too-many-arguments
    caplog.set_level(logging.DEBUG)
    login_manager_mock = unittest.mock.MagicMock()
    with unittest.mock.patch(
        "socket.create_connection"
    ) as create_socket_mock, unittest.mock.patch(
        "ssl.SSLContext.wrap_socket", autospec=True
    ) as ssl_wrap_socket_mock, unittest.mock.patch(
        "paho.mqtt.client.Client.loop_forever", autospec=True
    ) as mqtt_loop_forever_mock, unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager_proxy", return_value=login_manager_mock
    ), mock_open_dbus_connection() as open_dbus_connection_mock:
        ssl_wrap_socket_mock.return_value.send = len
        login_manager_mock.Inhibit.return_value = (jeepney.fds.FileDescriptor(-1),)
        login_manager_mock.Get.return_value = (("b", False),)
        with pytest.raises(StopIteration):
            systemctl_mqtt._run(
                mqtt_host=mqtt_host,
                mqtt_port=mqtt_port,
                mqtt_username=None,
                mqtt_password=None,
                mqtt_topic_prefix=mqtt_topic_prefix,
                homeassistant_discovery_prefix=homeassistant_discovery_prefix,
                homeassistant_discovery_object_id=homeassistant_discovery_object_id,
                poweroff_delay=datetime.timedelta(),
            )
    assert caplog.records[0].levelno == logging.INFO
    assert caplog.records[0].message == (
        f"connecting to MQTT broker {mqtt_host}:{mqtt_port} (TLS enabled)"
    )
    # correct remote?
    create_socket_mock.assert_called_once()
    create_socket_args, _ = create_socket_mock.call_args
    assert create_socket_args[0] == (mqtt_host, mqtt_port)
    # ssl enabled?
    ssl_wrap_socket_mock.assert_called_once()
    ssl_context = ssl_wrap_socket_mock.call_args[0][0]  # self
    assert ssl_context.check_hostname is True
    assert ssl_wrap_socket_mock.call_args[1]["server_hostname"] == mqtt_host
    # loop started?
    while threading.active_count() > 1:
        time.sleep(0.01)
    mqtt_loop_forever_mock.assert_called_once()
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
    open_dbus_connection_mock.assert_called_once_with(bus="SYSTEM")
    login_manager_mock.Inhibit.assert_called_once_with(
        what="shutdown",
        who="systemctl-mqtt",
        why="Report shutdown via MQTT",
        mode="delay",
    )
    login_manager_mock.Get.assert_called_once_with("PreparingForShutdown")
    open_dbus_connection_mock.return_value.send_and_get_reply.assert_called_once()
    assert sorted(mqtt_subscribe_mock.call_args_list) == [
        unittest.mock.call(mqtt_topic_prefix + "/lock-all-sessions"),
        unittest.mock.call(mqtt_topic_prefix + "/poweroff"),
    ]
    assert mqtt_client.on_message is None
    for suffix in ("poweroff", "lock-all-sessions"):
        assert (  # pylint: disable=comparison-with-callable
            mqtt_client._on_message_filtered[mqtt_topic_prefix + "/" + suffix]
            == systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING[
                suffix
            ].mqtt_message_callback
        )
    assert caplog.records[0].levelno == logging.DEBUG
    assert (
        caplog.records[0].message == f"connected to MQTT broker {mqtt_host}:{mqtt_port}"
    )
    assert caplog.records[1].levelno == logging.DEBUG
    assert caplog.records[1].message == "acquired shutdown inhibitor lock"
    assert caplog.records[2].levelno == logging.INFO
    assert (
        caplog.records[2].message
        == f"publishing 'false' on {mqtt_topic_prefix}/preparing-for-shutdown"
    )
    assert caplog.records[3].levelno == logging.DEBUG
    assert (
        caplog.records[3].message
        == "publishing home assistant config on "
        + homeassistant_discovery_prefix
        + "/device/"
        + homeassistant_discovery_object_id
        + "/config"
    )
    assert all(r.levelno == logging.INFO for r in caplog.records[4::2])
    assert {r.message for r in caplog.records[4::2]} == {
        f"subscribing to {mqtt_topic_prefix}/{s}"
        for s in ("poweroff", "lock-all-sessions")
    }
    assert all(r.levelno == logging.DEBUG for r in caplog.records[5::2])
    assert {r.message for r in caplog.records[5::2]} == {
        f"registered MQTT callback for topic {mqtt_topic_prefix}/{s}"
        f" triggering {systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING[s]}"
        for s in ("poweroff", "lock-all-sessions")
    }
    open_dbus_connection_mock.return_value.filter.assert_called_once()
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
    ) as mqtt_client_class, mock_open_dbus_connection(), pytest.raises(StopIteration):
        systemctl_mqtt._run(
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_disable_tls=mqtt_disable_tls,
            mqtt_username=None,
            mqtt_password=None,
            mqtt_topic_prefix="systemctl/hosts",
            homeassistant_discovery_prefix="homeassistant",
            homeassistant_discovery_object_id="host",
            poweroff_delay=datetime.timedelta(),
        )
    assert caplog.records[0].levelno == logging.INFO
    assert caplog.records[0].message == (
        f"connecting to MQTT broker {mqtt_host}:{mqtt_port}"
        f" (TLS {'disabled' if mqtt_disable_tls else 'enabled'})"
    )
    if mqtt_disable_tls:
        mqtt_client_class().tls_set.assert_not_called()
    else:
        mqtt_client_class().tls_set.assert_called_once_with(ca_certs=None)


def test__run_tls_default():
    with unittest.mock.patch(
        "paho.mqtt.client.Client"
    ) as mqtt_client_class, mock_open_dbus_connection(), pytest.raises(StopIteration):
        systemctl_mqtt._run(
            mqtt_host="mqtt-broker.local",
            mqtt_port=1833,
            # mqtt_disable_tls default,
            mqtt_username=None,
            mqtt_password=None,
            mqtt_topic_prefix="systemctl/hosts",
            homeassistant_discovery_prefix="homeassistant",
            homeassistant_discovery_object_id="host",
            poweroff_delay=datetime.timedelta(),
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
        "paho.mqtt.client.Client.loop_forever", autospec=True
    ) as mqtt_loop_forever_mock, unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager_proxy"
    ), mock_open_dbus_connection(), pytest.raises(
        StopIteration
    ):
        ssl_wrap_socket_mock.return_value.send = len
        systemctl_mqtt._run(
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_username=mqtt_username,
            mqtt_password=mqtt_password,
            mqtt_topic_prefix=mqtt_topic_prefix,
            homeassistant_discovery_prefix="discovery-prefix",
            homeassistant_discovery_object_id="node-id",
            poweroff_delay=datetime.timedelta(),
        )
    mqtt_loop_forever_mock.assert_called_once()
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
        "ssl.SSLContext.wrap_socket"
    ) as ssl_wrap_socket_mock, unittest.mock.patch(
        "paho.mqtt.client.Client.loop_forever", autospec=True
    ) as mqtt_loop_forever_mock, unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager_proxy"
    ) as get_login_manager_mock, mock_open_dbus_connection(), pytest.raises(
        StopIteration
    ):
        ssl_wrap_socket_mock.return_value.send = len
        get_login_manager_mock.return_value.Inhibit.return_value = (
            jeepney.fds.FileDescriptor(-1),
        )
        get_login_manager_mock.return_value.Get.return_value = (("b", True),)
        systemctl_mqtt._run(
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_username=None,
            mqtt_password=None,
            mqtt_topic_prefix=mqtt_topic_prefix,
            homeassistant_discovery_prefix="discovery-prefix",
            homeassistant_discovery_object_id="node-id",
            poweroff_delay=datetime.timedelta(),
        )
    while threading.active_count() > 1:
        time.sleep(0.01)
    mqtt_loop_forever_mock.assert_called_once()
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
        systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING["poweroff"], "trigger"
    ) as poweroff_trigger_mock:
        mqtt_client._handle_on_message(poweroff_message)
    poweroff_trigger_mock.assert_called_once_with(state=mqtt_client._userdata)
    assert all(r.levelno == logging.DEBUG for r in caplog.records)
    assert (
        caplog.records[0].message
        == f"received topic={poweroff_message.topic} payload=b''"
    )
    assert caplog.records[1].message == "executing action _MQTTActionSchedulePoweroff"
    assert caplog.records[2].message == "completed action _MQTTActionSchedulePoweroff"


@pytest.mark.parametrize("mqtt_host", ["mqtt-broker.local"])
@pytest.mark.parametrize("mqtt_port", [1833])
@pytest.mark.parametrize("mqtt_password", ["secret"])
def test__run_authentication_missing_username(mqtt_host, mqtt_port, mqtt_password):
    with unittest.mock.patch("paho.mqtt.client.Client"), unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager_proxy"
    ), mock_open_dbus_connection():
        with pytest.raises(ValueError, match=r"^Missing MQTT username$"):
            systemctl_mqtt._run(
                mqtt_host=mqtt_host,
                mqtt_port=mqtt_port,
                mqtt_username=None,
                mqtt_password=mqtt_password,
                mqtt_topic_prefix="prefix",
                homeassistant_discovery_prefix="discovery-prefix",
                homeassistant_discovery_object_id="node-id",
                poweroff_delay=datetime.timedelta(),
            )


@pytest.mark.parametrize("mqtt_topic", ["system/command/poweroff"])
@pytest.mark.parametrize("payload", [b"", b"junk"])
def test_mqtt_message_callback_poweroff(caplog, mqtt_topic: str, payload: bytes):
    message = MQTTMessage(topic=mqtt_topic.encode())
    message.payload = payload
    with unittest.mock.patch.object(
        systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING["poweroff"], "trigger"
    ) as trigger_mock, caplog.at_level(logging.DEBUG):
        systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING[
            "poweroff"
        ].mqtt_message_callback(
            None, "state_dummy", message  # type: ignore
        )
    trigger_mock.assert_called_once_with(state="state_dummy")
    assert len(caplog.records) == 3
    assert caplog.records[0].levelno == logging.DEBUG
    assert caplog.records[0].message == (
        f"received topic={mqtt_topic} payload={payload!r}"
    )
    assert caplog.records[1].levelno == logging.DEBUG
    assert caplog.records[1].message == "executing action _MQTTActionSchedulePoweroff"
    assert caplog.records[2].levelno == logging.DEBUG
    assert caplog.records[2].message == "completed action _MQTTActionSchedulePoweroff"


@pytest.mark.parametrize("mqtt_topic", ["system/command/poweroff"])
@pytest.mark.parametrize("payload", [b"", b"junk"])
def test_mqtt_message_callback_poweroff_retained(
    caplog, mqtt_topic: str, payload: bytes
):
    message = MQTTMessage(topic=mqtt_topic.encode())
    message.payload = payload
    message.retain = True
    with unittest.mock.patch.object(
        systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING["poweroff"], "trigger"
    ) as trigger_mock, caplog.at_level(logging.DEBUG):
        systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING[
            "poweroff"
        ].mqtt_message_callback(
            None, None, message  # type: ignore
        )
    trigger_mock.assert_not_called()
    assert len(caplog.records) == 2
    assert caplog.records[0].levelno == logging.DEBUG
    assert caplog.records[0].message == (
        f"received topic={mqtt_topic} payload={payload!r}"
    )
    assert caplog.records[1].levelno == logging.INFO
    assert caplog.records[1].message == "ignoring retained message"


@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("block", [True, False])
def test__publish_preparing_for_shutdown_blocking(active: bool, block: bool) -> None:
    login_manager_mock = unittest.mock.MagicMock()
    login_manager_mock.Get.return_value = (("b", active),)
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager_proxy", return_value=login_manager_mock
    ):
        state = systemctl_mqtt._State(
            mqtt_topic_prefix="prefix",
            homeassistant_discovery_prefix="prefix",
            homeassistant_discovery_object_id="object-id",
            poweroff_delay=datetime.timedelta(),
        )
    mqtt_client_mock = unittest.mock.MagicMock()
    state._publish_preparing_for_shutdown(
        mqtt_client=mqtt_client_mock, active=active, block=block
    )
    mqtt_client_mock.publish.assert_called_once_with(
        topic="prefix/preparing-for-shutdown",
        payload="true" if active else "false",
        retain=True,
    )
    msg_info = mqtt_client_mock.publish.return_value
    if block:
        msg_info.wait_for_publish.assert_called_once()
    else:
        msg_info.wait_for_publish.assert_not_called()
