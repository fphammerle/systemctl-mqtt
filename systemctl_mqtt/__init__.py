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

import abc
import argparse
import asyncio
import datetime
import functools
import importlib.metadata
import json
import logging
import os
import pathlib
import socket
import ssl
import threading
import typing

import aiomqtt
import jeepney
import jeepney.bus_messages
import jeepney.io.asyncio

import systemctl_mqtt._dbus.login_manager
import systemctl_mqtt._dbus.service_manager
import systemctl_mqtt._homeassistant
import systemctl_mqtt._mqtt

_MQTT_DEFAULT_PORT = 1883
_MQTT_DEFAULT_TLS_PORT = 8883
# > payload_not_available string (Optional, default: offline)
# https://web.archive.org/web/20250101075341/https://www.home-assistant.io/integrations/sensor.mqtt/#payload_not_available
_MQTT_PAYLOAD_NOT_AVAILABLE = "offline"
_MQTT_PAYLOAD_AVAILABLE = "online"
# https://www.home-assistant.io/integrations/mqtt/#birth-and-last-will-messages
_HOMEASSISTANT_BIRTH_TOPIC = "homeassistant/status"
_HOMEASSISTANT_BIRTH_PAYLOAD = b"online"
_ARGUMENT_LOG_LEVEL_MAPPING = {
    a: getattr(logging, a.upper())
    for a in ("debug", "info", "warning", "error", "critical")
}

_LOGGER = logging.getLogger(__name__)


