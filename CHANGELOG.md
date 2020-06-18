# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/fphammerle/systemctl-mqtt/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/fphammerle/systemctl-mqtt/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/fphammerle/systemctl-mqtt/releases/tag/v0.1.0
