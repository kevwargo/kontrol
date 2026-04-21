.PHONY: build
build:
	tar -caf pacman/kontrol_src.tar.zst --exclude-vcs --exclude-vcs-ignores --exclude pacman .
	cd pacman && makepkg -f

.PHONY: install
install:
	cd pacman && makepkg -i