class _State:
    # pylint: disable=too-many-instance-attributes
    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        mqtt_topic_prefix: str,
        homeassistant_discovery_prefix: str,
        homeassistant_discovery_object_id: str,
        poweroff_delay: datetime.timedelta,
        monitored_system_unit_names: list[str],
        controlled_system_unit_names: list[str],
    ) -> None:
        self._mqtt_topic_prefix = mqtt_topic_prefix
        self._homeassistant_discovery_prefix = homeassistant_discovery_prefix
        self._homeassistant_discovery_object_id = homeassistant_discovery_object_id
        self._login_manager = (
            systemctl_mqtt._dbus.login_manager.get_login_manager_proxy()
        )
        self._shutdown_lock: jeepney.fds.FileDescriptor | None = None
        self._shutdown_lock_mutex = threading.Lock()
        self.poweroff_delay = poweroff_delay
        self._monitored_system_unit_names = monitored_system_unit_names
        self._controlled_system_unit_names = controlled_system_unit_names

    @property
    def mqtt_topic_prefix(self) -> str:
        return self._mqtt_topic_prefix

    @property
    def mqtt_availability_topic(self) -> str:
        # > mqtt.ATTR_TOPIC: "homeassistant/status",
        # https://github.com/home-assistant/core/blob/2024.12.5/tests/components/mqtt/conftest.py#L23
        # > _MQTT_AVAILABILITY_TOPIC = "switchbot-mqtt/status"
        # https://github.com/fphammerle/switchbot-mqtt/blob/v3.3.1/switchbot_mqtt/__init__.py#L30
        return self._mqtt_topic_prefix + "/status"

    def get_system_unit_active_state_mqtt_topic(self, *, unit_name: str) -> str:
        return self._mqtt_topic_prefix + "/unit/system/" + unit_name + "/active-state"

    def get_system_unit_action_mqtt_topic(
        self, *, unit_name: str, action_name: str
    ) -> str:
        return self._mqtt_topic_prefix + "/unit/system/" + unit_name + "/" + action_name

    @property
    def monitored_system_unit_names(self) -> list[str]:
        return self._monitored_system_unit_names

    @property
    def controlled_system_unit_names(self) -> list[str]:
        return self._controlled_system_unit_names

    @property
    def shutdown_lock_acquired(self) -> bool:
        return self._shutdown_lock is not None

    def acquire_shutdown_lock(self) -> None:
        with self._shutdown_lock_mutex:
            assert self._shutdown_lock is None
            # https://www.freedesktop.org/wiki/Software/systemd/inhibit/
            (self._shutdown_lock,) = self._login_manager.Inhibit(
                what="shutdown",
                who="systemctl-mqtt",
                why="Report shutdown via MQTT",
                mode="delay",
            )
            assert isinstance(
                self._shutdown_lock, jeepney.fds.FileDescriptor
            ), self._shutdown_lock
            _LOGGER.debug("acquired shutdown inhibitor lock")

    def release_shutdown_lock(self) -> None:
        with self._shutdown_lock_mutex:
            if self._shutdown_lock:
                self._shutdown_lock.close()
                _LOGGER.debug("released shutdown inhibitor lock")
                self._shutdown_lock = None

    @property
    def _preparing_for_shutdown_topic(self) -> str:
        return self.mqtt_topic_prefix + "/preparing-for-shutdown"

    async def _publish_preparing_for_shutdown(
        self, *, mqtt_client: aiomqtt.Client, active: bool
    ) -> None:
        topic = self._preparing_for_shutdown_topic
        # pylint: disable=protected-access
        payload = systemctl_mqtt._mqtt.encode_bool(active)
        _LOGGER.info("publishing %r on %s", payload, topic)
        await mqtt_client.publish(topic=topic, payload=payload, retain=True)

    async def preparing_for_shutdown_handler(
        self, active: bool, mqtt_client: aiomqtt.Client
    ) -> None:
        active = bool(active)
        await self._publish_preparing_for_shutdown(
            mqtt_client=mqtt_client, active=active
        )
        if active:
            self.release_shutdown_lock()
        else:
            self.acquire_shutdown_lock()

    async def publish_preparing_for_shutdown(self, mqtt_client: aiomqtt.Client) -> None:
        try:
            ((return_type, active),) = self._login_manager.Get("PreparingForShutdown")
        except jeepney.wrappers.DBusErrorResponse as exc:
            _LOGGER.error(
                "failed to read logind's PreparingForShutdown property: %s", exc
            )
            return
        assert return_type == "b", return_type
        assert isinstance(active, bool), active
        await self._publish_preparing_for_shutdown(
            mqtt_client=mqtt_client, active=active
        )

    async def publish_homeassistant_device_config(
        self, mqtt_client: aiomqtt.Client
    ) -> None:
        # <discovery_prefix>/<component>/[<node_id>/]<object_id>/config
        # https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery
        discovery_topic = "/".join(
            (
                self._homeassistant_discovery_prefix,
                "device",
                self._homeassistant_discovery_object_id,
                "config",
            )
        )
        hostname = (
            # pylint: disable=protected-access; function in internal module
            systemctl_mqtt._utils.get_hostname()
        )
        package_metadata = importlib.metadata.metadata(__name__)
        unique_id_prefix = "systemctl-mqtt-" + hostname
        config = {
            "device": {"identifiers": [hostname], "name": hostname},
            "origin": {
                "name": package_metadata["Name"],
                "sw_version": package_metadata["Version"],
                "support_url": package_metadata["Home-page"],
            },
            "availability": {"topic": self.mqtt_availability_topic},
            "components": {
                "logind/preparing-for-shutdown": {
                    "unique_id": unique_id_prefix + "-logind-preparing-for-shutdown",
                    "object_id": f"{hostname}_logind_preparing_for_shutdown",  # entity id
                    "name": "preparing for shutdown",  # home assistant prepends device name
                    "platform": "binary_sensor",
                    "state_topic": self._preparing_for_shutdown_topic,
                    # pylint: disable=protected-access
                    "payload_on": systemctl_mqtt._mqtt.encode_bool(True),
                    "payload_off": systemctl_mqtt._mqtt.encode_bool(False),
                },
            },
        }
        for mqtt_topic_suffix in _MQTT_TOPIC_SUFFIX_ACTION_MAPPING.keys():
            # false positive warning by mypy:
            # > Unsupported target for indexed assignment
            config["components"]["logind/" + mqtt_topic_suffix] = {  # type: ignore
                "unique_id": unique_id_prefix + "-logind-" + mqtt_topic_suffix,
                "object_id": hostname
                + "_logind_"
                + mqtt_topic_suffix.replace("-", "_"),  # entity id
                "name": mqtt_topic_suffix.replace("-", " "),
                "platform": "button",
                "command_topic": self.mqtt_topic_prefix + "/" + mqtt_topic_suffix,
            }
        for unit_name in self._monitored_system_unit_names:
            config["components"]["unit/system/" + unit_name + "/active-state"] = {  # type: ignore
                "unique_id": f"{unique_id_prefix}-unit-system-{unit_name}-active-state",
                "object_id": f"{hostname}_unit_system_{unit_name}_active_state",
                "name": f"{unit_name} active state",
                "platform": "sensor",
                "state_topic": self.get_system_unit_active_state_mqtt_topic(
                    unit_name=unit_name
                ),
            }
        for unit_name in self._controlled_system_unit_names:
            component_prefix = "unit/system/" + unit_name
            for action_name, action_class in [
                ("start", _MQTTActionStartUnit),
                ("stop", _MQTTActionStopUnit),
                ("restart", _MQTTActionRestartUnit),
                ("isolate", _MQTTActionIsolateUnit),
            ]:
                if action_class(unit_name).is_allowed():
                    config["components"][component_prefix + "/" + action_name] = {  # type: ignore
                        "unique_id": f"{unique_id_prefix}-unit-system-{unit_name}-{action_name}",
                        "object_id": f"{hostname}_unit_system_{unit_name}_{action_name}",
                        "name": f"{unit_name} {action_name}",
                        "platform": "button",
                        "command_topic": self.get_system_unit_action_mqtt_topic(
                            unit_name=unit_name, action_name=action_name
                        ),
                    }

        _LOGGER.debug("publishing home assistant config on %s", discovery_topic)
        await mqtt_client.publish(
            topic=discovery_topic, payload=json.dumps(config), retain=True
        )


