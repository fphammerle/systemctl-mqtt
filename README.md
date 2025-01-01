# systemctl-mqtt

[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![CI Pipeline Status](https://github.com/fphammerle/systemctl-mqtt/workflows/tests/badge.svg)](https://github.com/fphammerle/systemctl-mqtt/actions)
![Coverage Status](https://ipfs.io/ipfs/QmP8k5H4MkfspFxQxdL2kEZ4QQWQjF8xwPYD35KvNH4CA6/20230429T090002+0200/s3.amazonaws.com/assets.coveralls.io/badges/coveralls_100.svg)
[![Last Release](https://img.shields.io/pypi/v/systemctl-mqtt.svg)](https://pypi.org/project/systemctl-mqtt/#history)
[![Compatible Python Versions](https://img.shields.io/pypi/pyversions/systemctl-mqtt.svg)](https://pypi.org/project/systemctl-mqtt/)
[![DOI](https://zenodo.org/badge/272405671.svg)](https://zenodo.org/badge/latestdoi/272405671)

MQTT client triggering & reporting shutdown on [systemd](https://freedesktop.org/wiki/Software/systemd/)-based systems

## Setup

```sh
$ pip3 install --user --upgrade systemctl-mqtt
$ systemctl-mqtt --mqtt-host HOSTNAME_OR_IP_ADDRESS
```

On debian-based systems, a subset of dependencies can optionally be installed via:
```sh
$ sudo apt-get install --no-install-recommends python3-jeepney python3-paho-mqtt
```

## Usage

### Schedule Poweroff

Schedule poweroff by sending a MQTT message to topic `systemctl/hostname/poweroff`.

```sh
$ mosquitto_pub -h MQTT_BROKER -t systemctl/hostname/poweroff -n
```

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

## Home Assistant 🏡

### Automatic Discovery

When [MQTT Discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery)
is enabled (default in Home Assistant ≥0.117.0), the following entities will be
added automatically:
- `binary_sensor.[hostname]_logind_preparing_for_shutdown`
- `button.[hostname]_logind_lock_all_sessions`
- `button.[hostname]_logind_poweroff`
- `button.[hostname]_logind_suspend`

![homeassistant entities_over_auto_discovery](docs/homeassistant/entities-after-auto-discovery.png)

Pass `--homeassistant-discovery-prefix custom-prefix` to `systemctl-mqtt` when
using a custom discovery topic prefix.

### Manual YAML Configuration

#### Send Poweroff Command

```yaml
# https://www.home-assistant.io/docs/mqtt/broker/#configuration-variables
mqtt:
  broker: BROKER_HOSTNAME_OR_IP_ADDRESS
  # credentials, additional options…

script:
  poweroff_raspberry_pi:
    sequence:
      service: mqtt.publish
      data:
        topic: systemctl/raspberrypi/poweroff

homeassistant:
  customize:
    script.poweroff_raspberry_pi:
      friendly_name: poweroff pi
      icon: mdi:power
```

#### Trigger Automation on Shutdown

```yaml
automation:
- trigger:
    platform: mqtt
    topic: systemctl/raspberrypi/preparing-for-shutdown
    payload: 'true'
  action:
    service: switch.turn_off
    entity_id: switch.desk_lamp
```

## Docker 🐳

1. Clone this repository.
2. Edit `docker-compose.yml`.
3. Load [AppArmor](https://en.wikipedia.org/wiki/AppArmor) profile:
   `sudo apparmor_parser ./docker-apparmor-profile`
4. `sudo docker-compose up --build`

Pre-built docker image are available at https://hub.docker.com/r/fphammerle/systemctl-mqtt/tags

Annotation of signed tags `docker/*` contains docker image digests: https://github.com/fphammerle/systemctl-mqtt/tags

## MQTT via TLS

TLS is enabled by default.
Run `systemctl-mqtt --mqtt-disable-tls …` to disable TLS.

## MQTT Authentication

```sh
systemctl-mqtt --mqtt-username me --mqtt-password-file /run/secrets/password …
# or for testing (unsafe):
systemctl-mqtt --mqtt-username me --mqtt-password secret …
```

## Adapt Poweroff Delay

```sh
systemctl-mqtt --poweroff-delay-seconds 60 …
```
