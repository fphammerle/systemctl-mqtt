DOCKER_IMAGE_NAME := fphammerle/systemctl-mqtt
DOCKER_TAG_VERSION := $(shell git describe --match=v* --dirty | sed -e 's/^v//')
ARCH := $(shell arch)
DOCKER_TAG_ARCH_SUFFIX_armv6l := armv6
DOCKER_TAG_ARCH_SUFFIX_x86_64 := amd64
DOCKER_TAG_ARCH_SUFFIX = ${DOCKER_TAG_ARCH_SUFFIX_${ARCH}}
DOCKER_TAG = ${DOCKER_TAG_VERSION}-${DOCKER_TAG_ARCH_SUFFIX}

.PHONY: docker-build docker-push

docker-build:
	sudo docker build -t "${DOCKER_IMAGE_NAME}:${DOCKER_TAG}" .

docker-push: docker-build
	sudo docker push "${DOCKER_IMAGE_NAME}:${DOCKER_TAG}"
