# sync with https://github.com/fphammerle/switchbot-mqtt/blob/master/Makefile

DOCKER_IMAGE_NAME := docker.io/fphammerle/systemctl-mqtt
DOCKER_TAG_VERSION := $(shell git describe --match=v* --abbrev=0 --dirty | sed -e 's/^v//')
DOCKER_TAG = ${DOCKER_TAG_VERSION}-armv6

.PHONY: docker-build docker-push

podman-build:
	podman build --format docker -t "${DOCKER_IMAGE_NAME}:${DOCKER_TAG}" .

docker-push: docker-build
	sudo docker push "${DOCKER_IMAGE_NAME}:${DOCKER_TAG}"
	@echo git tag --sign --message '$(shell sudo docker image inspect --format '{{join .RepoDigests "\n"}}' "${DOCKER_IMAGE_NAME}:${DOCKER_TAG}")' docker/${DOCKER_TAG} $(shell git rev-parse HEAD)
