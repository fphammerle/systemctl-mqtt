# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- MQTT message on topic `systemctl/hostname/poweroff`
  schedules a poweroff via systemd's dbus interface (4 seconds delay)

[Unreleased]: https://github.com/fphammerle/systemctl-mqtt/tree/master
