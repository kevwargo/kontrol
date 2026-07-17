import json
from subprocess import check_output, run

BUILTIN_SCREEN_NAME = "eDP-1"


def main():
    screens = json.loads(check_output(["kscreen-doctor", "-j"]))["outputs"]
    try:
        builtin = next(s for s in screens if s["name"] == BUILTIN_SCREEN_NAME)
    except StopIteration:
        # Don't do anything if eDP-1 is not found
        return

    if len(screens) == 1:
        # Enable the sole builtin display just in case
        _kscreen_enable(True)
    else:
        _kscreen_enable(not builtin["enabled"])


def _kscreen_enable(enable: bool):
    args = ["kscreen-doctor", f"output.{BUILTIN_SCREEN_NAME}.{'en' if enable else 'dis'}able"]
    print(" ".join(args))
    run(args)


if __name__ == "__main__":
    main()
