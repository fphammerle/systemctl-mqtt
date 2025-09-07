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
import ssl
import unittest.mock

import aiomqtt
import jeepney.fds
import jeepney.low_level
import pytest

import systemctl_mqtt

# pylint: disable=protected-access,too-many-positional-arguments


@pytest.mark.asyncio
@pytest.mark.parametrize("mqtt_host", ["mqtt-broker.local"])
@pytest.mark.parametrize("mqtt_port", [1883])
@pytest.mark.parametrize("mqtt_topic_prefix", ["systemctl/host", "system/command"])
@pytest.mark.parametrize("homeassistant_discovery_prefix", ["homeassistant"])
@pytest.mark.parametrize("homeassistant_discovery_object_id", ["host", "node"])
async def test__run(
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
        "aiomqtt.Client", autospec=False
    ) as mqtt_client_class_mock, unittest.mock.patch(
        "systemctl_mqtt._dbus.login_manager.get_login_manager_proxy",
        return_value=login_manager_mock,
    ), unittest.mock.patch(
        "systemctl_mqtt._dbus_signal_loop"
    ) as dbus_signal_loop_mock:
        login_manager_mock.Inhibit.return_value = (jeepney.fds.FileDescriptor(-1),)
        login_manager_mock.Get.return_value = (("b", False),)
        await systemctl_mqtt._run(
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_username=None,
            mqtt_password=None,
            mqtt_topic_prefix=mqtt_topic_prefix,
            homeassistant_discovery_prefix=homeassistant_discovery_prefix,
            homeassistant_discovery_object_id=homeassistant_discovery_object_id,
            poweroff_delay=datetime.timedelta(),
            monitored_system_unit_names=[],
            controlled_system_unit_names=[],
        )
    assert caplog.records[0].levelno == logging.INFO
    assert caplog.records[0].message == (
        f"connecting to MQTT broker {mqtt_host}:{mqtt_port} (TLS enabled)"
    )
    mqtt_client_class_mock.assert_called_once()
    _, mqtt_client_init_kwargs = mqtt_client_class_mock.call_args
    assert mqtt_client_init_kwargs.pop("hostname") == mqtt_host
    assert mqtt_client_init_kwargs.pop("port") == mqtt_port
    assert isinstance(mqtt_client_init_kwargs.pop("tls_context"), ssl.SSLContext)
    assert mqtt_client_init_kwargs.pop("username") is None
    assert mqtt_client_init_kwargs.pop("password") is None
    assert mqtt_client_init_kwargs.pop("will") == aiomqtt.Will(
        topic=mqtt_topic_prefix + "/status",
        payload="offline",
        qos=0,
        retain=True,
        properties=None,
    )
    assert not mqtt_client_init_kwargs
    login_manager_mock.Inhibit.assert_called_once_with(
        what="shutdown",
        who="systemctl-mqtt",
        why="Report shutdown via MQTT",
        mode="delay",
    )
    login_manager_mock.Get.assert_called_once_with("PreparingForShutdown")
    async with mqtt_client_class_mock() as mqtt_client_mock:
        pass
    assert mqtt_client_mock.publish.call_count == 4
    assert (
        mqtt_client_mock.publish.call_args_list[0][1]["topic"]
        == f"{homeassistant_discovery_prefix}/device/{homeassistant_discovery_object_id}/config"
    )
    assert mqtt_client_mock.publish.call_args_list[1] == unittest.mock.call(
        topic=mqtt_topic_prefix + "/preparing-for-shutdown",
        payload="false",
        retain=False,
    )
    assert mqtt_client_mock.publish.call_args_list[2][1] == {
        "topic": mqtt_topic_prefix + "/status",
        "payload": "online",
        "retain": True,
    }
    assert mqtt_client_mock.publish.call_args_list[3][1] == {
        "topic": mqtt_topic_prefix + "/status",
        "payload": "offline",
        "retain": True,
    }
    assert sorted(mqtt_client_mock.subscribe.call_args_list) == [
        unittest.mock.call(mqtt_topic_prefix + "/lock-all-sessions"),
        unittest.mock.call(mqtt_topic_prefix + "/poweroff"),
        unittest.mock.call(mqtt_topic_prefix + "/suspend"),
    ]
    assert caplog.records[1].levelno == logging.DEBUG
    assert (
        caplog.records[1].message == f"connected to MQTT broker {mqtt_host}:{mqtt_port}"
    )
    assert caplog.records[2].levelno == logging.DEBUG
    assert caplog.records[2].message == "acquired shutdown inhibitor lock"
    assert caplog.records[3].levelno == logging.DEBUG
    assert (
        caplog.records[3].message
        == "publishing home assistant config on "
        + homeassistant_discovery_prefix
        + "/device/"
        + homeassistant_discovery_object_id
        + "/config"
    )
    assert caplog.records[4].levelno == logging.INFO
    assert (
        caplog.records[4].message
        == f"publishing 'false' on {mqtt_topic_prefix}/preparing-for-shutdown"
    )
    assert all(r.levelno == logging.INFO for r in caplog.records[5::2])
    assert {r.message for r in caplog.records[5:]} == {
        f"subscribing to {mqtt_topic_prefix}/{s}"
        for s in ("poweroff", "lock-all-sessions", "suspend")
    }
    dbus_signal_loop_mock.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mqtt_host", ["mqtt-broker.local"])
