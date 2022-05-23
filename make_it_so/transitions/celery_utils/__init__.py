import functools
import inspect
import pickle
import random
import sys
import typing

import redis
import structlog

MAX_WAIT = sys.maxsize / 2


logger = structlog.get_logger(__name__)


# removed:
#def wait_exponential(
#def wait_incrementing(


class _Memorize(object):

    def __init__(self, function, cache_keys=None, expiry=180, **kwargs):
        assert callable(function)
        self.func = function

        self.arg_names = inspect.getfullargspec(function).args

        self.expiry = expiry
        self.cache_keys = cache_keys or self.arg_names
        self._redis_cli = None

    @property
    def redis_cli(self):
        if self._redis_cli is None:
            self._redis_cli = redis.from_url('redis://127.0.0.1:6379/0')
        return self._redis_cli

    def _create_cache_key(self, kwargs):
        kwargs = kwargs.copy()
        cache_id_elems = [self.func.__name__]
        for key in self.cache_keys:
            if key == 'transition_pk' and 'transition' in kwargs:
                value = kwargs.pop('transition').pk
            else:
                value = kwargs[key]
            cache_id_elems.append(f'{key}:{value}')

        return 'Mz' + ('|'.join(cache_id_elems))

    def _redis_get(self, cache_key):
        cached_result = self.redis_cli.get(cache_key)
        if cached_result is None:
            return None
        return pickle.loads(cached_result)

    def _redis_set(self, cache_key, result):
        self.redis_cli.set(
            cache_key, pickle.dumps(result), ex=self.expiry
        )

    def __call__(self, *args, **kwargs):
        kwargs = kwargs.copy()
        for i, arg in enumerate(args):
            kwargs[self.arg_names[i]] = arg

        cache_key = self._create_cache_key(kwargs)

        retry_index = None
        if kwargs.get('task'):
            # if task is available, use retry_index to decide whether
            # the cached value should be used
            retry_index = kwargs['task'].retry_index

        if retry_index is None or retry_index > 0:
            cached_result = self._redis_get(cache_key)
            if cached_result:
                return cache_key

        result = self.func(**kwargs)
        success = result[0] if isinstance(result, (tuple, list)) else result
        assert isinstance(success, bool)

        if success:
            self._redis_set(cache_key, result)
        return result

    def __get__(self, obj, objtype):
        """ Support instance methods. """
        return functools.partial(self.__call__, obj)


# todo: should checkpoints also have a redis lock? (scoped by: [transition_pk, func_name] )


def Memorize(function=None, **kwargs):
    if function:
        return _Memorize(function)
    else:
        def wrapper(function):
            return _Memorize(function, **kwargs)
        return wrapper


@Memorize(keys=["l"])
def checkpoint__calculate(max_val, default=4):
    return [v for v in range(max_val)]


def wait_exponential(
    retry_index,
    retry_backoff: typing.Union[int, float] = 1,
    max_value: typing.Union[int, float] = MAX_WAIT,  # noqa
    exp_base: typing.Union[int, float] = 2,
    min_value: typing.Union[int, float] = 0,  # noqa
):
    try:
        exp = exp_base ** retry_index
        result = retry_backoff * exp
    except OverflowError:
        return max_value
    return max(max(0, min_value), min(result, max_value))


def get_exponential_backoff_interval(
    factor,  # aka retry_backoff
    retries,
    minimum,
    maximum,  # aka retry_backoff_max
    full_jitter=False
):
    """Calculate the exponential backoff wait time."""
    # Will be zero if factor equals 0
    countdown = min(maximum, factor * (2 ** retries))
    # Full jitter according to
    # https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/
    if full_jitter:
        countdown = random.randrange(countdown + 1)
    # Adjust according to maximum wait time and account for negative values.
    return max(minimum, countdown)