class _MQTTAction(metaclass=abc.ABCMeta):
    def is_allowed(self) -> bool:
        return True

    @abc.abstractmethod
    def trigger(self, state: _State) -> None:
        pass  # pragma: no cover

    def __str__(self) -> str:
        return type(self).__name__


class _MQTTActionSchedulePoweroff(_MQTTAction):
    # pylint: disable=too-few-public-methods
    def trigger(self, state: _State) -> None:
        # pylint: disable=protected-access
        systemctl_mqtt._dbus.login_manager.schedule_shutdown(
            action="poweroff", delay=state.poweroff_delay
        )


class _MQTTActionStartUnit(_MQTTAction):
    # pylint: disable=protected-access,too-few-public-methods
    def __init__(self, unit_name: str):
        self._unit_name = unit_name

    def trigger(self, state: _State) -> None:
        systemctl_mqtt._dbus.service_manager.start_unit(unit_name=self._unit_name)


class _MQTTActionStopUnit(_MQTTAction):
    # pylint: disable=protected-access,too-few-public-methods
    def __init__(self, unit_name: str):
        self._unit_name = unit_name

    def trigger(self, state: _State) -> None:
        systemctl_mqtt._dbus.service_manager.stop_unit(unit_name=self._unit_name)


class _MQTTActionRestartUnit(_MQTTAction):
    # pylint: disable=protected-access,too-few-public-methods
    def __init__(self, unit_name: str):
        self._unit_name = unit_name

    def trigger(self, state: _State) -> None:
        systemctl_mqtt._dbus.service_manager.restart_unit(unit_name=self._unit_name)


