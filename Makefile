ENV_MK := env.mk
GEN_ENV_MK := ./scripts/gen-env-mk.py

-include $(ENV_MK)

RPMTOP := $(CURDIR)/rpm
RPM_SOURCE := $(PKG_NAME)-$(PKG_VERSION).tar.zst
RPMBUILD_DEFINES := --define "_topdir $(RPMTOP)" \
	--define "kontrol_name $(PKG_NAME)" \
	--define "kontrol_version $(PKG_VERSION)" \
	--define "kontrol_description $(PKG_DESCRIPTION)" \
	--define "kontrol_src $(RPM_SOURCE)" \
	--define "qasync_whl $(QASYNC_WHEEL_FILENAME)"

.PHONY: build-pacman install-pacman install build-rpm prepare-rpm-source install-rpmdeps

build-pacman:
	tar -caf pacman/kontrol_src.tar.zst --exclude-vcs --exclude-vcs-ignores --exclude pacman .
	makepkg --dir pacman --force $(EXTRA_MAKEPKG_BUILD_FLAGS)

clean-pacman:
	cd pacman && rm -rf src pkg *.tar.zst

install-pacman:
	makepkg --dir pacman --install --noconfirm

build-rpm: prepare-rpm-source $(RPMTOP)/SOURCES/$(QASYNC_WHEEL_FILENAME)
	rpmbuild $(RPMBUILD_DEFINES) --noclean --nodebuginfo -bb $(RPMTOP)/SPEC/kontrol.spec

prepare-rpm-source:
	mkdir -p $(RPMTOP)/SOURCES
	tar -caf $(RPMTOP)/SOURCES/$(RPM_SOURCE) \
		--exclude-vcs --exclude-vcs-ignores \
		--exclude configs --exclude pacman --exclude rpm \
		--transform="s|^\.|$(PKG_NAME)-$(PKG_VERSION)|" .

$(RPMTOP)/SOURCES/$(QASYNC_WHEEL_FILENAME):
	mkdir -p $(RPMTOP)/SOURCES
	curl -L $(QASYNC_WHEEL_URL) -o $@
	printf '%s  %s\n' $(QASYNC_WHEEL_SHA256) $@ | sha256sum --check

install-rpmdeps:
	-rpmbuild $(RPMBUILD_DEFINES) -br $(RPMTOP)/SPEC/kontrol.spec
	sudo dnf builddep $(RPMTOP)/SRPMS/$(PKG_NAME)-$(PKG_VERSION)-*.rpm

$(ENV_MK): pyproject.toml uv.lock $(GEN_ENV_MK)
	$(GEN_ENV_MK) $@