@pytest.mark.parametrize("mqtt_port", [1833])
@pytest.mark.parametrize("mqtt_disable_tls", [True, False])
async def test__run_tls(caplog, mqtt_host, mqtt_port, mqtt_disable_tls):
    caplog.set_level(logging.INFO)
    with unittest.mock.patch(
        "aiomqtt.Client"
    ) as mqtt_client_class_mock, unittest.mock.patch(
        "systemctl_mqtt._dbus_signal_loop"
    ) as dbus_signal_loop_mock:
        await systemctl_mqtt._run(
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_disable_tls=mqtt_disable_tls,
            mqtt_username=None,
            mqtt_password=None,
            mqtt_topic_prefix="systemctl/hosts",
            homeassistant_discovery_prefix="homeassistant",
            homeassistant_discovery_object_id="host",
            poweroff_delay=datetime.timedelta(),
            monitored_system_unit_names=[],
            controlled_system_unit_names=[],
        )
    mqtt_client_class_mock.assert_called_once()
    _, mqtt_client_init_kwargs = mqtt_client_class_mock.call_args
    assert mqtt_client_init_kwargs.pop("hostname") == mqtt_host
    assert mqtt_client_init_kwargs.pop("port") == mqtt_port
    if mqtt_disable_tls:
        assert mqtt_client_init_kwargs.pop("tls_context") is None
    else:
        assert isinstance(mqtt_client_init_kwargs.pop("tls_context"), ssl.SSLContext)
    assert set(mqtt_client_init_kwargs.keys()) == {"username", "password", "will"}
    assert caplog.records[0].levelno == logging.INFO
    assert caplog.records[0].message == (
        f"connecting to MQTT broker {mqtt_host}:{mqtt_port}"
        f" (TLS {'disabled' if mqtt_disable_tls else 'enabled'})"
    )
    dbus_signal_loop_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test__run_tls_default():
    with unittest.mock.patch(
        "aiomqtt.Client"
    ) as mqtt_client_class_mock, unittest.mock.patch(
        "systemctl_mqtt._dbus_signal_loop"
    ) as dbus_signal_loop_mock:
        await systemctl_mqtt._run(
            mqtt_host="mqtt-broker.local",
            mqtt_port=1883,
            # mqtt_disable_tls default,
            mqtt_username=None,
            mqtt_password=None,
            mqtt_topic_prefix="systemctl/hosts",
            homeassistant_discovery_prefix="homeassistant",
            homeassistant_discovery_object_id="host",
            poweroff_delay=datetime.timedelta(),
            monitored_system_unit_names=[],
            controlled_system_unit_names=[],
        )
    mqtt_client_class_mock.assert_called_once()
    # enabled by default
    assert isinstance(
        mqtt_client_class_mock.call_args[1]["tls_context"], ssl.SSLContext
    )
    dbus_signal_loop_mock.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mqtt_host", ["mqtt-broker.local"])
