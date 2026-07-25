import json
from subprocess import check_output, run

BUILTIN_SCREEN_NAME = "eDP-1"


def main():
    screens = json.loads(check_output(["kscreen-doctor", "-j"]))["outputs"]
    try:
        builtin = next(s for s in screens if s["name"] == BUILTIN_SCREEN_NAME)
    except StopIteration:
        print(f"No {BUILTIN_SCREEN_NAME} screen found.")
        return

    if len(screens) == 1:
        print(f"No external screens, enabling {BUILTIN_SCREEN_NAME} screen")
        _kscreen_enable(True)
    else:
        if builtin["enabled"]:
            print(f"Disabling {BUILTIN_SCREEN_NAME}")
            _kscreen_enable(False)
        else:
            print(f"Enabling {BUILTIN_SCREEN_NAME}")
            _kscreen_enable(True)


def _kscreen_enable(enable: bool):
    run(["kscreen-doctor", f"output.{BUILTIN_SCREEN_NAME}.{'en' if enable else 'dis'}able"])


if __name__ == "__main__":
    main()