class _MQTTActionIsolateUnit(_MQTTAction):
    # pylint: disable=protected-access,too-few-public-methods
    def __init__(self, unit_name: str):
        self._unit_name = unit_name

    def is_allowed(self) -> bool:
        return systemctl_mqtt._dbus.service_manager.is_isolate_unit_allowed(
            unit_name=self._unit_name
        )

    def trigger(self, state: _State) -> None:
        systemctl_mqtt._dbus.service_manager.isolate_unit(unit_name=self._unit_name)


class _MQTTActionLockAllSessions(_MQTTAction):
    # pylint: disable=too-few-public-methods
    def trigger(self, state: _State) -> None:
        # pylint: disable=protected-access
        systemctl_mqtt._dbus.login_manager.lock_all_sessions()


class _MQTTActionSuspend(_MQTTAction):
    # pylint: disable=too-few-public-methods
    def trigger(self, state: _State) -> None:
        # pylint: disable=protected-access
        systemctl_mqtt._dbus.login_manager.suspend()


_MQTT_TOPIC_SUFFIX_ACTION_MAPPING = {
    "poweroff": _MQTTActionSchedulePoweroff(),
    "lock-all-sessions": _MQTTActionLockAllSessions(),
    "suspend": _MQTTActionSuspend(),
}


async def _mqtt_message_loop(*, state: _State, mqtt_client: aiomqtt.Client) -> None:
    _LOGGER.info("subscribing to %s", _HOMEASSISTANT_BIRTH_TOPIC)
    await mqtt_client.subscribe(_HOMEASSISTANT_BIRTH_TOPIC)

    action_by_topic: dict[str, _MQTTAction] = {}
    for topic_suffix, action in _MQTT_TOPIC_SUFFIX_ACTION_MAPPING.items():
        topic = state.mqtt_topic_prefix + "/" + topic_suffix
        _LOGGER.info("subscribing to %s", topic)
        await mqtt_client.subscribe(topic)
        action_by_topic[topic] = action

    for unit_name in state.controlled_system_unit_names:
        for topic_suffix, action_class in [
            ("start", _MQTTActionStartUnit),
            ("stop", _MQTTActionStopUnit),
            ("restart", _MQTTActionRestartUnit),
            ("isolate", _MQTTActionIsolateUnit),
        ]:
            topic = (
                state.mqtt_topic_prefix
                + "/unit/system/"
                + unit_name
                + "/"
                + topic_suffix
            )
            _LOGGER.info("subscribing to %s", topic)
            await mqtt_client.subscribe(topic)
            action_by_topic[topic] = action_class(unit_name=unit_name)

    async for message in mqtt_client.messages:
        if message.retain:
            _LOGGER.info("ignoring retained message on topic %r", message.topic.value)
        elif message.topic.value == _HOMEASSISTANT_BIRTH_TOPIC:
            _LOGGER.debug("received homeassistant status: %r", message.payload)
            if message.payload == _HOMEASSISTANT_BIRTH_PAYLOAD:
                await state.publish_homeassistant_device_config(mqtt_client=mqtt_client)
        else:
            _LOGGER.debug(
                "received message on topic %r: %r", message.topic.value, message.payload
            )
            action_by_topic[message.topic.value].trigger(state=state)


async def _dbus_signal_loop_preparing_for_shutdown(
    *,
    state: _State,
    mqtt_client: aiomqtt.Client,
    dbus_router: jeepney.io.asyncio.DBusRouter,
    bus_proxy: jeepney.io.asyncio.Proxy,
) -> None:
    preparing_for_shutdown_match_rule = (
        # pylint: disable=protected-access
        systemctl_mqtt._dbus.login_manager.get_login_manager_signal_match_rule(
            "PrepareForShutdown"
        )
    )
    assert await bus_proxy.AddMatch(preparing_for_shutdown_match_rule) == ()
    with dbus_router.filter(preparing_for_shutdown_match_rule) as queue:
        while True:
            message: jeepney.low_level.Message = await queue.get()
            (preparing_for_shutdown,) = message.body
            await state.preparing_for_shutdown_handler(
                active=preparing_for_shutdown, mqtt_client=mqtt_client
            )
            queue.task_done()