@pytest.mark.parametrize("mqtt_port", [1883])
@pytest.mark.parametrize("mqtt_username", ["me"])
@pytest.mark.parametrize("mqtt_password", [None, "secret"])
@pytest.mark.parametrize("mqtt_topic_prefix", ["systemctl/host"])
async def test__run_authentication(
    mqtt_host, mqtt_port, mqtt_username, mqtt_password, mqtt_topic_prefix
):
    with unittest.mock.patch(
        "aiomqtt.Client"
    ) as mqtt_client_class_mock, unittest.mock.patch(
        "systemctl_mqtt._dbus_signal_loop"
    ) as dbus_signal_loop_mock:
        await systemctl_mqtt._run(
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_username=mqtt_username,
            mqtt_password=mqtt_password,
            mqtt_topic_prefix=mqtt_topic_prefix,
            homeassistant_discovery_prefix="discovery-prefix",
            homeassistant_discovery_object_id="node-id",
            poweroff_delay=datetime.timedelta(),
            monitored_system_unit_names=[],
            controlled_system_unit_names=[],
        )
    mqtt_client_class_mock.assert_called_once()
    _, mqtt_client_init_kwargs = mqtt_client_class_mock.call_args
    assert mqtt_client_init_kwargs["username"] == mqtt_username
    if mqtt_password:
        assert mqtt_client_init_kwargs["password"] == mqtt_password
    else:
        assert mqtt_client_init_kwargs["password"] is None
    dbus_signal_loop_mock.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mqtt_host", ["mqtt-broker.local"])
@pytest.mark.parametrize("mqtt_port", [1883])
@pytest.mark.parametrize("mqtt_password", ["secret"])
async def test__run_authentication_missing_username(
    mqtt_host: str, mqtt_port: int, mqtt_password: str
) -> None:
    with unittest.mock.patch("aiomqtt.Client"), unittest.mock.patch(
        "systemctl_mqtt._dbus.login_manager.get_login_manager_proxy"
    ), unittest.mock.patch("systemctl_mqtt._dbus_signal_loop") as dbus_signal_loop_mock:
        with pytest.raises(ValueError, match=r"^Missing MQTT username$"):
            await systemctl_mqtt._run(
                mqtt_host=mqtt_host,
                mqtt_port=mqtt_port,
                mqtt_username=None,
                mqtt_password=mqtt_password,
                mqtt_topic_prefix="prefix",
                homeassistant_discovery_prefix="discovery-prefix",
                homeassistant_discovery_object_id="node-id",
                poweroff_delay=datetime.timedelta(),
                monitored_system_unit_names=[],
                controlled_system_unit_names=[],
            )
    dbus_signal_loop_mock.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mqtt_topic_prefix", ["systemctl/host"])
