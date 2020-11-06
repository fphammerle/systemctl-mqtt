# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/fphammerle/systemctl-mqtt/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/fphammerle/systemctl-mqtt/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/fphammerle/systemctl-mqtt/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/fphammerle/systemctl-mqtt/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/fphammerle/systemctl-mqtt/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/fphammerle/systemctl-mqtt/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/fphammerle/systemctl-mqtt/releases/tag/v0.1.0