async def _get_unit_path(
    *, service_manager: jeepney.io.asyncio.Proxy, unit_name: str
) -> str:
    (path,) = await service_manager.GetUnit(name=unit_name)
    return path


async def _dbus_signal_loop_unit(  # pylint: disable=too-many-arguments
    *,
    state: _State,
    mqtt_client: aiomqtt.Client,
    dbus_router: jeepney.io.asyncio.DBusRouter,
    bus_proxy: jeepney.io.asyncio.Proxy,
    unit_name: str,
    unit_path: str,
) -> None:
    unit_proxy = jeepney.io.asyncio.Proxy(
        # pylint: disable=protected-access
        msggen=systemctl_mqtt._dbus.service_manager.Unit(object_path=unit_path),
        router=dbus_router,
    )
    unit_properties_changed_match_rule = jeepney.MatchRule(
        type="signal",
        interface="org.freedesktop.DBus.Properties",
        member="PropertiesChanged",
        path=unit_path,
    )
    assert (await bus_proxy.AddMatch(unit_properties_changed_match_rule)) == ()
    # > Table 1. Unit ACTIVE states …
    # > active	Started, bound, plugged in, …
    # > inactive	Stopped, unbound, unplugged, …
    # > failed	… process returned error code on exit, crashed, an operation
    # .         timed out, or after too many restarts).
    # > activating	Changing from inactive to active.
    # > deactivating	Changing from active to inactive.
    # > maintenance	Unit is inactive and … maintenance … in progress.
    # > reloading	Unit is active and it is reloading its configuration.
    # > refreshing	Unit is active and a new mount is being activated in its
    # .             namespace.
    # https://web.archive.org/web/20250101121304/https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.systemd1.html
    active_state_topic = state.get_system_unit_active_state_mqtt_topic(
        unit_name=unit_name
    )
    ((_, last_active_state),) = await unit_proxy.Get(property_name="ActiveState")
    await mqtt_client.publish(
        topic=active_state_topic, payload=last_active_state, retain=True
    )
    with dbus_router.filter(unit_properties_changed_match_rule) as queue:
        while True:
            await queue.get()
            ((_, current_active_state),) = await unit_proxy.Get(
                property_name="ActiveState"
            )
            if current_active_state != last_active_state:
                await mqtt_client.publish(
                    topic=active_state_topic, payload=current_active_state, retain=True
                )
                last_active_state = current_active_state
            queue.task_done()


async def _dbus_signal_loop(*, state: _State, mqtt_client: aiomqtt.Client) -> None:
    async with jeepney.io.asyncio.open_dbus_router(bus="SYSTEM") as router:
        # router: jeepney.io.asyncio.DBusRouter
        bus_proxy = jeepney.io.asyncio.Proxy(
            msggen=jeepney.bus_messages.message_bus, router=router
        )
        system_service_manager = jeepney.io.asyncio.Proxy(
            # pylint: disable=protected-access
            msggen=systemctl_mqtt._dbus.service_manager.ServiceManager(),
            router=router,
        )
        await asyncio.gather(
            *[
                _dbus_signal_loop_preparing_for_shutdown(
                    state=state,
                    mqtt_client=mqtt_client,
                    dbus_router=router,
                    bus_proxy=bus_proxy,
                )
            ]
            + [
                _dbus_signal_loop_unit(
                    state=state,
                    mqtt_client=mqtt_client,
                    dbus_router=router,
                    bus_proxy=bus_proxy,
                    unit_name=unit_name,
                    unit_path=await _get_unit_path(
                        service_manager=system_service_manager, unit_name=unit_name
                    ),
                )
                for unit_name in state.monitored_system_unit_names
            ],
            return_exceptions=False,
        )


