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
