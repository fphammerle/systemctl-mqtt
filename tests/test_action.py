import datetime
import unittest.mock

import pytest

import systemctl_mqtt

# pylint: disable=protected-access


@pytest.mark.parametrize(
    "delay", [datetime.timedelta(seconds=4), datetime.timedelta(hours=21)]
)
def test_poweroff_trigger(delay):
    action = systemctl_mqtt._MQTTActionSchedulePoweroff()
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.schedule_shutdown"
    ) as schedule_shutdown_mock:
        action.trigger(
            state=systemctl_mqtt._State(
                mqtt_topic_prefix="systemctl/hostname",
                homeassistant_discovery_prefix="homeassistant",
                homeassistant_node_id="node",
                poweroff_delay=delay,
            )
        )
    schedule_shutdown_mock.assert_called_once_with(action="poweroff", delay=delay)
