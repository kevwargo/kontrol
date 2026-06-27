.PHONY: build-pacman install-pacman install

MAKEPKG_BUILD_FLAGS := --force

ifeq ($(GITHUB_ACTIONS),true)
MAKEPKG_BUILD_FLAGS += --nodeps
endif

build-pacman:
	tar -caf pacman/kontrol_src.tar.zst --exclude-vcs --exclude-vcs-ignores --exclude pacman .
	makepkg --dir pacman $(MAKEPKG_BUILD_FLAGS)

install-pacman:
	makepkg --dir pacman --install --noconfirm

install:
	install -Dm755 kwinctl/kwinctl.py /usr/bin/kwinctl
	install -Dm755 kwinctl/kwinjs-inspect.py /usr/bin/kwinjs-inspect
	install -Dm644 kwinctl/kwinctl.service /usr/lib/systemd/user/kwinctl.service
	install -Dm644 kwinctl/kwinctl.js /usr/share/kwinctl/script.js
	install -Dm644 kwinctl/rules.yaml /usr/share/kwinctl/rules.yaml
	install -Dm755 konsctl/konsctl.py /usr/bin/konsctl
