# sync with https://github.com/fphammerle/switchbot-mqtt/blob/master/Makefile

IMAGE_NAME = docker.io/fphammerle/systemctl-mqtt
PROJECT_VERSION = $(shell git describe --match=v* --dirty | sed -e 's/^v//')
ARCH = $(shell arch)
# architecture[arm_variant]
# https://github.com/opencontainers/image-spec/blob/v1.0.1/image-index.md#image-index-property-descriptions
IMAGE_TAG_ARCH_aarch64 = arm64
IMAGE_TAG_ARCH_armv6l = armv6
IMAGE_TAG_ARCH_armv7l = armv7
IMAGE_TAG_ARCH_x86_64 = amd64
IMAGE_TAG_ARCH = ${IMAGE_TAG_ARCH_${ARCH}}
IMAGE_TAG = ${PROJECT_VERSION}-${IMAGE_TAG_ARCH}

.PHONY: docker-build podman-build docker-push

docker-build:
	sudo docker build --tag="${IMAGE_NAME}:${IMAGE_TAG}" .

podman-build:
	# --format=oci (default) not fully supported by hub.docker.com
	# https://github.com/docker/hub-feedback/issues/1871#issuecomment-748924149
	podman build --format=docker --tag="${IMAGE_NAME}:${IMAGE_TAG}" .

docker-push: docker-build
	sudo docker push "${IMAGE_NAME}:${IMAGE_TAG}"
	@echo git tag --sign --message '$(shell sudo docker image inspect --format '{{join .RepoDigests "\n"}}' "${IMAGE_NAME}:${IMAGE_TAG}")' docker/${IMAGE_TAG} $(shell git rev-parse HEAD)
