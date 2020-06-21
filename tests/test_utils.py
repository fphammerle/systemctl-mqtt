import unittest.mock

import pytest

import systemctl_mqtt._utils


@pytest.mark.parametrize("hostname", ["test"])
def test__get_hostname(hostname):
    with unittest.mock.patch("socket.gethostname", return_value=hostname):
        # pylint: disable=protected-access
        assert systemctl_mqtt._utils.get_hostname() == hostname
