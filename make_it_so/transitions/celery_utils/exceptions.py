from contextlib import redirect_stdout, redirect_stderr
import json
import os

from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded, MaxRetriesExceededError
from celery.exceptions import Ignore as IgnoreException
from celery.exceptions import Retry as InternalRetryException
from celery.contrib import rdb
from django.core.exceptions import ObjectDoesNotExist
from googleapiclient.errors import HttpError as GcpHttpError
from requests.exceptions import RequestException
import structlog
from urllib3.exceptions import HTTPError as BaseHTTPError


logger = structlog.get_logger(__name__)


class BaseTransitionTaskException(Exception):

    def __init__(self, *args, reason=None, info=None):
        # constructor ensures it is serializable:
        # https://docs.celeryq.dev/en/stable/userguide/tasks.html#creating-pickleable-exceptions
        Exception.__init__(self, *args)
        self.event_type = args[0]
        self.reason = reason
        self.extra_info = info

    @property
    def event_type_and_reason(self):
        if not self.reason:
            return self.event_type
        return f'{self.event_type}:{self.reason}'

    @property
    def details_tuple(self):
        return self.event_type, self.reason, self.extra_info

    def __str__(self):
        super_str = super().__str__()
        if self.reason:
            super_str = f'{super_str}: {self.reason}'
        return super_str


class TaskRetryException(BaseTransitionTaskException):

    def __init__(
        self, *args, reason=None, info=None, exhausted_side_effect=None
    ):
        # super().__init__() seems to mess with celery, so
        # the constructor is repeated here
        Exception.__init__(self, *args)
        self.event_type = args[0]
        self.reason = reason
        self.extra_info = info
        self.exhausted_side_effect = exhausted_side_effect


class TaskFailureException(BaseTransitionTaskException):
    pass


RETRY_FOR = [
    RequestException, BaseHTTPError, GcpHttpError,
    TaskRetryException, InternalRetryException,
    TimeLimitExceeded, SoftTimeLimitExceeded
]
THROWS = tuple(RETRY_FOR + [
    ObjectDoesNotExist, IgnoreException, MaxRetriesExceededError,
    TaskFailureException
])

EXCLUDE_EXCEPTIONS_FROM_INFO = (
    TaskRetryException, IgnoreException, SoftTimeLimitExceeded, TimeLimitExceeded
)


def create_extra_info(
    model_obj, exc=None, einfo=None, extra_info=None, celery_task=None
):
    extra_info = extra_info or {}
    extra_info['current_state'] = model_obj.state

    if exc and not isinstance(exc, EXCLUDE_EXCEPTIONS_FROM_INFO):
        extra_info['exception'] = exc.__repr__()
        if einfo and isinstance(exc, THROWS) is False:
            extra_info['traceback'] = einfo.traceback

    if celery_task:
        extra_info['retry_index'] = celery_task.retry_index
        extra_info['max_retries'] = celery_task.tc.get_max_retries()

    if isinstance(exc, GcpHttpError):
        fields = ['reason', 'resp', 'uri', 'status_code']
        extra_info['args'] = exc.args[0]
        for fn in fields:
            val = getattr(exc, fn, None)
            if val:
                if fn in extra_info:  # not a big deal but should be avoided
                    logger.warning('overwriting extra_info field', field=fn)
                extra_info[fn] = val

    ensure_extra_info_is_serializable(extra_info)

    return extra_info


def _dumps_suppressed(di):
    devnull = open(os.devnull, "w")
    with redirect_stdout(devnull), redirect_stderr(devnull):
        return json.dumps(di)


def ensure_extra_info_is_serializable(extra_info):
    try:
        json.dumps(extra_info)
        return
    except TypeError:  # note: stderr is getting printed on console
        pass
    keys = [k for k in extra_info.keys()]
    for key in keys:
        val = extra_info[key]
        try:
            json.dumps(extra_info)
        except TypeError:
            if key == 'resp':
                extra_info[key] = str(val)
                continue
            logger.warning(
                f'failed to serialize extra_info["{key}"]', type=type(val),
                exc_info=False
            )
            del extra_info[key]
