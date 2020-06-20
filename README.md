# systemctl-mqtt

[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![CI Pipeline Status](https://github.com/fphammerle/systemctl-mqtt/workflows/tests/badge.svg)](https://github.com/fphammerle/systemctl-mqtt/actions)
[![Coverage Status](https://coveralls.io/repos/github/fphammerle/systemctl-mqtt/badge.svg?branch=master)](https://coveralls.io/github/fphammerle/systemctl-mqtt?branch=master)
[![Last Release](https://img.shields.io/pypi/v/systemctl-mqtt.svg)](https://pypi.org/project/systemctl-mqtt/#history)
[![Compatible Python Versions](https://img.shields.io/pypi/pyversions/systemctl-mqtt.svg)](https://pypi.org/project/systemctl-mqtt/)

MQTT client triggering & reporting shutdown on [systemd](https://freedesktop.org/wiki/Software/systemd/)-based systems

## Setup

```sh
$ pip3 install --user --upgrade systemctl-mqtt
$ systemctl-mqtt --mqtt-host HOSTNAME_OR_IP_ADDRESS
```

On debian-based systems, dependencies can optionally be installed via:
```sh
$ sudo apt-get install --no-install-recommends python3-dbus python3-gi python3-paho-mqtt
```

Schedule poweroff by sending a MQTT message to topic `systemctl/hostname/poweroff`.

```sh
$ mosquitto_pub -h MQTT_BROKER -t systemctl/hostname/poweroff -n
```

### Shutdown Report

`systemctl-mqtt` subscribes to [logind](https://freedesktop.org/wiki/Software/systemd/logind/)'s `PrepareForShutdown` signal.

`systemctl halt|poweroff|reboot` triggers a message with payload `true` on topic `systemctl/hostname/preparing-for-shutdown`.

## Home Assistant 🏡

### Sample Setup

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

## MQTT Authentication

```sh
systemctl-mqtt --mqtt-username me --mqtt-password secret …
# or
systemctl-mqtt --mqtt-username me --mqtt-password-file /var/lib/secrets/mqtt/password …
```
