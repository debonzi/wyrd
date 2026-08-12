.DEFAULT_GOAL := help

.PHONY: help editable release

help:
	@printf '%s\n' \
		'make editable  Install wyrd from this checkout in editable mode' \
		'make release   Install the latest wyrd-cli release from PyPI'

editable:
	uv tool install --force --editable .

release:
	uv tool install --force --upgrade wyrd-cli
