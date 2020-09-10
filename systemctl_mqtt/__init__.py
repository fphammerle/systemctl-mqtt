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

import argparse
import datetime
import functools
import json
import logging
import os
import pathlib
import socket
import threading
import typing

import dbus
import dbus.mainloop.glib

# black keeps inserting a blank line above
# https://pygobject.readthedocs.io/en/latest/getting_started.html#ubuntu-logo-ubuntu-debian-logo-debian
import gi.repository.GLib  # pylint-import-requirements: imports=PyGObject
import paho.mqtt.client

import systemctl_mqtt._dbus
import systemctl_mqtt._homeassistant
import systemctl_mqtt._mqtt

_MQTT_DEFAULT_PORT = 1883
_MQTT_DEFAULT_TLS_PORT = 8883

_LOGGER = logging.getLogger(__name__)


class _State:
    def __init__(
        self,
        mqtt_topic_prefix: str,
        homeassistant_discovery_prefix: str,
        homeassistant_node_id: str,
    ) -> None:
        self._mqtt_topic_prefix = mqtt_topic_prefix
        self._homeassistant_discovery_prefix = homeassistant_discovery_prefix
        self._homeassistant_node_id = homeassistant_node_id
        self._login_manager = (
            systemctl_mqtt._dbus.get_login_manager()
        )  # type: dbus.proxies.Interface
        self._shutdown_lock = None  # type: typing.Optional[dbus.types.UnixFd]
        self._shutdown_lock_mutex = threading.Lock()

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
            self._shutdown_lock = self._login_manager.Inhibit(
                "shutdown", "systemctl-mqtt", "Report shutdown via MQTT", "delay",
            )
            _LOGGER.debug("acquired shutdown inhibitor lock")

    def release_shutdown_lock(self) -> None:
        with self._shutdown_lock_mutex:
            if self._shutdown_lock:
                # https://dbus.freedesktop.org/doc/dbus-python/dbus.types.html#dbus.types.UnixFd.take
                os.close(self._shutdown_lock.take())
                _LOGGER.debug("released shutdown inhibitor lock")
                self._shutdown_lock = None

    @property
    def _preparing_for_shutdown_topic(self) -> str:
        return self.mqtt_topic_prefix + "/preparing-for-shutdown"

    def _publish_preparing_for_shutdown(
        self, mqtt_client: paho.mqtt.client.Client, active: bool, block: bool,
    ) -> None:
        # https://github.com/eclipse/paho.mqtt.python/blob/v1.5.0/src/paho/mqtt/client.py#L1199
        topic = self._preparing_for_shutdown_topic
        # pylint: disable=protected-access
        payload = systemctl_mqtt._mqtt.encode_bool(active)
        _LOGGER.info("publishing %r on %s", payload, topic)
        msg_info = mqtt_client.publish(
            topic=topic, payload=payload, retain=True,
        )  # type: paho.mqtt.client.MQTTMessageInfo
        if not block:
            return
        msg_info.wait_for_publish()
        if msg_info.rc != paho.mqtt.client.MQTT_ERR_SUCCESS:
            _LOGGER.error(
                "failed to publish on %s (return code %d)", topic, msg_info.rc
            )

    def _prepare_for_shutdown_handler(
        self, active: dbus.Boolean, mqtt_client: paho.mqtt.client.Client
    ) -> None:
        assert isinstance(active, dbus.Boolean)
        active = bool(active)
        self._publish_preparing_for_shutdown(
            mqtt_client=mqtt_client, active=active, block=True,
        )
        if active:
            self.release_shutdown_lock()
        else:
            self.acquire_shutdown_lock()

    def register_prepare_for_shutdown_handler(
        self, mqtt_client: paho.mqtt.client.Client
    ) -> None:
        self._login_manager.connect_to_signal(
            signal_name="PrepareForShutdown",
            handler_function=functools.partial(
                self._prepare_for_shutdown_handler, mqtt_client=mqtt_client
            ),
        )

    def publish_preparing_for_shutdown(
        self, mqtt_client: paho.mqtt.client.Client,
    ) -> None:
        try:
            active = self._login_manager.Get(
                "org.freedesktop.login1.Manager",
                "PreparingForShutdown",
                dbus_interface="org.freedesktop.DBus.Properties",
            )
        except dbus.DBusException as exc:
            _LOGGER.error(
                "failed to read logind's PreparingForShutdown property: %s",
                exc.get_dbus_message(),
            )
            return
        assert isinstance(active, dbus.Boolean), active
        self._publish_preparing_for_shutdown(
            mqtt_client=mqtt_client,
            active=bool(active),
            # https://github.com/eclipse/paho.mqtt.python/issues/439#issuecomment-565514393
            block=False,
        )

    def publish_preparing_for_shutdown_homeassistant_config(
        self, mqtt_client: paho.mqtt.client.Client
    ) -> None:
        # <discovery_prefix>/<component>/[<node_id>/]<object_id>/config
        # https://www.home-assistant.io/docs/mqtt/discovery/
        discovery_topic = "/".join(
            (
                self._homeassistant_discovery_prefix,
                "binary_sensor",
                self._homeassistant_node_id,
                "preparing-for-shutdown",
                "config",
            )
        )
        unique_id = "/".join(
            (
                "systemctl-mqtt",
                self._homeassistant_node_id,
                "logind",
                "preparing-for-shutdown",
            )
        )
        # https://www.home-assistant.io/integrations/binary_sensor.mqtt/#configuration-variables
        config = {
            "unique_id": unique_id,
            "state_topic": self._preparing_for_shutdown_topic,
            # pylint: disable=protected-access
            "payload_on": systemctl_mqtt._mqtt.encode_bool(True),
            "payload_off": systemctl_mqtt._mqtt.encode_bool(False),
            # friendly_name & template for default entity_id
            "name": "{} preparing for shutdown".format(self._homeassistant_node_id),
        }
        _LOGGER.debug("publishing home assistant config on %s", discovery_topic)
        mqtt_client.publish(
            topic=discovery_topic, payload=json.dumps(config), retain=True,
        )


