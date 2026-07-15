PYTHON ?= python3

.PHONY: test build twine-check clean release-check

test:
	$(PYTHON) -m pytest

build:
	$(PYTHON) -m build

twine-check:
	$(PYTHON) -m twine check dist/*

release-check: test clean build twine-check

clean:
	rm -rf build dist *.egg-info
