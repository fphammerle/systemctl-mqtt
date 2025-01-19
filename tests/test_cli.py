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
import typing
import unittest.mock

import pytest

import systemctl_mqtt
import systemctl_mqtt._homeassistant
import systemctl_mqtt._utils

# pylint: disable=protected-access,too-many-positional-arguments


@pytest.mark.parametrize(
    ("args", "log_level"),
    [
        ([], logging.INFO),
        (["--log-level", "debug"], logging.DEBUG),
        (["--log-level", "info"], logging.INFO),
        (["--log-level", "warning"], logging.WARNING),
        (["--log-level", "error"], logging.ERROR),
        (["--log-level", "critical"], logging.CRITICAL),
    ],
)
def test__main_log_level(args: typing.List[str], log_level: int) -> None:
    with unittest.mock.patch("systemctl_mqtt._run") as run_mock, unittest.mock.patch(
        "sys.argv", ["", "--mqtt-host", "mqtt-broker.local"] + args
    ):
        systemctl_mqtt._main()
    run_mock.assert_called_once()
    assert logging.root.getEffectiveLevel() == log_level


@pytest.mark.parametrize(
    (
        "argv",
        "expected_mqtt_host",
        "expected_mqtt_port",
        "expected_mqtt_disable_tls",
        "expected_username",
        "expected_password",
        "expected_topic_prefix",
    ),
    [
        (
            ["", "--mqtt-host", "mqtt-broker.local"],
            "mqtt-broker.local",
            8883,
            False,
            None,
            None,
            None,
        ),
        (
            ["", "--mqtt-host", "mqtt-broker.local", "--mqtt-disable-tls"],
            "mqtt-broker.local",
            1883,
            True,
            None,
            None,
            None,
        ),
        (
            ["", "--mqtt-host", "mqtt-broker.local", "--mqtt-port", "8883"],
            "mqtt-broker.local",
            8883,
            False,
            None,
            None,
            None,
        ),
        (
            ["", "--mqtt-host", "mqtt-broker.local", "--mqtt-port", "8884"],
            "mqtt-broker.local",
            8884,
            False,
            None,
            None,
            None,
        ),
        (
            [
                "",
                "--mqtt-host",
                "mqtt-broker.local",
                "--mqtt-port",
                "8884",
                "--mqtt-disable-tls",
            ],
            "mqtt-broker.local",
            8884,
            True,
            None,
            None,
            None,
        ),
        (
            ["", "--mqtt-host", "mqtt-broker.local", "--mqtt-username", "me"],
            "mqtt-broker.local",
            8883,
            False,
            "me",
            None,
            None,
        ),
        (
            [
                "",
                "--mqtt-host",
                "mqtt-broker.local",
                "--mqtt-username",
                "me",
                "--mqtt-password",
                "secret",
            ],
            "mqtt-broker.local",
            8883,
            False,
            "me",
            "secret",
            None,
        ),
        (
            [
                "",
                "--mqtt-host",
                "mqtt-broker.local",
                "--mqtt-topic-prefix",
                "system/command",
            ],
            "mqtt-broker.local",
            8883,
            False,
            None,
            None,
            "system/command",
        ),
    ],
)
def test__main(
    argv,
    expected_mqtt_host,
    expected_mqtt_port,
    expected_mqtt_disable_tls,
    expected_username,
    expected_password,
    expected_topic_prefix: typing.Optional[str],
):
    # pylint: disable=too-many-arguments
    with unittest.mock.patch("systemctl_mqtt._run") as run_mock, unittest.mock.patch(
        "sys.argv", argv
    ), unittest.mock.patch(
        "systemctl_mqtt._utils.get_hostname", return_value="hostname"
    ):
        # pylint: disable=protected-access
        systemctl_mqtt._main()
    run_mock.assert_called_once_with(
        mqtt_host=expected_mqtt_host,
        mqtt_port=expected_mqtt_port,
        mqtt_disable_tls=expected_mqtt_disable_tls,
        mqtt_username=expected_username,
        mqtt_password=expected_password,
        mqtt_topic_prefix=expected_topic_prefix or "systemctl/hostname",
        homeassistant_discovery_prefix="homeassistant",
        homeassistant_discovery_object_id="systemctl-mqtt-hostname",
        poweroff_delay=datetime.timedelta(seconds=4),
        monitored_system_unit_names=[],
        controlled_system_unit_names=[],
    )


