# systemctl-mqtt

[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![CI Pipeline Status](https://github.com/fphammerle/systemctl-mqtt/workflows/tests/badge.svg)](https://github.com/fphammerle/systemctl-mqtt/actions)
![Coverage Status](https://ipfs.io/ipfs/QmP8k5H4MkfspFxQxdL2kEZ4QQWQjF8xwPYD35KvNH4CA6/20230429T090002+0200/s3.amazonaws.com/assets.coveralls.io/badges/coveralls_100.svg)
[![Last Release](https://img.shields.io/pypi/v/systemctl-mqtt.svg)](https://pypi.org/project/systemctl-mqtt/#history)
[![Compatible Python Versions](https://img.shields.io/pypi/pyversions/systemctl-mqtt.svg)](https://pypi.org/project/systemctl-mqtt/)
[![DOI](https://zenodo.org/badge/272405671.svg)](https://zenodo.org/badge/latestdoi/272405671)

MQTT client triggering & reporting shutdown on [systemd](https://freedesktop.org/wiki/Software/systemd/)-based systems

## Setup

### Via Pip

```sh
$ pip3 install --user --upgrade systemctl-mqtt
```

On debian-based systems, a subset of dependencies can optionally be installed via:
```sh
$ sudo apt-get install --no-install-recommends python3-jeepney python3-paho-mqtt
```

Follow instructions in [systemd-user.service](systemd-user.service) to start
systemctl-mqtt automatically via systemd.

### Via Docker Compose 🐳

1. Clone this repository.
2. Load [AppArmor](https://en.wikipedia.org/wiki/AppArmor) profile:
   `sudo apparmor_parser ./docker-apparmor-profile`
3. `sudo docker-compose up --build`

Pre-built docker image are available at https://hub.docker.com/r/fphammerle/systemctl-mqtt/tags

Annotation of signed tags `docker/*` contains docker image digests: https://github.com/fphammerle/systemctl-mqtt/tags

## Usage

```sh
$ systemctl-mqtt --mqtt-host HOSTNAME_OR_IP_ADDRESS
```

`systemctl-mqtt --help` explains all available command-line options / parameters.

### MQTT via TLS

TLS is enabled by default.
Run `systemctl-mqtt --mqtt-disable-tls …` to disable TLS.

### MQTT Authentication

```sh
systemctl-mqtt --mqtt-username me --mqtt-password-file /run/secrets/password …
# or for testing (unsafe):
systemctl-mqtt --mqtt-username me --mqtt-password secret …
```

### Schedule Poweroff

Schedule poweroff by sending a MQTT message to topic `systemctl/hostname/poweroff`.

```sh
$ mosquitto_pub -h MQTT_BROKER -t systemctl/hostname/poweroff -n
```

Adapt delay via: `systemctl-mqtt --poweroff-delay-seconds 60 …`

### Shutdown Report

`systemctl-mqtt` subscribes to [logind](https://freedesktop.org/wiki/Software/systemd/logind/)'s `PrepareForShutdown` signal.

`systemctl halt|poweroff|reboot` triggers a message with payload `true` on topic `systemctl/hostname/preparing-for-shutdown`.

### Lock Screen

Lock screen by sending a MQTT message to topic `systemctl/hostname/lock-all-sessions`.

```
$ mosquitto_pub -h MQTT_BROKER -t systemctl/hostname/lock-all-sessions -n
```

### Suspend

```
$ mosquitto_pub -h MQTT_BROKER -t systemctl/hostname/suspend -n
```

### Monitor `ActiveState` of System Units

```
$ systemctl-mqtt --monitor-system-unit foo.service
```
enables reports on topic
`systemctl/[hostname]/unit/system/[unit_name]/active-state`.

### Restarting of System Units

```
$ systemctl-mqtt  --control-system-unit <unit_name>
```
enables that a system unit can be started, stopped, and restarted by a message on topic
`systemctl/[hostname]/unit/system/[unit_name]/start`, `…/stop`, `…/restart`.

## Home Assistant 🏡

When [MQTT Discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery)
is enabled (default in Home Assistant ≥0.117.0), the following entities will be
added automatically:
- `binary_sensor.[hostname]_logind_preparing_for_shutdown`
- `button.[hostname]_logind_lock_all_sessions`
- `button.[hostname]_logind_poweroff`
- `button.[hostname]_logind_suspend`
- `sensor.[hostname]_unit_system_[unit_name]_active_state`
  for `--monitor-system-unit [unit_name]`
- `button.[hostname]_unit_system_[unit_name]_restart`
  for `--control-system-unit [unit_name]`

![homeassistant entities_over_auto_discovery](docs/homeassistant/entities-after-auto-discovery.png)

Pass `--homeassistant-discovery-prefix custom-prefix` to `systemctl-mqtt` when
using a custom discovery topic prefix.
