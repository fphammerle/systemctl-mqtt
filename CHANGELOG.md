# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2025-09-07
## Added
- support jeepney v0.9
- ability to start/stop system units using `--control-system-unit <unit_name>`
  ([#223](https://github.com/fphammerle/systemctl-mqtt/pull/223), [@nougad](https://github.com/nougad))

## [1.1.0] - 2025-01-19
### Added
- ability to restart system units using `--control_system_unit <unit_name>`
  ([#180](https://github.com/fphammerle/systemctl-mqtt/pull/180)
  by Hanspeter Gosteli (hanspeter.gosteli@gmail.com))

### Documentation
- added systemd user service config for autostart
  ([#66](https://github.com/fphammerle/systemctl-mqtt/issues/66))

## [1.0.0] - 2025-01-04
### Added
- suspend when receiving message on topic `systemctl/[hostname]/suspend`
  (https://github.com/fphammerle/systemctl-mqtt/issues/97)
- birth & last will message on topic `systemctl/[hostname]/status`
  ("online" / "offline", https://github.com/fphammerle/systemctl-mqtt/issues/38)
- command-line option `--monitor-system-unit [unit_name]` enables reports on
  topic `systemctl/[hostname]/unit/system/[unit_name]/active-state`
  (https://github.com/fphammerle/systemctl-mqtt/issues/56)
- automatic discovery in home assistant:
  - availability status
  - entity `button.[hostname]_logind_lock_all_sessions`
  - entity `button.[hostname]_logind_poweroff`
  - entity `button.[hostname]_logind_suspend`
  - entity `sensor.[hostname]_unit_system_[unit_name]_active_state`
    for each command-line parameter `--monitor-system-unit [unit_name]`
- command-line option `--log-level {debug,info,warning,error,critical}`
- suggest polkit rule when poweroff & locking fails due to
  `org.freedesktop.DBus.Error.InteractiveAuthorizationRequired`
  (https://github.com/fphammerle/systemctl-mqtt/issues/67)
- declare compatibility with `python3.11`, `python3.12` & `python3.13`

### Changed
- disable `retain` flag on topic `systemctl/[hostname]/preparing-for-shutdown`
- migrated from [dbus-python](https://gitlab.freedesktop.org/dbus/dbus-python/)
  to pure-python [jeepney](https://gitlab.com/takluyver/jeepney)
  (removes indirect dependency on libdbus, glib,
  [PyGObject](https://gitlab.gnome.org/GNOME/pygobject) and
  [pycairo](https://github.com/pygobject/pycairo),
  fixes https://github.com/fphammerle/systemctl-mqtt/issues/39)
- migrate from [paho-mqtt](https://github.com/eclipse/paho.mqtt.python) to its
  async wrapper [aiomqtt](https://github.com/sbtinstruments/aiomqtt)
- automatic discovery in home assistant:
  - replaced component-based (topic:
    `<discovery_prefix>/binary_sensor/<node_id>/preparing-for-shutdown/config`)
    with device-based discovery (`<discovery_prefix>/device/<object_id>/config`)
  - replaced command-line option `--homeassistant-node-id` with
    `--homeassistant-discovery-object-id`
  - renamed entity `binary_sensor.[hostname]_preparing_for_shutdown` to
    `binary_sensor.[hostname]_logind_preparing_for_shutdown`
  - disabled "retain" flag for discovery messages
    (to avoid reappearing ghost devices)
- container image / dockerfile:
  - upgraded alpine base image from 3.13.1 to 3.21.0 including upgrade of python
    from 3.8 to 3.12
  - support build without git history by manually setting build argument
    `SETUPTOOLS_SCM_PRETEND_VERSION`
- changed default log level from `debug` to `info`

### Fixed
- apparmor profile for architectures other than x86_64/amd64
  (`ImportError: Error loading [...]/_gi.cpython-38-aarch64-linux-gnu.so: Permission denied`)
- fatal `org.freedesktop.DBus.Error.InteractiveAuthorizationRequired` when
  attempting to lock sessions
- container image / dockerfile:
  - split `pipenv install` into two stages to speed up image builds
  - `chmod` files copied from host to no longer require `o=rX` perms on host
  - added registry to base image specifier for `podman build`
  - added `--force` flag to `rm` invocation to avoid interactive questions while
    running `podman build`

### Removed
- compatibility with `python3.5`, `python3.6`, `python3.7` & `python3.8`

### Internal
- pipeline:
  - build container image for armv6 & arm64 (in addition to amd64 & armv7)
  - push container images to ghcr.io
  - create tags & labels for container image via
    [docker/metadata-action](https://github.com/docker/metadata-action)

## [0.5.0] - 2020-11-06
### Added
- MQTT message on topic `systemctl/hostname/lock-all-sessions`
  instructs all sessions to activate screen locks
  (functionally equivalent to command `loginctl lock-sessions`)
- command line option `--poweroff-delay-seconds` (default: 4 seconds)

### Changed
- docker image:
  - upgrade `paho-mqtt` to no longer suppress exceptions occuring in mqtt callbacks
    ( https://github.com/eclipse/paho.mqtt.python/blob/v1.5.1/ChangeLog.txt#L4 )
  - build stage: revert user after applying `chown` workaround for inter-stage copy

## [0.4.0] - 2020-09-10
### Added
- command line option `--mqtt-disable-tls`

## [0.3.0] - 2020-06-21
### Added
- home assistant: enable [automatic discovery](https://www.home-assistant.io/docs/mqtt/discovery/#discovery_prefix)
  for logind's `PreparingForShutdown` signal

### Fixed
- fatal error on MQTT reconnect:
  tried to re-acquire shutdown inhibitor lock

## [0.2.0] - 2020-06-21
### Added
- forward logind's [PreparingForShutdown](https://www.freedesktop.org/wiki/Software/systemd/inhibit/)
  to `systemctl/hostname/preparing-for-shutdown`
- log [inhibitor locks](https://www.freedesktop.org/wiki/Software/systemd/inhibit/)
  when scheduling a shutdown

### Fixed
- explicit timestamp type specification to avoid
  `OverflowError: Python int too large to convert to C long`

## [0.1.1] - 2020-06-18
### Fixed
- compatibility with python3.5:
  - replaced [PEP526](https://www.python.org/dev/peps/pep-0526/#abstract)-style variable type hints
    with [PEP484](https://www.python.org/dev/peps/pep-0484/)-compatible
  - fixed `AttributeError` due to unavailable `MagicMock.assert_called_once`
  - fixed `TypeError` when calling `datetime.datetime.isoformat(datespec=…)`

## [0.1.0] - 2020-06-16
### Added
- MQTT message on topic `systemctl/hostname/poweroff`
  schedules a poweroff via systemd's dbus interface (4 seconds delay)

[Unreleased]: https://github.com/fphammerle/systemctl-mqtt/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/fphammerle/systemctl-mqtt/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/fphammerle/systemctl-mqtt/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/fphammerle/systemctl-mqtt/compare/v0.5.0...v1.0.0
[0.5.0]: https://github.com/fphammerle/systemctl-mqtt/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/fphammerle/systemctl-mqtt/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/fphammerle/systemctl-mqtt/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/fphammerle/systemctl-mqtt/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/fphammerle/systemctl-mqtt/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/fphammerle/systemctl-mqtt/releases/tag/v0.1.0
