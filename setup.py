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
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Home Automation",
    ],
    entry_points={
        "console_scripts": [
            "systemctl-mqtt = systemctl_mqtt:_main",
        ]
    },
    # >=3.6 variable type hints, f-strings & * to force keyword-only arguments
    # >=3.8 importlib.metadata
    python_requires=">=3.9",  # <3.9 untested
    # > Currently, the only main loop supported by dbus-python is GLib.
    # https://web.archive.org/web/20241228081405/https://dbus.freedesktop.org/doc/dbus-python/tutorial.html#setting-up-an-event-loop
    # PyGObject depends on pycairo
    # > When pip-installing systemctl-mqtt on a system without graphics it
    # > fails as pycairo fails building.
    # https://web.archive.org/web/20241228083145/https://github.com/fphammerle/systemctl-mqtt/issues/39
    # > Jeepney is a pure Python D-Bus module. It consists of an IO-free core
    # > implementing the protocol, and integrations for both blocking I/O and
    # > for different asynchronous frameworks.
    # https://web.archive.org/web/20241206000411/https://www.freedesktop.org/wiki/Software/DBusBindings/
    install_requires=["aiomqtt>=2,<3", "jeepney>=0.8,<1.0"],
    setup_requires=["setuptools_scm"],
    tests_require=["pytest"],
)