async def test__run_sigint(mqtt_topic_prefix: str):
    login_manager_mock = unittest.mock.MagicMock()
    with unittest.mock.patch(
        "aiomqtt.Client", autospec=False
    ) as mqtt_client_class_mock, unittest.mock.patch(
        "systemctl_mqtt._dbus.login_manager.get_login_manager_proxy",
        return_value=login_manager_mock,
    ), unittest.mock.patch(
        "asyncio.gather", side_effect=KeyboardInterrupt
    ):
        login_manager_mock.Inhibit.return_value = (jeepney.fds.FileDescriptor(-1),)
        login_manager_mock.Get.return_value = (("b", False),)
        with pytest.raises(KeyboardInterrupt):
            await systemctl_mqtt._run(
                mqtt_host="mqtt-broker.local",
                mqtt_port=1883,
                mqtt_username=None,
                mqtt_password=None,
                mqtt_topic_prefix=mqtt_topic_prefix,
                homeassistant_discovery_prefix="homeassistant",
                homeassistant_discovery_object_id="host",
                poweroff_delay=datetime.timedelta(),
                monitored_system_unit_names=[],
                controlled_system_unit_names=[],
            )
    async with mqtt_client_class_mock() as mqtt_client_mock:
        pass
    assert mqtt_client_mock.publish.call_count == 4
    assert mqtt_client_mock.publish.call_args_list[0][1]["topic"].endswith("/config")
    assert mqtt_client_mock.publish.call_args_list[1][1]["topic"].endswith(
        "/preparing-for-shutdown"
    )
    assert mqtt_client_mock.publish.call_args_list[2][1] == {
        "topic": mqtt_topic_prefix + "/status",
        "payload": "online",
        "retain": True,
    }
    assert mqtt_client_mock.publish.call_args_list[3][1] == {
        "topic": mqtt_topic_prefix + "/status",
        "payload": "offline",
        "retain": True,
    }


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:coroutine '_dbus_signal_loop' was never awaited")
@pytest.mark.filterwarnings("ignore:coroutine '_mqtt_message_loop' was never awaited")
@pytest.mark.parametrize("mqtt_topic_prefix", ["systemctl/host", "system/command"])
async def test__mqtt_message_loop_trigger_poweroff(
    caplog: pytest.LogCaptureFixture, mqtt_topic_prefix: str
) -> None:
    state = systemctl_mqtt._State(
        mqtt_topic_prefix=mqtt_topic_prefix,
        homeassistant_discovery_prefix="homeassistant",
        homeassistant_discovery_object_id="whatever",
        poweroff_delay=datetime.timedelta(seconds=21),
        monitored_system_unit_names=[],
        controlled_system_unit_names=[],
    )
    mqtt_client_mock = unittest.mock.AsyncMock()
    mqtt_client_mock.messages.__aiter__.return_value = [
        aiomqtt.Message(
            topic=mqtt_topic_prefix + "/poweroff",
            payload=b"some-payload",
            qos=0,
            retain=False,
            mid=42 // 2,
            properties=None,
        )
    ]
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.login_manager.schedule_shutdown"
    ) as schedule_shutdown_mock, caplog.at_level(logging.DEBUG):
        await systemctl_mqtt._mqtt_message_loop(
            state=state, mqtt_client=mqtt_client_mock
        )
    assert sorted(mqtt_client_mock.subscribe.await_args_list) == [
        unittest.mock.call(mqtt_topic_prefix + "/lock-all-sessions"),
        unittest.mock.call(mqtt_topic_prefix + "/poweroff"),
        unittest.mock.call(mqtt_topic_prefix + "/suspend"),
    ]
    schedule_shutdown_mock.assert_called_once_with(
        action="poweroff", delay=datetime.timedelta(seconds=21)
    )
    assert [
        t for t in caplog.record_tuples[2:] if not t[2].startswith("subscribing to ")
    ] == [
        (
            "systemctl_mqtt",
            logging.DEBUG,
            f"received message on topic '{mqtt_topic_prefix}/poweroff': b'some-payload'",
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("mqtt_topic_prefix", ["systemctl/host"])
async def test__mqtt_message_loop_retained(
    caplog: pytest.LogCaptureFixture, mqtt_topic_prefix: str
) -> None:
    state = systemctl_mqtt._State(
        mqtt_topic_prefix=mqtt_topic_prefix,
        homeassistant_discovery_prefix="homeassistant",
        homeassistant_discovery_object_id="whatever",
        poweroff_delay=datetime.timedelta(seconds=21),
        monitored_system_unit_names=[],
        controlled_system_unit_names=[],
    )
    mqtt_client_mock = unittest.mock.AsyncMock()
    mqtt_client_mock.messages.__aiter__.return_value = [
        aiomqtt.Message(
            topic=mqtt_topic_prefix + "/poweroff",
            payload=b"some-payload",
            qos=0,
            retain=True,
            mid=42 // 2,
            properties=None,
        )
    ]
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.login_manager.schedule_shutdown"
    ) as schedule_shutdown_mock, caplog.at_level(logging.DEBUG):
        await systemctl_mqtt._mqtt_message_loop(
            state=state, mqtt_client=mqtt_client_mock
        )
    schedule_shutdown_mock.assert_not_called()
    assert [
        t for t in caplog.record_tuples[2:] if not t[2].startswith("subscribing to ")
    ] == [
        (
            "systemctl_mqtt",
            logging.INFO,
            "ignoring retained message on topic 'systemctl/host/poweroff'",
        ),
    ]


@pytest.mark.parametrize("mqtt_topic_prefix", ["systemctl/host", "systemd/raspberrypi"])
@pytest.mark.parametrize("unit_name", ["foo.service", "bar.service"])
def test_state_get_system_unit_active_state_mqtt_topic(
    mqtt_topic_prefix: str, unit_name: str
) -> None:
    state = systemctl_mqtt._State(
        mqtt_topic_prefix=mqtt_topic_prefix,
        homeassistant_discovery_prefix="homeassistant",
        homeassistant_discovery_object_id="whatever",
        poweroff_delay=datetime.timedelta(seconds=21),
        monitored_system_unit_names=[],
        controlled_system_unit_names=[],
    )
    assert (
        state.get_system_unit_active_state_mqtt_topic(unit_name=unit_name)
        == f"{mqtt_topic_prefix}/unit/system/{unit_name}/active-state"
    )


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:coroutine '_dbus_signal_loop' was never awaited")
@pytest.mark.filterwarnings("ignore:coroutine '_mqtt_message_loop' was never awaited")
@pytest.mark.parametrize("mqtt_topic_prefix", ["systemctl/host"])
@pytest.mark.parametrize("unit_name", ["foo.service", "bar.service"])
@pytest.mark.parametrize("action", ["restart", "start", "stop"])
async def test__mqtt_message_loop_triggers_unit_action(
    caplog: pytest.LogCaptureFixture,
    mqtt_topic_prefix: str,
    unit_name: str,
    action: str,
) -> None:
    state = systemctl_mqtt._State(
        mqtt_topic_prefix=mqtt_topic_prefix,
        homeassistant_discovery_prefix="homeassistant",
        homeassistant_discovery_object_id="whatever",
        poweroff_delay=datetime.timedelta(seconds=21),
        monitored_system_unit_names=[],
        controlled_system_unit_names=[unit_name],
    )

    mqtt_client_mock = unittest.mock.AsyncMock()
    topic = f"{mqtt_topic_prefix}/unit/system/{unit_name}/{action}"
    mqtt_client_mock.messages.__aiter__.return_value = [
        aiomqtt.Message(
            topic=topic,
            payload=b"some-payload",
            qos=0,
            retain=False,
            mid=42 // 2,
            properties=None,
        )
    ]

    with unittest.mock.patch(
        f"systemctl_mqtt._dbus.service_manager.{action}_unit"
    ) as trigger_service_mock, caplog.at_level(logging.DEBUG):
        await systemctl_mqtt._mqtt_message_loop(
            state=state, mqtt_client=mqtt_client_mock
        )

    # check subscription
    assert unittest.mock.call(topic) in mqtt_client_mock.subscribe.await_args_list

    # check correct action method called
    trigger_service_mock.assert_called_once_with(unit_name=unit_name)

    # check logs (skip "subscribing to ..." chatter)
    assert [
        t for t in caplog.record_tuples[2:] if not t[2].startswith("subscribing to ")
    ] == [
        (
            "systemctl_mqtt",
            logging.DEBUG,
            f"received message on topic '{topic}': b'some-payload'",
        ),
    ]
