# General

- Simplify build process - build the wheel instead of a source tarball in Makefile

# KWinCTL

## Rules

1. match multiple classes (or regexp) in a single rule

## Snippets: strip whitespace

```
<id>:
  snippet:
    cmd: ...
    whitespace:
      strip: bool
      strip-head: bool
      strip-tail: bool
      chars: (default ' \n\r\t')
```

# QKVox

- **fix**: BT devices don't switch to `[OFF]` in UI
- actually run `pactl set-default-sink` once previously inactive BT device comes online
- make loader last longer when connecting to a device with bt disabled (currently loader disappers quicker than needed - when adapter activates, while it should wait until the device in question appears)
- implement unmuting channels
- adapt for multiple adapters (xD)

# New stuff

- VPNs from /etc/wireguard