async def _run(  # pylint: disable=too-many-arguments
    *,
    mqtt_host: str,
    mqtt_port: int,
    mqtt_username: str | None,
    mqtt_password: str | None,
    mqtt_topic_prefix: str,
    homeassistant_discovery_prefix: str,
    homeassistant_discovery_object_id: str,
    poweroff_delay: datetime.timedelta,
    monitored_system_unit_names: list[str],
    controlled_system_unit_names: list[str],
    mqtt_disable_tls: bool = False,
) -> None:
    state = _State(
        mqtt_topic_prefix=mqtt_topic_prefix,
        homeassistant_discovery_prefix=homeassistant_discovery_prefix,
        homeassistant_discovery_object_id=homeassistant_discovery_object_id,
        poweroff_delay=poweroff_delay,
        monitored_system_unit_names=monitored_system_unit_names,
        controlled_system_unit_names=controlled_system_unit_names,
    )
    _LOGGER.info(
        "connecting to MQTT broker %s:%d (TLS %s)",
        mqtt_host,
        mqtt_port,
        "disabled" if mqtt_disable_tls else "enabled",
    )
    if mqtt_password and not mqtt_username:
        raise ValueError("Missing MQTT username")
    async with aiomqtt.Client(  # raises aiomqtt.MqttError
        hostname=mqtt_host,
        port=mqtt_port,
        # > The settings [...] usually represent a higher security level than
        # > when calling the SSLContext constructor directly.
        # https://web.archive.org/web/20230714183106/https://docs.python.org/3/library/ssl.html
        tls_context=None if mqtt_disable_tls else ssl.create_default_context(),
        username=None if mqtt_username is None else mqtt_username,
        password=None if mqtt_password is None else mqtt_password,
        will=aiomqtt.Will(  # e.g. on SIGTERM & SIGKILL
            topic=state.mqtt_availability_topic,
            payload=_MQTT_PAYLOAD_NOT_AVAILABLE,
            retain=True,
        ),
    ) as mqtt_client:
        _LOGGER.debug("connected to MQTT broker %s:%d", mqtt_host, mqtt_port)
        if not state.shutdown_lock_acquired:
            state.acquire_shutdown_lock()
        await state.publish_homeassistant_device_config(mqtt_client=mqtt_client)
        await state.publish_preparing_for_shutdown(mqtt_client=mqtt_client)
        try:
            await mqtt_client.publish(
                topic=state.mqtt_availability_topic,
                payload=_MQTT_PAYLOAD_AVAILABLE,
                retain=True,
            )
            # asynpio.TaskGroup added in python3.11
            await asyncio.gather(
                _mqtt_message_loop(state=state, mqtt_client=mqtt_client),
                _dbus_signal_loop(state=state, mqtt_client=mqtt_client),
                return_exceptions=False,
            )
        finally:  # e.g. on SIGINT
            # https://web.archive.org/web/20250101080719/https://github.com/empicano/aiomqtt/issues/28
            await mqtt_client.publish(
                topic=state.mqtt_availability_topic,
                payload=_MQTT_PAYLOAD_NOT_AVAILABLE,
                retain=True,
            )


