# sync with https://github.com/fphammerle/switchbot-mqtt/blob/master/Makefile

IMAGE_NAME = docker.io/fphammerle/systemctl-mqtt
BUILD_VERSION = $(shell git describe --match=v* --dirty | sed -e 's/^v//')
ARCH = $(shell arch)
# architecture[arm_variant]
# https://github.com/opencontainers/image-spec/blob/v1.0.1/image-index.md#image-index-property-descriptions
IMAGE_TAG_ARCH_aarch64 = arm64
IMAGE_TAG_ARCH_armv6l = armv6
IMAGE_TAG_ARCH_armv7l = armv7
IMAGE_TAG_ARCH_x86_64 = amd64
IMAGE_TAG_ARCH = ${IMAGE_TAG_ARCH_${ARCH}}
IMAGE_TAG = ${BUILD_VERSION}-${IMAGE_TAG_ARCH}

.PHONY: docker-build docker-push

docker-build:
	sudo docker build -t "${IMAGE_NAME}:${IMAGE_TAG}" .

docker-push: docker-build
	sudo docker push "${IMAGE_NAME}:${IMAGE_TAG}"
	@echo git tag --sign --message '$(shell sudo docker image inspect --format '{{join .RepoDigests "\n"}}' "${IMAGE_NAME}:${IMAGE_TAG}")' docker/${IMAGE_TAG} $(shell git rev-parse HEAD)
