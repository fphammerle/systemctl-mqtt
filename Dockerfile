# not using python:3.*-alpine cause glib-dev package depends on python3
# https://pkgs.alpinelinux.org/package/v3.11/main/aarch64/glib-dev
ARG BASE_IMAGE=alpine:3.11
ARG SOURCE_DIR_PATH=/systemctl-mqtt


# hadolint ignore=DL3006
FROM $BASE_IMAGE as build

RUN apk add --no-cache \
        cairo-dev `# PyGObject > pycairo` \
        dbus-dev \
        gcc \
        git `# setuptools_scm` \
        glib-dev `# dbus-python` \
        gobject-introspection-dev `# PyGObject` \
        make `# dbus-python` \
        musl-dev `# dbus-python` \
        py3-certifi `# pipenv` \
        py3-virtualenv `# pipenv` \
        python3-dev `# dbus-python` \
    && adduser -S build

USER build
RUN pip3 install --user --no-cache-dir pipenv==2020.6.2

ARG SOURCE_DIR_PATH
COPY --chown=build:1234 . $SOURCE_DIR_PATH
WORKDIR $SOURCE_DIR_PATH
ENV PIPENV_CACHE_DIR=/tmp/pipenv-cache \
    PIPENV_VENV_IN_PROJECT=yes-please \
    PATH=/home/build/.local/bin:$PATH
RUN pipenv install --deploy --verbose \
    && pipenv graph \
    && pipenv run pip freeze \
    && rm -r .git/ $PIPENV_CACHE_DIR

# workaround for broken multi-stage copy
# > failed to copy files: failed to copy directory: Error processing tar file(exit status 1): Container ID ... cannot be mapped to a host ID
USER 0
RUN chown -R 0:0 $SOURCE_DIR_PATH
USER build


# hadolint ignore=DL3006
FROM $BASE_IMAGE

RUN apk add --no-cache \
        ca-certificates \
        dbus-libs \
        glib `# PyGObject` \
        gobject-introspection `# PyGObject` \
        python3 \
        tini \
    && find / -xdev -type f -perm /u+s -exec chmod -c u-s {} \; \
    && find / -xdev -type f -perm /g+s -exec chmod -c g-s {} \;

USER nobody

ARG SOURCE_DIR_PATH
COPY --from=build $SOURCE_DIR_PATH $SOURCE_DIR_PATH
ARG VIRTUALENV_PATH=$SOURCE_DIR_PATH/.venv
ENV PATH=$VIRTUALENV_PATH/bin:$PATH
ENTRYPOINT ["tini", "--"]
CMD ["systemctl-mqtt", "--help"]