@pytest.mark.parametrize(
    ("password_file_content", "expected_password"),
    [
        ("secret", "secret"),
        ("secret space", "secret space"),
        ("secret   ", "secret   "),
        ("  secret ", "  secret "),
        ("secret\n", "secret"),
        ("secret\n\n", "secret\n"),
        ("secret\r\n", "secret"),
        ("secret\n\r\n", "secret\n"),
        ("你好\n", "你好"),
    ],
)
def test__main_password_file(tmpdir, password_file_content, expected_password):
    mqtt_password_path = tmpdir.join("mqtt-password")
    with mqtt_password_path.open("w") as mqtt_password_file:
        mqtt_password_file.write(password_file_content)
    with unittest.mock.patch("systemctl_mqtt._run") as run_mock, unittest.mock.patch(
        "sys.argv",
        [
            "",
            "--mqtt-host",
            "localhost",
            "--mqtt-username",
            "me",
            "--mqtt-password-file",
            str(mqtt_password_path),
        ],
    ), unittest.mock.patch(
        "systemctl_mqtt._utils.get_hostname", return_value="hostname"
    ):
        # pylint: disable=protected-access
        systemctl_mqtt._main()
    run_mock.assert_called_once_with(
        mqtt_host="localhost",
        mqtt_port=8883,
        mqtt_disable_tls=False,
        mqtt_username="me",
        mqtt_password=expected_password,
        mqtt_topic_prefix="systemctl/hostname",
        homeassistant_discovery_prefix="homeassistant",
        homeassistant_discovery_object_id="systemctl-mqtt-hostname",
        poweroff_delay=datetime.timedelta(seconds=4),
        monitored_system_unit_names=[],
        controlled_system_unit_names=[],
    )


def test__main_password_file_collision(capsys):
    with unittest.mock.patch(
        "sys.argv",
        [
            "",
            "--mqtt-host",
            "localhost",
            "--mqtt-username",
            "me",
            "--mqtt-password",
            "secret",
            "--mqtt-password-file",
            "/var/lib/secrets/mqtt/password",
        ],
    ):
        with pytest.raises(SystemExit):
            # pylint: disable=protected-access
            systemctl_mqtt._main()
    out, err = capsys.readouterr()
    assert not out
    assert (
        "argument --mqtt-password-file: not allowed with argument --mqtt-password\n"
        in err
    )


@pytest.mark.parametrize(
    ("args", "discovery_prefix"),
    [
        ([], "homeassistant"),
        (["--homeassistant-discovery-prefix", "home/assistant"], "home/assistant"),
    ],
)
def test__main_homeassistant_discovery_prefix(args, discovery_prefix):
    with unittest.mock.patch("systemctl_mqtt._run") as run_mock, unittest.mock.patch(
        "sys.argv", ["", "--mqtt-host", "mqtt-broker.local"] + args
    ):
        systemctl_mqtt._main()
    run_mock.assert_called_once()
    assert run_mock.call_args[1]["homeassistant_discovery_prefix"] == discovery_prefix


@pytest.mark.parametrize(
    ("args", "object_id"),
    [
        ([], "systemctl-mqtt-fallback"),
        (["--homeassistant-discovery-object-id", "raspberrypi"], "raspberrypi"),
    ],
)
def test__main_homeassistant_discovery_object_id(args, object_id):
    with unittest.mock.patch("systemctl_mqtt._run") as run_mock, unittest.mock.patch(
        "sys.argv", ["", "--mqtt-host", "mqtt-broker.local"] + args
    ), unittest.mock.patch(
        "systemctl_mqtt._utils.get_hostname", return_value="fallback"
    ):
        systemctl_mqtt._main()
    run_mock.assert_called_once()
    assert run_mock.call_args[1]["homeassistant_discovery_object_id"] == object_id


@pytest.mark.parametrize(
    "args",
    [
        ["--homeassistant-discovery-object-id", "no pe"],
        ["--homeassistant-discovery-object-id", ""],
    ],
)
def test__main_homeassistant_discovery_object_id_invalid(args):
    with unittest.mock.patch(
        "sys.argv", ["", "--mqtt-host", "mqtt-broker.local"] + args
    ):
        with pytest.raises(ValueError):
            systemctl_mqtt._main()


@pytest.mark.parametrize(
    ("args", "poweroff_delay"),
    [
        ([], datetime.timedelta(seconds=4)),
        (["--poweroff-delay-seconds", "42.21"], datetime.timedelta(seconds=42.21)),
        (["--poweroff-delay-seconds", "3600"], datetime.timedelta(hours=1)),
    ],
)
def test__main_poweroff_delay(args, poweroff_delay):
    with unittest.mock.patch("systemctl_mqtt._run") as run_mock, unittest.mock.patch(
        "sys.argv", ["", "--mqtt-host", "mqtt-broker.local"] + args
    ):
        systemctl_mqtt._main()
    run_mock.assert_called_once()
    assert run_mock.call_args[1]["poweroff_delay"] == poweroff_delay
