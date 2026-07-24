from subprocess import PIPE, CalledProcessError, Popen
from typing import Iterator


def multi_command(*commands: list[list[str]]) -> Iterator[bytes]:
    for p in [Popen(cmd, stdout=PIPE, stderr=PIPE) for cmd in commands]:
        out, err = p.communicate()
        if p.returncode:
            raise CalledProcessError(p.returncode, p.args, output=out, stderr=err)

        yield out
