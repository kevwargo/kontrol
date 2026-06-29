.PHONY: build-pacman install-pacman install

build-pacman:
	tar -caf pacman/kontrol_src.tar.zst --exclude-vcs --exclude-vcs-ignores --exclude pacman .
	makepkg --dir pacman --force $(EXTRA_MAKEPKG_BUILD_FLAGS)

install-pacman:
	makepkg --dir pacman --install --noconfirm

install:
	install  -Dm755  kwinctl/kwinctl.py         /usr/bin/kwinctl
	install  -Dm755  kwinctl/kwinjs-inspect.py  /usr/bin/kwinjs-inspect
	install  -Dm644  kwinctl/kwinctl.service    /usr/lib/systemd/user/kwinctl.service
	install  -Dm644  kwinctl/kwinctl.js         /usr/share/kwinctl/kwinctl.js
	install  -Dm644  kwinctl/rules.yaml         /usr/share/kwinctl/rules.yaml
	install  -Dm644  kwinctl/commands.yaml      /usr/share/kwinctl/commands.yaml
	install  -Dm644  kwinctl/overrides.yaml     /usr/share/kwinctl/overrides.yaml
	install  -Dm755  kombi/kombi.py      		/usr/bin/kombi
	install  -Dm755  konsctl/konsctl.py         /usr/bin/konsctl
	install  -Dm755  kscreen-toggle-laptop.sh   /usr/bin/kscreen-toggle-laptop
