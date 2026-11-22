# Contributing

When contributing to this repository, please first discuss the change you wish to make via issue with the owners of this repository before making a change.

## Pull Request Process

1. Ensure any install or build dependencies are removed before the end of the layer when doing a
   build.
2. Update the README.md with details of changes to the interface, this includes new environment
   variables, exposed ports, useful file locations and container parameters.
3. Update the `CHANGELOG.md` file with your changes under the `[Unreleased]` section.

## Development Setup

1.  Install `pipenv`.
2.  Install dependencies with `pipenv install --dev`.

## Running Checks

Before submitting a pull request, please run the following checks to ensure your code is clean and passes all tests.

### Code Formatting

Run `black` to format your code:

```bash
pipenv run black .
```

### Linting

Run `pylint` to check for code style issues:

```bash
pipenv run pylint systemctl_mqtt
pipenv run pylint tests/*
```

### Static Type Checking

Run `mypy` to check for type errors:

```bash
pipenv run mypy systemctl_mqtt tests
```

### Testing

Run `pytest` to run the test suite:

```bash
pipenv run pytest --cov=systemctl_mqtt --cov-report=term-missing --cov-fail-under=100
```
