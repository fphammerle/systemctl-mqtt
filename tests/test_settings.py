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
