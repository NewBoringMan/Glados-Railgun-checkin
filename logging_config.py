import datetime
import logging
import logging.config


def taipei_time_converter(timestamp):
    utc_dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
    taipei = datetime.timezone(datetime.timedelta(hours=8))
    return utc_dt.astimezone(taipei).timetuple()


LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s - %(levelname)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "converter": taipei_time_converter,
        }
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard", "level": "INFO"}
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}


def init_logger():
    logging.config.dictConfig(LOGGING_CONFIG)
    return logging.getLogger()
