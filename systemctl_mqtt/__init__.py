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

import argparse
import datetime
import functools
import logging
import pathlib
import socket
import typing

import dbus

import paho.mqtt.client

_LOGGER = logging.getLogger(__name__)

_SHUTDOWN_DELAY = datetime.timedelta(seconds=4)


def _get_login_manager() -> dbus.proxies.Interface:
    # https://dbus.freedesktop.org/doc/dbus-python/tutorial.html
    bus = dbus.SystemBus()
    proxy: dbus.proxies.ProxyObject = bus.get_object(
        bus_name="org.freedesktop.login1", object_path="/org/freedesktop/login1"
    )
    # https://freedesktop.org/wiki/Software/systemd/logind/
    return dbus.Interface(object=proxy, dbus_interface="org.freedesktop.login1.Manager")


def _schedule_shutdown(action: str) -> None:
    # https://github.com/systemd/systemd/blob/v237/src/systemctl/systemctl.c#L8553
    assert action in ["poweroff", "reboot"], action
    shutdown_datetime = datetime.datetime.now() + _SHUTDOWN_DELAY
    _LOGGER.info(
        "scheduling %s for %s",
        action,
        shutdown_datetime.isoformat(sep=" ", timespec="seconds"),
    )
    shutdown_epoch_usec = int(shutdown_datetime.timestamp() * 10 ** 6)
    try:
        # $ gdbus introspect --system --dest org.freedesktop.login1 \
        #       --object-path /org/freedesktop/login1 | grep -A 1 ScheduleShutdown
        # ScheduleShutdown(in  s arg_0,
        #                  in  t arg_1);
        # $ gdbus call --system --dest org.freedesktop.login1 \
        #       --object-path /org/freedesktop/login1 \
        #       --method org.freedesktop.login1.Manager.ScheduleShutdown \
        #       poweroff "$(date --date=10min +%s)000000"
        # $ dbus-send --type=method_call --print-reply --system --dest=org.freedesktop.login1 \
        #       /org/freedesktop/login1 \
        #       org.freedesktop.login1.Manager.ScheduleShutdown \
        #       string:poweroff "uint64:$(date --date=10min +%s)000000"
        _get_login_manager().ScheduleShutdown(action, shutdown_epoch_usec)
    except dbus.DBusException as exc:
        exc_msg = exc.get_dbus_message()
        if "authentication required" in exc_msg.lower():
            _LOGGER.error(
                "failed to schedule %s: unauthorized; missing polkit authorization rules?",
                action,
            )
        else:
            _LOGGER.error("failed to schedule %s: %s", action, exc_msg)


_MQTT_TOPIC_SUFFIX_ACTION_MAPPING = {
    "poweroff": functools.partial(_schedule_shutdown, action="poweroff"),
}


class _Settings:
    # pylint: disable=too-few-public-methods
    def __init__(self, mqtt_topic_prefix: str) -> None:
        self.mqtt_topic_action_mapping: typing.Dict[str, typing.Callable] = {}
        for topic_suffix, action in _MQTT_TOPIC_SUFFIX_ACTION_MAPPING.items():
            topic = mqtt_topic_prefix + "/" + topic_suffix
            self.mqtt_topic_action_mapping[topic] = action


def _mqtt_on_connect(
    mqtt_client: paho.mqtt.client.Client,
    settings: _Settings,
    flags: typing.Dict,
    return_code: int,
) -> None:
    # pylint: disable=unused-argument; callback
    # https://github.com/eclipse/paho.mqtt.python/blob/v1.5.0/src/paho/mqtt/client.py#L441
    assert return_code == 0, return_code  # connection accepted
    mqtt_broker_host, mqtt_broker_port = mqtt_client.socket().getpeername()
    _LOGGER.debug("connected to MQTT broker %s:%d", mqtt_broker_host, mqtt_broker_port)
    for topic in settings.mqtt_topic_action_mapping.keys():
        _LOGGER.debug("subscribing to %s", topic)
        mqtt_client.subscribe(topic)


def _mqtt_on_message(
    mqtt_client: paho.mqtt.client.Client,
    settings: _Settings,
    message: paho.mqtt.client.MQTTMessage,
) -> None:
    # pylint: disable=unused-argument; callback
    # https://github.com/eclipse/paho.mqtt.python/blob/v1.5.0/src/paho/mqtt/client.py#L469
    _LOGGER.debug("received topic=%s payload=%r", message.topic, message.payload)
    if message.retain:
        _LOGGER.info("ignoring retained message")
        return
    try:
        action = settings.mqtt_topic_action_mapping[message.topic]
    except KeyError:
        _LOGGER.warning("unexpected topic %s", message.topic)
        return
    _LOGGER.debug("executing action %r", action)
    action()
    _LOGGER.debug("completed action %r", action)


def _run(
    mqtt_host: str,
    mqtt_port: int,
    mqtt_username: typing.Optional[str],
    mqtt_password: typing.Optional[str],
    mqtt_topic_prefix: str,
) -> None:
    # https://pypi.org/project/paho-mqtt/
    mqtt_client = paho.mqtt.client.Client(
        userdata=_Settings(mqtt_topic_prefix=mqtt_topic_prefix)
    )
    mqtt_client.on_connect = _mqtt_on_connect
    mqtt_client.on_message = _mqtt_on_message
    mqtt_client.tls_set(ca_certs=None)  # enable tls trusting default system certs
    _LOGGER.info(
        "connecting to MQTT broker %s:%d", mqtt_host, mqtt_port,
    )
    if mqtt_username:
        mqtt_client.username_pw_set(username=mqtt_username, password=mqtt_password)
    elif mqtt_password:
        raise ValueError("Missing MQTT username")
    mqtt_client.connect(host=mqtt_host, port=mqtt_port)
    mqtt_client.loop_forever()


def _get_hostname() -> str:
    return socket.gethostname()


def _main() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s:%(levelname)s:%(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    argparser = argparse.ArgumentParser(
        description="MQTT client triggering shutdown on systemd-based systems",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    argparser.add_argument("--mqtt-host", type=str, required=True)
    argparser.add_argument("--mqtt-port", type=int, default=8883)
    argparser.add_argument("--mqtt-username", type=str)
    password_argument_group = argparser.add_mutually_exclusive_group()
    password_argument_group.add_argument("--mqtt-password", type=str)
    password_argument_group.add_argument(
        "--mqtt-password-file",
        type=pathlib.Path,
        metavar="PATH",
        dest="mqtt_password_path",
        help="stripping trailing newline",
    )
    # https://www.home-assistant.io/docs/mqtt/discovery/#discovery_prefix
    argparser.add_argument(
        "--mqtt-topic-prefix",
        type=str,
        default="systemctl/" + _get_hostname(),
        help=" ",  # show default
    )
    args = argparser.parse_args()
    if args.mqtt_password_path:
        # .read_text() replaces \r\n with \n
        mqtt_password = args.mqtt_password_path.read_bytes().decode()
        if mqtt_password.endswith("\r\n"):
            mqtt_password = mqtt_password[:-2]
        elif mqtt_password.endswith("\n"):
            mqtt_password = mqtt_password[:-1]
    else:
        mqtt_password = args.mqtt_password
    _run(
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        mqtt_username=args.mqtt_username,
        mqtt_password=mqtt_password,
        mqtt_topic_prefix=args.mqtt_topic_prefix,
    )
