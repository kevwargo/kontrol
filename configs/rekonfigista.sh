#!/bin/bash

kwriteconfig6 --file kxkbrc --group Layout --key Options "caps:super,shift:both_capslock"
kwriteconfig6 --file kxkbrc --group Layout --key ResetOldOptions true

kwriteconfig6 --file kglobalshortcutsrc --group kwin --key "Window to Next Screen" "Meta+Shift+Down,Meta+Shift+Right,Move Window to Next Screen"
kwriteconfig6 --file kglobalshortcutsrc --group kwin --key "Window to Previous Screen" "Meta+Shift+Up,Meta+Shift+Left,Move Window to Previous Screen"

pacman -S keyd
cat > /etc/keyd/default.conf <<EOF
[ids]
*

[main]
leftshift = leftshift
rightshift = rightshift
sysrq = layer(altgr)
insert = menu
rightalt = leftalt
EOF
systemctl enable --now keyd