class _MQTTAction:

    # pylint: disable=too-few-public-methods

    def __init__(self, name: str, action: typing.Callable) -> None:
        self.name = name
        self.action = action

    def mqtt_message_callback(
        self,
        mqtt_client: paho.mqtt.client.Client,
        state: _State,
        message: paho.mqtt.client.MQTTMessage,
    ) -> None:
        # pylint: disable=unused-argument; callback
        # https://github.com/eclipse/paho.mqtt.python/blob/v1.5.0/src/paho/mqtt/client.py#L3416
        # https://github.com/eclipse/paho.mqtt.python/blob/v1.5.0/src/paho/mqtt/client.py#L469
        _LOGGER.debug("received topic=%s payload=%r", message.topic, message.payload)
        if message.retain:
            _LOGGER.info("ignoring retained message")
            return
        _LOGGER.debug("executing action %s (%r)", self.name, self.action)
        self.action()
        _LOGGER.debug("completed action %s (%r)", self.name, self.action)


_MQTT_TOPIC_SUFFIX_ACTION_MAPPING = {
    "poweroff": _MQTTAction(
        name="poweroff",
        action=functools.partial(
            # pylint: disable=protected-access
            systemctl_mqtt._dbus.schedule_shutdown,
            action="poweroff",
        ),
    ),
}


def _mqtt_on_connect(
    mqtt_client: paho.mqtt.client.Client,
    state: _State,
    flags: typing.Dict,
    return_code: int,
) -> None:
    # pylint: disable=unused-argument; callback
    # https://github.com/eclipse/paho.mqtt.python/blob/v1.5.0/src/paho/mqtt/client.py#L441
    assert return_code == 0, return_code  # connection accepted
    mqtt_broker_host, mqtt_broker_port = mqtt_client.socket().getpeername()
    _LOGGER.debug("connected to MQTT broker %s:%d", mqtt_broker_host, mqtt_broker_port)
    if not state.shutdown_lock_acquired:
        state.acquire_shutdown_lock()
    state.register_prepare_for_shutdown_handler(mqtt_client=mqtt_client)
    state.publish_preparing_for_shutdown(mqtt_client=mqtt_client)
    state.publish_preparing_for_shutdown_homeassistant_config(mqtt_client=mqtt_client)
    for topic_suffix, action in _MQTT_TOPIC_SUFFIX_ACTION_MAPPING.items():
        topic = state.mqtt_topic_prefix + "/" + topic_suffix
        _LOGGER.info("subscribing to %s", topic)
        mqtt_client.subscribe(topic)
        mqtt_client.message_callback_add(
            sub=topic, callback=action.mqtt_message_callback
        )
        _LOGGER.debug(
            "registered MQTT callback for topic %s triggering %r", topic, action.action,
        )


