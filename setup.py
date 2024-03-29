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

import pathlib

import setuptools

_REPO_URL = "https://github.com/fphammerle/systemctl-mqtt"

setuptools.setup(
    name="systemctl-mqtt",
    use_scm_version={
        # > AssertionError: cant parse version docker/0.1.0-amd64
        # https://github.com/pypa/setuptools_scm/blob/master/src/setuptools_scm/git.py#L15
        "git_describe_command": "git describe --dirty --tags --long --match v*",
    },
    packages=setuptools.find_packages(),
    description="MQTT client triggering & reporting shutdown on systemd-based systems",
    long_description=pathlib.Path(__file__).parent.joinpath("README.md").read_text(),
    long_description_content_type="text/markdown",
    author="Fabian Peter Hammerle",
    author_email="fabian@hammerle.me",
    url=_REPO_URL,
    project_urls={"Changelog": _REPO_URL + "/blob/master/CHANGELOG.md"},
    license="GPLv3+",
    keywords=[
        "IoT",
        "automation",
        "home-assistant",
        "home-automation",
        "lock",
        "mqtt",
        "shutdown",
        "systemd",
    ],
    classifiers=[
        # https://pypi.org/classifiers/
        "Development Status :: 4 - Beta",
        "Intended Audience :: End Users/Desktop",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
        "Operating System :: POSIX :: Linux",
        # .github/workflows/python.yml
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Topic :: Home Automation",
    ],
    entry_points={
        "console_scripts": [
            "systemctl-mqtt = systemctl_mqtt:_main",
        ]
    },
    # >=3.6 variable type hints, f-strings & * to force keyword-only arguments
    python_requires=">=3.8",  # python<3.8 untested
    # https://dbus.freedesktop.org/doc/dbus-python/news.html
    install_requires=["PyGObject<4", "dbus-python<2", "paho-mqtt<2"],
    setup_requires=["setuptools_scm"],
    tests_require=["pytest"],
)
