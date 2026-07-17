#!/usr/bin/env python3

import sys
import tomllib

with open("pyproject.toml", "rb") as f:
    project = tomllib.load(f)["project"]

with open("uv.lock", "rb") as f:
    uvlock = tomllib.load(f)

qasync_wheels = [
    whl for p in uvlock["package"] for whl in (p.get("wheels") or []) if p["name"] == "qasync"
]
assert len(qasync_wheels) == 1
qasync_whl = qasync_wheels[0]

with open(sys.argv[1], "w") as f:
    print("export PKG_NAME := " + project["name"], file=f)
    print("export PKG_VERSION := " + project["version"], file=f)
    print("export PKG_DESCRIPTION := " + project["description"], file=f)
    print("QASYNC_WHEEL_URL := " + qasync_whl["url"], file=f)
    print("QASYNC_WHEEL_FILENAME := " + qasync_whl["url"].split("/")[-1], file=f)
    print("QASYNC_WHEEL_SHA256 := " + qasync_whl["hash"].removeprefix("sha256:"), file=f)
