import logging
import os

__all__ = ["get_logger"]


def get_logger(name: str, level=None, fmt=None):
    logging.setLogRecordFactory(_LogRecord)

    if level is None:
        level = _default_level()

    formatter = logging.Formatter(fmt or "%(asctime)s [%(lvl)s] %(name)s | %(message)s")

    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(handler)
    logger.propagate = False

    return logger


class _LogRecord(logging.LogRecord):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lvl = {
            logging.ERROR: "ERR",
            logging.WARNING: "WRN",
            logging.INFO: "INF",
            logging.DEBUG: "DBG",
        }.get(self.levelno, self.levelname)


def _default_level():
    if level := os.environ.get("LOG_LEVEL", "").upper():
        if level in logging.getLevelNamesMapping():
            return level

    return logging.INFO
