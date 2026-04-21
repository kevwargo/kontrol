.PHONY: build
build:
	tar -caf pacman/kontrol_src.tar.zst --exclude-vcs --exclude-vcs-ignores --exclude pacman .
	makepkg --dir pacman --force

.PHONY: install
install:
	makepkg --dir pacman --install --noconfirm
