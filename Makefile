## Project-defined variables

ENV_MK := env.mk
GEN_ENV_MK := ./scripts/gen-env-mk.py
-include $(ENV_MK)

## Other variables

DISTDIR := dist
export PKG_SOURCE_DIST := $(PKG_NAME)-src-$(PKG_VERSION).tar.zst

PACMAN_DIR := pacman

RPM_DIR := rpm
RPMTOP := $(CURDIR)/$(RPM_DIR)
RPMBUILD_DEFINES := --define "_topdir $(RPMTOP)" \
	--define "kontrol_name $(PKG_NAME)" \
	--define "kontrol_version $(PKG_VERSION)" \
	--define "kontrol_description $(PKG_DESCRIPTION)" \
	--define "kontrol_src $(PKG_SOURCE_DIST)" \
	--define "qasync_whl $(QASYNC_WHEEL_FILENAME)"

## Common targets
.PHONY: dist-source

$(ENV_MK): pyproject.toml uv.lock $(GEN_ENV_MK)
	$(GEN_ENV_MK) $@

dist-source:
	uv sync
	mkdir -p $(DISTDIR)
	tar -caf $(DISTDIR)/$(PKG_SOURCE_DIST) \
		--exclude-vcs --exclude-vcs-ignores \
		--exclude configs --exclude $(PACMAN_DIR) --exclude $(RPM_DIR) \
		--transform="s|^\.|$(PKG_NAME)-$(PKG_VERSION)|" .

## Pacman targets
.PHONY: build-pacman install-pacman clean-pacman

build-pacman: dist-source
	cp $(DISTDIR)/$(PKG_SOURCE_DIST) $(PACMAN_DIR)/$(PKG_SOURCE_DIST)
	makepkg --dir $(PACMAN_DIR) --force $(EXTRA_MAKEPKG_BUILD_FLAGS)

install-pacman:
	makepkg --dir $(PACMAN_DIR) --install --noconfirm

clean-pacman:
	cd $(PACMAN_DIR) && rm -rf src pkg *.tar.zst

## RPM targets
.PHONY: build-rpm

build-rpm: dist-source $(RPM_DIR)/SOURCES/$(QASYNC_WHEEL_FILENAME)
	cp $(DISTDIR)/$(PKG_SOURCE_DIST) $(RPM_DIR)/SOURCES/$(PKG_SOURCE_DIST)
	rpmbuild $(RPMBUILD_DEFINES) --noclean --nodebuginfo -bb $(RPM_DIR)/SPEC/kontrol.spec

$(RPM_DIR)/SOURCES/$(QASYNC_WHEEL_FILENAME):
	mkdir -p $(RPM_DIR)/SOURCES
	curl -L $(QASYNC_WHEEL_URL) -o $@
	printf '%s  %s\n' $(QASYNC_WHEEL_SHA256) $@ | sha256sum --check

rpm-docker:
	cd docker && HOST_USER=$(shell id -u):$(shell id -g) docker compose run --rm --build rpm-builder
