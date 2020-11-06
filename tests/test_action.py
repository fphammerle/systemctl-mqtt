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


@pytest.mark.parametrize(
    ("topic_suffix", "expected_action_arg"), [("poweroff", "poweroff")]
)
def test_mqtt_topic_suffix_action_mapping_poweroff(topic_suffix, expected_action_arg):
    mqtt_action = systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING[topic_suffix]
    login_manager_mock = unittest.mock.MagicMock()
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager", return_value=login_manager_mock
    ):
        mqtt_action.trigger(
            state=systemctl_mqtt._State(
                mqtt_topic_prefix="systemctl/hostname",
                homeassistant_discovery_prefix="homeassistant",
                homeassistant_node_id="node",
                poweroff_delay=datetime.timedelta(),
            )
        )
    assert login_manager_mock.ScheduleShutdown.call_count == 1
    schedule_args, schedule_kwargs = login_manager_mock.ScheduleShutdown.call_args
    assert len(schedule_args) == 2
    assert schedule_args[0] == expected_action_arg
    assert not schedule_kwargs


def test_mqtt_topic_suffix_action_mapping_lock():
    mqtt_action = systemctl_mqtt._MQTT_TOPIC_SUFFIX_ACTION_MAPPING["lock-all-sessions"]
    login_manager_mock = unittest.mock.MagicMock()
    with unittest.mock.patch(
        "systemctl_mqtt._dbus.get_login_manager", return_value=login_manager_mock
    ):
        mqtt_action.trigger(state="dummy")
    login_manager_mock.LockSessions.assert_called_once_with()