def _run(
    mqtt_host: str,
    mqtt_port: int,
    mqtt_username: typing.Optional[str],
    mqtt_password: typing.Optional[str],
    mqtt_topic_prefix: str,
    homeassistant_discovery_prefix: str,
    homeassistant_node_id: str,
    mqtt_disable_tls: bool = False,
) -> None:
    # pylint: disable=too-many-arguments
    # https://dbus.freedesktop.org/doc/dbus-python/tutorial.html#setting-up-an-event-loop
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    # https://pypi.org/project/paho-mqtt/
    mqtt_client = paho.mqtt.client.Client(
        userdata=_State(
            mqtt_topic_prefix=mqtt_topic_prefix,
            homeassistant_discovery_prefix=homeassistant_discovery_prefix,
            homeassistant_node_id=homeassistant_node_id,
        )
    )
    mqtt_client.on_connect = _mqtt_on_connect
    if not mqtt_disable_tls:
        mqtt_client.tls_set(ca_certs=None)  # enable tls trusting default system certs
    _LOGGER.info(
        "connecting to MQTT broker %s:%d (TLS %s)",
        mqtt_host,
        mqtt_port,
        "disabled" if mqtt_disable_tls else "enabled",
    )
    if mqtt_username:
        mqtt_client.username_pw_set(username=mqtt_username, password=mqtt_password)
    elif mqtt_password:
        raise ValueError("Missing MQTT username")
    mqtt_client.connect(host=mqtt_host, port=mqtt_port)
    # loop_start runs loop_forever in a new thread (daemon)
    # https://github.com/eclipse/paho.mqtt.python/blob/v1.5.0/src/paho/mqtt/client.py#L1814
    # loop_forever attempts to reconnect if disconnected
    # https://github.com/eclipse/paho.mqtt.python/blob/v1.5.0/src/paho/mqtt/client.py#L1744
    mqtt_client.loop_start()
    try:
        gi.repository.GLib.MainLoop().run()
    finally:
        # blocks until loop_forever stops
        _LOGGER.debug("waiting for MQTT loop to stop")
        mqtt_client.loop_stop()
        _LOGGER.debug("MQTT loop stopped")


def _main() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s:%(levelname)s:%(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    argparser = argparse.ArgumentParser(
        description="MQTT client triggering & reporting shutdown on systemd-based systems",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    argparser.add_argument("--mqtt-host", type=str, required=True)
    argparser.add_argument(
        "--mqtt-port",
        type=int,
        help="default {} ({} with --mqtt-disable-tls)".format(
            _MQTT_DEFAULT_TLS_PORT, _MQTT_DEFAULT_PORT
        ),
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
        help=" ",  # show default
    )
    # https://www.home-assistant.io/docs/mqtt/discovery/#discovery_prefix
    argparser.add_argument(
        "--homeassistant-discovery-prefix", type=str, default="homeassistant", help=" ",
    )
    argparser.add_argument(
        "--homeassistant-node-id",
        type=str,
        # pylint: disable=protected-access
        default=systemctl_mqtt._homeassistant.get_default_node_id(),
        help=" ",
    )
    args = argparser.parse_args()
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
    if not systemctl_mqtt._homeassistant.validate_node_id(args.homeassistant_node_id):
        raise ValueError(
            "invalid home assistant node id {!r} (length >= 1, allowed characters: {})".format(
                args.homeassistant_node_id,
                # pylint: disable=protected-access
                systemctl_mqtt._homeassistant.NODE_ID_ALLOWED_CHARS,
            )
            + "\nchange --homeassistant-node-id"
        )
    _run(
        mqtt_host=args.mqtt_host,
        mqtt_port=mqtt_port,
        mqtt_disable_tls=args.mqtt_disable_tls,
        mqtt_username=args.mqtt_username,
        mqtt_password=mqtt_password,
        mqtt_topic_prefix=args.mqtt_topic_prefix,
        homeassistant_discovery_prefix=args.homeassistant_discovery_prefix,
        homeassistant_node_id=args.homeassistant_node_id,
    )
