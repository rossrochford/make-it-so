import time

import structlog


logger = structlog.get_logger(__name__)


class ResourceApiListResponse:

    @property
    def provider_id(self):
        raise NotImplementedError


class CatchTime:

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, type, value, traceback):
        self.end = time.perf_counter() - self.start

    @property
    def duration(self):
        return self.end


# usage:
# with catchtime() as t:
#    time.sleep(1)
#
# print(t.time)  #  1.000000321
