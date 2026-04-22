#!/bin/bash

enabled=$(kscreen-doctor -j | jq -r '.outputs[]|select(.name=="eDP-1").enabled')
if [ "$enabled" = "true" ]; then
    kscreen-doctor output.eDP-1.disable
else
    kscreen-doctor output.eDP-1.enable
fi
