.PHONY: build
build:
	tar -caf pacman/kontrol_src.tar.xz --exclude .git --exclude pacman .
	cd pacman && makepkg -f

.PHONY: install
install:
	cd pacman && makepkg -i
