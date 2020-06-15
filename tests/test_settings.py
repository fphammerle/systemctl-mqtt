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

import functools

import pytest

import systemctl_mqtt

# pylint: disable=protected-access


@pytest.mark.parametrize("mqtt_topic_prefix", ["systemctl/host", "system/command"])
def test_mqtt_topic_action_mapping(mqtt_topic_prefix):
    settings = systemctl_mqtt._Settings(mqtt_topic_prefix=mqtt_topic_prefix)
    assert len(settings.mqtt_topic_action_mapping) == 1
    action = settings.mqtt_topic_action_mapping[mqtt_topic_prefix + "/poweroff"]
    assert isinstance(action, functools.partial)
    # pylint: disable=comparison-with-callable
    assert action.func == systemctl_mqtt._schedule_shutdown
    assert not action.args
    assert action.keywords == {"action": "poweroff"}