def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s:%(levelname)s:%(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    argparser = argparse.ArgumentParser(
        description="MQTT client triggering & reporting shutdown on systemd-based systems",
    )
    argparser.add_argument(
        "--log-level",
        choices=_ARGUMENT_LOG_LEVEL_MAPPING.keys(),
        default="info",
        help="log level (default: %(default)s)",
    )
    argparser.add_argument("--mqtt-host", type=str, required=True)
    argparser.add_argument(
        "--mqtt-port",
        type=int,
        help=f"default {_MQTT_DEFAULT_TLS_PORT} ({_MQTT_DEFAULT_PORT} with --mqtt-disable-tls)",
    )
    argparser.add_argument("--mqtt-username", type=str)
    argparser.add_argument("--mqtt-disable-tls", action="store_true")
    password_argument_group = argparser.add_mutually_exclusive_group()
    password_argument_group.add_argument("--mqtt-password", type=str)
    password_argument_group.add_argument(
        "--mqtt-password-file",
        type=pathlib.Path,
        metavar="PATH",
        dest="mqtt_password_path",
        help="stripping trailing newline",
    )
    argparser.add_argument(
        "--mqtt-topic-prefix",
        type=str,
        # pylint: disable=protected-access
        default="systemctl/" + systemctl_mqtt._utils.get_hostname(),
        help="default: %(default)s",
    )
    # https://www.home-assistant.io/docs/mqtt/discovery/#discovery_prefix
    argparser.add_argument(
        "--homeassistant-discovery-prefix",
        type=str,
        default="homeassistant",
        help="home assistant's prefix for discovery topics" + " (default: %(default)s)",
    )
    argparser.add_argument(
        "--homeassistant-discovery-object-id",
        type=str,
        # pylint: disable=protected-access
        default=systemctl_mqtt._homeassistant.get_default_discovery_object_id(),
        help="part of discovery topic (default: %(default)s)",
    )
    argparser.add_argument(
        "--poweroff-delay-seconds", type=float, default=4.0, help="default: %(default)s"
    )
    argparser.add_argument(
        "--monitor-system-unit",
        type=str,
        metavar="UNIT_NAME",
        dest="monitored_system_unit_names",
        action="append",
        help="e.g. --monitor-system-unit ssh.service --monitor-system-unit custom.service",
    )
    argparser.add_argument(
        "--control-system-unit",
        type=str,
        metavar="UNIT_NAME",
        dest="controlled_system_unit_names",
        action="append",
        help="e.g. --control-system-unit ansible-pull.service --control-system-unit custom.service",
    )
    args = argparser.parse_args()
    logging.root.setLevel(_ARGUMENT_LOG_LEVEL_MAPPING[args.log_level])
    if args.mqtt_port:
        mqtt_port = args.mqtt_port
    elif args.mqtt_disable_tls:
        mqtt_port = _MQTT_DEFAULT_PORT
    else:
        mqtt_port = _MQTT_DEFAULT_TLS_PORT
    if args.mqtt_password_path:
        # .read_text() replaces \r\n with \n
        mqtt_password = args.mqtt_password_path.read_bytes().decode()
        if mqtt_password.endswith("\r\n"):
            mqtt_password = mqtt_password[:-2]
        elif mqtt_password.endswith("\n"):
            mqtt_password = mqtt_password[:-1]
    else:
        mqtt_password = args.mqtt_password
    # pylint: disable=protected-access
    if not systemctl_mqtt._homeassistant.validate_discovery_object_id(
        args.homeassistant_discovery_object_id
    ):
        raise ValueError(
            # pylint: disable=protected-access
            "invalid home assistant discovery object id"
            f" {args.homeassistant_discovery_object_id!r} (length >= 1"
            ", allowed characters:"
            f" {systemctl_mqtt._homeassistant.NODE_ID_ALLOWED_CHARS})"
            "\nchange --homeassistant-discovery-object-id"
        )
    asyncio.run(
        _run(
            mqtt_host=args.mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_disable_tls=args.mqtt_disable_tls,
            mqtt_username=args.mqtt_username,
            mqtt_password=mqtt_password,
            mqtt_topic_prefix=args.mqtt_topic_prefix,
            homeassistant_discovery_prefix=args.homeassistant_discovery_prefix,
            homeassistant_discovery_object_id=args.homeassistant_discovery_object_id,
            poweroff_delay=datetime.timedelta(seconds=args.poweroff_delay_seconds),
            monitored_system_unit_names=args.monitored_system_unit_names or [],
            controlled_system_unit_names=args.controlled_system_unit_names or [],
        )
    )
