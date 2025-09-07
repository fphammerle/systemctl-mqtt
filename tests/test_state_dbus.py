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
import json
import logging
import re
import typing
import unittest.mock

import jeepney.wrappers
import pytest

import systemctl_mqtt

# pylint: disable=protected-access


def test_shutdown_lock():
    lock_fd = unittest.mock.MagicMock(spec=jeepney.fds.FileDescriptor)
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.login_manager.get_login_manager_proxy"
    ) as get_login_manager_mock:
        state = systemctl_mqtt._State(
            mqtt_topic_prefix="any",
            homeassistant_discovery_prefix=None,
            homeassistant_discovery_object_id=None,
            poweroff_delay=datetime.timedelta(),
            monitored_system_unit_names=[],
            controlled_system_unit_names=[],
        )
        get_login_manager_mock.return_value.Inhibit.return_value = (lock_fd,)
        state.acquire_shutdown_lock()
    state._login_manager.Inhibit.assert_called_once_with(
        what="shutdown",
        who="systemctl-mqtt",
        why="Report shutdown via MQTT",
        mode="delay",
    )
    assert state._shutdown_lock == lock_fd
    lock_fd.close.assert_not_called()
    state.release_shutdown_lock()
    lock_fd.close.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [True, False])
async def test_preparing_for_shutdown_handler(active: bool) -> None:
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.login_manager.get_login_manager_proxy"
    ):
        state = systemctl_mqtt._State(
            mqtt_topic_prefix="any",
            homeassistant_discovery_prefix="pre/fix",
            homeassistant_discovery_object_id="obj",
            poweroff_delay=datetime.timedelta(),
            monitored_system_unit_names=[],
            controlled_system_unit_names=[],
        )
    mqtt_client_mock = unittest.mock.MagicMock()
    with unittest.mock.patch.object(
        state, "_publish_preparing_for_shutdown"
    ) as publish_mock, unittest.mock.patch.object(
        state, "acquire_shutdown_lock"
    ) as acquire_lock_mock, unittest.mock.patch.object(
        state, "release_shutdown_lock"
    ) as release_lock_mock:
        await state.preparing_for_shutdown_handler(
            active=active, mqtt_client=mqtt_client_mock
        )
    publish_mock.assert_awaited_once_with(mqtt_client=mqtt_client_mock, active=active)
    if active:
        acquire_lock_mock.assert_not_called()
        release_lock_mock.assert_called_once_with()
    else:
        acquire_lock_mock.assert_called_once_with()
        release_lock_mock.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [True, False])
async def test_publish_preparing_for_shutdown(active: bool) -> None:
    login_manager_mock = unittest.mock.MagicMock()
    login_manager_mock.Get.return_value = (("b", active),)[:]
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.login_manager.get_login_manager_proxy",
        return_value=login_manager_mock,
    ):
        state = systemctl_mqtt._State(
            mqtt_topic_prefix="any",
            homeassistant_discovery_prefix="pre/fix",
            homeassistant_discovery_object_id="obj",
            poweroff_delay=datetime.timedelta(),
            monitored_system_unit_names=[],
            controlled_system_unit_names=[],
        )
    assert state._login_manager == login_manager_mock
    mqtt_client_mock = unittest.mock.AsyncMock()
    await state.publish_preparing_for_shutdown(mqtt_client=mqtt_client_mock)
    login_manager_mock.Get.assert_called_once_with("PreparingForShutdown")
    mqtt_client_mock.publish.assert_awaited_once_with(
        topic="any/preparing-for-shutdown",
        payload="true" if active else "false",
        retain=False,
    )


class DBusErrorResponseMock(jeepney.wrappers.DBusErrorResponse):
    # pylint: disable=missing-class-docstring,super-init-not-called
    def __init__(self, name: str, data: typing.Any):
        self.name = name
        self.data = data


