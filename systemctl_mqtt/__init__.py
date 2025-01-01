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

import systemctl_mqtt._dbus
import systemctl_mqtt._homeassistant
import systemctl_mqtt._mqtt

_MQTT_DEFAULT_PORT = 1883
_MQTT_DEFAULT_TLS_PORT = 8883
_ARGUMENT_LOG_LEVEL_MAPPING = {
    a: getattr(logging, a.upper())
    for a in ("debug", "info", "warning", "error", "critical")
}

_LOGGER = logging.getLogger(__name__)


class _State:
    def __init__(
        self,
        *,
        mqtt_topic_prefix: str,
        homeassistant_discovery_prefix: str,
        homeassistant_discovery_object_id: str,
        poweroff_delay: datetime.timedelta,
    ) -> None:
        self._mqtt_topic_prefix = mqtt_topic_prefix
        self._homeassistant_discovery_prefix = homeassistant_discovery_prefix
        self._homeassistant_discovery_object_id = homeassistant_discovery_object_id
        self._login_manager = systemctl_mqtt._dbus.get_login_manager_proxy()
        self._shutdown_lock: typing.Optional[jeepney.fds.FileDescriptor] = None
        self._shutdown_lock_mutex = threading.Lock()
        self.poweroff_delay = poweroff_delay

    @property
    def mqtt_topic_prefix(self) -> str:
        return self._mqtt_topic_prefix

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
        await mqtt_client.publish(topic=topic, payload=payload, retain=False)

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
        _LOGGER.debug("publishing home assistant config on %s", discovery_topic)
        await mqtt_client.publish(
            topic=discovery_topic, payload=json.dumps(config), retain=False
        )


class _MQTTAction(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def trigger(self, state: _State) -> None:
        pass  # pragma: no cover

    def __str__(self) -> str:
        return type(self).__name__


class _MQTTActionSchedulePoweroff(_MQTTAction):
    # pylint: disable=too-few-public-methods
    def trigger(self, state: _State) -> None:
        # pylint: disable=protected-access
        systemctl_mqtt._dbus.schedule_shutdown(
            action="poweroff", delay=state.poweroff_delay
        )


class _MQTTActionLockAllSessions(_MQTTAction):
    # pylint: disable=too-few-public-methods
    def trigger(self, state: _State) -> None:
        # pylint: disable=protected-access
        systemctl_mqtt._dbus.lock_all_sessions()


class _MQTTActionSuspend(_MQTTAction):
    # pylint: disable=too-few-public-methods
    def trigger(self, state: _State) -> None:
        # pylint: disable=protected-access
        systemctl_mqtt._dbus.suspend()


_MQTT_TOPIC_SUFFIX_ACTION_MAPPING = {
    "poweroff": _MQTTActionSchedulePoweroff(),
    "lock-all-sessions": _MQTTActionLockAllSessions(),
    "suspend": _MQTTActionSuspend(),
}


async def _mqtt_message_loop(*, state: _State, mqtt_client: aiomqtt.Client) -> None:
    action_by_topic: typing.Dict[str, _MQTTAction] = {}
    for topic_suffix, action in _MQTT_TOPIC_SUFFIX_ACTION_MAPPING.items():
        topic = state.mqtt_topic_prefix + "/" + topic_suffix
        _LOGGER.info("subscribing to %s", topic)
        await mqtt_client.subscribe(topic)
        action_by_topic[topic] = action
    async for message in mqtt_client.messages:
        if message.retain:
            _LOGGER.info("ignoring retained message on topic %r", message.topic.value)
        else:
            _LOGGER.debug(
                "received message on topic %r: %r", message.topic.value, message.payload
            )
            action_by_topic[message.topic.value].trigger(state=state)


async def _dbus_signal_loop(*, state: _State, mqtt_client: aiomqtt.Client) -> None:
    async with jeepney.io.asyncio.open_dbus_router(bus="SYSTEM") as router:
        # router: jeepney.io.asyncio.DBusRouter
        bus_proxy = jeepney.io.asyncio.Proxy(
            msggen=jeepney.bus_messages.message_bus, router=router
        )
        preparing_for_shutdown_match_rule = (
            # pylint: disable=protected-access
            systemctl_mqtt._dbus.get_login_manager_signal_match_rule(
                "PrepareForShutdown"
            )
        )
        assert await bus_proxy.AddMatch(preparing_for_shutdown_match_rule) == ()
        with router.filter(preparing_for_shutdown_match_rule) as queue:
            while True:
                message: jeepney.low_level.Message = await queue.get()
                (preparing_for_shutdown,) = message.body
                await state.preparing_for_shutdown_handler(
                    active=preparing_for_shutdown, mqtt_client=mqtt_client
                )
                queue.task_done()


async def _run(  # pylint: disable=too-many-arguments
    *,
    mqtt_host: str,
    mqtt_port: int,
    mqtt_username: typing.Optional[str],
    mqtt_password: typing.Optional[str],
    mqtt_topic_prefix: str,
    homeassistant_discovery_prefix: str,
    homeassistant_discovery_object_id: str,
    poweroff_delay: datetime.timedelta,
    mqtt_disable_tls: bool = False,
) -> None:
    state = _State(
        mqtt_topic_prefix=mqtt_topic_prefix,
        homeassistant_discovery_prefix=homeassistant_discovery_prefix,
        homeassistant_discovery_object_id=homeassistant_discovery_object_id,
        poweroff_delay=poweroff_delay,
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
    ) as mqtt_client:
        _LOGGER.debug("connected to MQTT broker %s:%d", mqtt_host, mqtt_port)
        if not state.shutdown_lock_acquired:
            state.acquire_shutdown_lock()
        await state.publish_homeassistant_device_config(mqtt_client=mqtt_client)
        await state.publish_preparing_for_shutdown(mqtt_client=mqtt_client)
        # asyncio.TaskGroup added in python3.11
        await asyncio.gather(
            _mqtt_message_loop(state=state, mqtt_client=mqtt_client),
            _dbus_signal_loop(state=state, mqtt_client=mqtt_client),
            return_exceptions=False,
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
        )
    )
