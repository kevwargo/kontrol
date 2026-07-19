import asyncio
import logging
from functools import wraps


class AsyncTaskSupervisor:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__tasks: set[asyncio.Task] = set()

    def as_task(self, fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            self.__start_task(fn(*args, **kwargs))

        return wrapped

    async def cleanup(self):
        if not self.__tasks:
            return

        for task in self.__tasks:
            task.cancel()

        await asyncio.gather(*self.__tasks, return_exceptions=True)

    def __start_task(self, coro):
        task = asyncio.create_task(coro)
        self.__tasks.add(task)
        task.add_done_callback(self.__task_done)

    def __task_done(self, task: asyncio.Task):
        self.__tasks.discard(task)

        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logging.exception()