@pytest.mark.asyncio
async def test_publish_preparing_for_shutdown_get_fail(caplog):
    login_manager_mock = unittest.mock.MagicMock()
    login_manager_mock.Get.side_effect = DBusErrorResponseMock("error", ("mocked",))
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.login_manager.get_login_manager_proxy",
        return_value=login_manager_mock,
    ):
        state = systemctl_mqtt._State(
            mqtt_topic_prefix="any",
            homeassistant_discovery_prefix=None,
            homeassistant_discovery_object_id=None,
            poweroff_delay=datetime.timedelta(),
            monitored_system_unit_names=[],
            controlled_system_unit_names=[],
        )
    mqtt_client_mock = unittest.mock.MagicMock()
    await state.publish_preparing_for_shutdown(mqtt_client=None)
    mqtt_client_mock.publish.assert_not_called()
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.ERROR
    assert (
        caplog.records[0].message
        == "failed to read logind's PreparingForShutdown property: [error] ('mocked',)"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("topic_prefix", ["systemctl/hostname", "hostname/systemctl"])
@pytest.mark.parametrize("discovery_prefix", ["homeassistant", "home/assistant"])
@pytest.mark.parametrize("object_id", ["raspberrypi", "debian21"])
@pytest.mark.parametrize("hostname", ["hostname", "host-name"])
@pytest.mark.parametrize(
    ("monitored_system_unit_names", "controlled_system_unit_names"),
    [
        ([], []),
        (
            ["foo.service", "bar.service"],
            ["foo-control.service", "bar-control.service"],
        ),
    ],
)
async def test_publish_homeassistant_device_config(
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    topic_prefix: str,
    discovery_prefix: str,
    object_id: str,
    hostname: str,
    monitored_system_unit_names: typing.List[str],
    controlled_system_unit_names: typing.List[str],
) -> None:
    with unittest.mock.patch("jeepney.io.blocking.open_dbus_connection"):
        state = systemctl_mqtt._State(
            mqtt_topic_prefix=topic_prefix,
            homeassistant_discovery_prefix=discovery_prefix,
            homeassistant_discovery_object_id=object_id,
            poweroff_delay=datetime.timedelta(),
            monitored_system_unit_names=monitored_system_unit_names,
            controlled_system_unit_names=controlled_system_unit_names,
        )
    assert state.monitored_system_unit_names == monitored_system_unit_names
    assert state.controlled_system_unit_names == controlled_system_unit_names
    mqtt_client = unittest.mock.AsyncMock()
    with unittest.mock.patch(
        "systemctl_mqtt._utils.get_hostname", return_value=hostname
    ):
        await state.publish_homeassistant_device_config(mqtt_client=mqtt_client)
    mqtt_client.publish.assert_called_once()
    publish_args, publish_kwargs = mqtt_client.publish.call_args
    assert not publish_args
    assert not publish_kwargs["retain"]
    assert (
        publish_kwargs["topic"] == discovery_prefix + "/device/" + object_id + "/config"
    )
    config = json.loads(publish_kwargs["payload"])
    assert re.match(r"\d+\.\d+\.", config["origin"].pop("sw_version"))
    assert config == {
        "origin": {
            "name": "systemctl-mqtt",
            "support_url": "https://github.com/fphammerle/systemctl-mqtt",
        },
        "device": {"identifiers": [hostname], "name": hostname},
        "availability": {"topic": topic_prefix + "/status"},
        "components": {
            "logind/preparing-for-shutdown": {
                "unique_id": f"systemctl-mqtt-{hostname}-logind-preparing-for-shutdown",
                "object_id": f"{hostname}_logind_preparing_for_shutdown",
                "name": "preparing for shutdown",
                "platform": "binary_sensor",
                "state_topic": topic_prefix + "/preparing-for-shutdown",
                "payload_on": "true",
                "payload_off": "false",
            },
            "logind/poweroff": {
                "unique_id": f"systemctl-mqtt-{hostname}-logind-poweroff",
                "object_id": f"{hostname}_logind_poweroff",
                "name": "poweroff",
                "platform": "button",
                "command_topic": f"{topic_prefix}/poweroff",
            },
            "logind/lock-all-sessions": {
                "unique_id": f"systemctl-mqtt-{hostname}-logind-lock-all-sessions",
                "object_id": f"{hostname}_logind_lock_all_sessions",
                "name": "lock all sessions",
                "platform": "button",
                "command_topic": f"{topic_prefix}/lock-all-sessions",
            },
            "logind/suspend": {
                "unique_id": f"systemctl-mqtt-{hostname}-logind-suspend",
                "object_id": f"{hostname}_logind_suspend",
                "name": "suspend",
                "platform": "button",
                "command_topic": f"{topic_prefix}/suspend",
            },
        }
        | {
            f"unit/system/{n}/active-state": {
                "unique_id": f"systemctl-mqtt-{hostname}-unit-system-{n}-active-state",
                "object_id": f"{hostname}_unit_system_{n}_active_state",
                "name": f"{n} active state",
                "platform": "sensor",
                "state_topic": f"{topic_prefix}/unit/system/{n}/active-state",
            }
            for n in monitored_system_unit_names
        }
        | {
            f"unit/system/{n}/{action}": {
                "unique_id": f"systemctl-mqtt-{hostname}-unit-system-{n}-{action}",
                "object_id": f"{hostname}_unit_system_{n}_{action}",
                "name": f"{n} {action}",
                "platform": "button",
                "command_topic": f"{topic_prefix}/unit/system/{n}/{action}",
            }
            for n in controlled_system_unit_names
            for action in ["restart", "start", "stop"]
        },
    }
