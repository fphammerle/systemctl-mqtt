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

import unittest.mock

import pytest

import systemctl_mqtt._homeassistant

# pylint: disable=protected-access


@pytest.mark.parametrize(
    ("hostname", "expected_node_id"),
    [
        ("raspberrypi", "raspberrypi"),
        ("da-sh", "da-sh"),
        ("under_score", "under_score"),
        ("someone evil mocked the hostname", "someoneevilmockedthehostname"),
    ],
)
def test_get_default_node_id(hostname, expected_node_id):
    with unittest.mock.patch(
        "systemctl_mqtt._utils.get_hostname", return_value=hostname
    ):
        assert systemctl_mqtt._homeassistant.get_default_node_id() == expected_node_id


@pytest.mark.parametrize(
    ("node_id", "valid"),
    [
        ("raspberrypi", True),
        ("da-sh", True),
        ("under_score", True),
        ('" or ""="', False),
        ("", False),
    ],
)
def test_validate_node_id(node_id, valid):
    assert systemctl_mqtt._homeassistant.validate_node_id(node_id) == valid
