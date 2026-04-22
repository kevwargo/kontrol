#!/bin/bash

if [ "$(kscreen-doctor -j | jq '.outputs|length')" -gt 1 ]; then
    enabled=$(kscreen-doctor -j | jq -r '.outputs[]|select(.name=="eDP-1").enabled')
    if [ "$enabled" = "true" ]; then
        kscreen-doctor output.eDP-1.disable
    else
        kscreen-doctor output.eDP-1.enable
    fi
else
    echo "No external monitor"
fi
