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
import dbus.types

# black keeps inserting a blank line above
# https://pygobject.readthedocs.io/en/latest/getting_started.html#ubuntu-logo-ubuntu-debian-logo-debian
import gi.repository.GLib  # pylint-import-requirements: imports=PyGObject
import paho.mqtt.client

_LOGGER = logging.getLogger(__name__)

_SHUTDOWN_DELAY = datetime.timedelta(seconds=4)


def _get_login_manager() -> dbus.proxies.Interface:
    # https://dbus.freedesktop.org/doc/dbus-python/tutorial.html
    bus = dbus.SystemBus()
    proxy = bus.get_object(
        bus_name="org.freedesktop.login1", object_path="/org/freedesktop/login1"
    )  # type: dbus.proxies.ProxyObject
    # https://freedesktop.org/wiki/Software/systemd/logind/
    return dbus.Interface(object=proxy, dbus_interface="org.freedesktop.login1.Manager")


def _log_shutdown_inhibitors(login_manager: dbus.proxies.Interface) -> None:
    if _LOGGER.getEffectiveLevel() > logging.DEBUG:
        return
    found_inhibitor = False
    try:
        # https://www.freedesktop.org/wiki/Software/systemd/inhibit/
        for what, who, why, mode, uid, pid in login_manager.ListInhibitors():
            if "shutdown" in what:
                found_inhibitor = True
                _LOGGER.debug(
                    "detected shutdown inhibitor %s (pid=%u, uid=%u, mode=%s): %s",
                    who,
                    pid,
                    uid,
                    mode,
                    why,
                )
    except dbus.DBusException as exc:
        _LOGGER.warning(
            "failed to fetch shutdown inhibitors: %s", exc.get_dbus_message()
        )
        return
    if not found_inhibitor:
        _LOGGER.debug("no shutdown inhibitor locks found")


def _schedule_shutdown(action: str) -> None:
    # https://github.com/systemd/systemd/blob/v237/src/systemctl/systemctl.c#L8553
    assert action in ["poweroff", "reboot"], action
    shutdown_datetime = datetime.datetime.now() + _SHUTDOWN_DELAY
    # datetime.datetime.isoformat(timespec=) not available in python3.5
    # https://github.com/python/cpython/blob/v3.5.9/Lib/datetime.py#L1552
    _LOGGER.info(
        "scheduling %s for %s", action, shutdown_datetime.strftime("%Y-%m-%d %H:%M:%S"),
    )
    # https://dbus.freedesktop.org/doc/dbus-python/tutorial.html?highlight=signature#basic-types
    shutdown_epoch_usec = dbus.UInt64(shutdown_datetime.timestamp() * 10 ** 6)
    login_manager = _get_login_manager()
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
        login_manager.ScheduleShutdown(action, shutdown_epoch_usec)
    except dbus.DBusException as exc:
        exc_msg = exc.get_dbus_message()
        if "authentication required" in exc_msg.lower():
            _LOGGER.error(
                "failed to schedule %s: unauthorized; missing polkit authorization rules?",
                action,
            )
        else:
            _LOGGER.error("failed to schedule %s: %s", action, exc_msg)
    _log_shutdown_inhibitors(login_manager)


class _State:
    def __init__(self, mqtt_topic_prefix: str) -> None:
        self._mqtt_topic_prefix = mqtt_topic_prefix
        self._login_manager = _get_login_manager()  # type: dbus.proxies.Interface
        self._shutdown_lock = None  # type: typing.Optional[dbus.types.UnixFd]
        self._shutdown_lock_mutex = threading.Lock()

    @property
    def mqtt_topic_prefix(self) -> str:
        return self._mqtt_topic_prefix

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

    def _publish_preparing_for_shutdown(
        self, mqtt_client: paho.mqtt.client.Client, active: bool, block: bool,
    ) -> None:
        # https://github.com/eclipse/paho.mqtt.python/blob/v1.5.0/src/paho/mqtt/client.py#L1199
        topic = self.mqtt_topic_prefix + "/preparing-for-shutdown"
        payload = json.dumps(active)
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
        name="poweroff", action=functools.partial(_schedule_shutdown, action="poweroff")
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
    state.acquire_shutdown_lock()
    state.register_prepare_for_shutdown_handler(mqtt_client=mqtt_client)
    state.publish_preparing_for_shutdown(mqtt_client=mqtt_client)
    for topic_suffix, action in _MQTT_TOPIC_SUFFIX_ACTION_MAPPING.items():
        topic = state.mqtt_topic_prefix + "/" + topic_suffix
        _LOGGER.info("subscribing to %s", topic)
        mqtt_client.subscribe(topic)
        mqtt_client.message_callback_add(
            sub=topic, callback=action.mqtt_message_callback
        )
        _LOGGER.debug(
            "registered MQTT callback for topic %s triggering %r", topic, action.action
        )


def _run(
    mqtt_host: str,
    mqtt_port: int,
    mqtt_username: typing.Optional[str],
    mqtt_password: typing.Optional[str],
    mqtt_topic_prefix: str,
) -> None:
    # https://dbus.freedesktop.org/doc/dbus-python/tutorial.html#setting-up-an-event-loop
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    # https://pypi.org/project/paho-mqtt/
    mqtt_client = paho.mqtt.client.Client(
        userdata=_State(mqtt_topic_prefix=mqtt_topic_prefix)
    )
    mqtt_client.on_connect = _mqtt_on_connect
    mqtt_client.tls_set(ca_certs=None)  # enable tls trusting default system certs
    _LOGGER.info(
        "connecting to MQTT broker %s:%d", mqtt_host, mqtt_port,
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


def _get_hostname() -> str:
    return socket.gethostname()


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
