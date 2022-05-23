import time

from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.instrumentation.utils import (
    _start_internal_or_server_span,
    extract_attributes_from_object,
)
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
# from opentelemetry.sdk.trace import _Span as Span
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor, SimpleSpanProcessor, ConsoleSpanExporter
)
from opentelemetry.propagators import textmap
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.trace import SpanKind, use_span
# from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry import trace
import structlog

from transitions.celery_utils.context import TransitionTaskContext


DEBUG = False

trace.set_tracer_provider(
   TracerProvider(
       resource=Resource.create({SERVICE_NAME: "make-it-so"})
   )
)
jaeger_exporter = JaegerExporter(
   agent_host_name="localhost", agent_port=6831,
)
trace.get_tracer_provider().add_span_processor(
   BatchSpanProcessor(jaeger_exporter)
)
if DEBUG:
    trace.get_tracer_provider().add_span_processor(
        SimpleSpanProcessor(ConsoleSpanExporter())
    )

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


class StartSpanWithCarrier:

    def __init__(self, t, span_name, carrier_or_none):
        self.tracer = t
        self.name = span_name
        self.carrier = carrier_or_none
        self.use_ctx = None

    def __enter__(self):

        current_span = trace.get_current_span()
        if current_span._context.span_id != 0:
            self.use_ctx = tracer.start_as_current_span(self.name)
            self.use_ctx.__enter__()
            return self

        if self.carrier:
            self.use_ctx = enter_span(self.name, self.carrier)

            # ctx = TraceContextTextMapPropagator().extract(carrier=self.carrier)
            # self.use_ctx = tracer.start_as_current_span(self.name, context=ctx)
            # self.use_ctx.__enter__()

        return self

    def __exit__(self, type, value, traceback):
        if self.use_ctx:
            self.use_ctx.__exit__(type, value, traceback)

    # - this version allows you to verify self.span == trace.get_current_span()
    # def __enter__OLD(self):
    #     ctx = TraceContextTextMapPropagator().extract(carrier=self.carrier)
    #     self.span = self.tracer.start_span(self.span_name, context=ctx)
    #     self.use_context = trace.use_span(self.span)
    #     self.use_context.__enter__()
    #     return self
    #
    # def __exit__OLD(self, type, value, traceback):
    #     self.span.end()
    #     self.use_context.__exit__(type, value, traceback)


carrier_getter = textmap.DefaultGetter()


# linking spans (horizontally instead of hierarchically)
#   span_ctx = trace.get_current_span().get_span_context()
#   link_from_current = trace.Link(span_ctx)
#   self.task_span = tracer.start_span(span_name)#, links=[link_from_current])


def enter_span(span_name, carrier):
    span, token = _start_internal_or_server_span(
        tracer=tracer,
        span_name=span_name,
        start_time=time.time(),
        context_carrier=carrier,
        context_getter=carrier_getter,
    )

    use_context = use_span(span)
    use_context.__enter__()
    return use_context


def trace_method(func):

    def _inner(self, *args, **kwargs):

        current_span = trace.get_current_span()
        span_name = f'{self.request.id[:-6]}_{self.retry_index}_{func.__name__}'

        if current_span._context.span_id != 0:
            with tracer.start_as_current_span(span_name): # context=trace_ctx):
                return func(self, *args, **kwargs)

        if func.__name__ == 'before_start' and self.tc is None:
            succ, _ = TransitionTaskContext.populate_context(self.request)

        if self.tc is None:
            return func(self, *args, **kwargs)

        # based on: https://opentelemetry.io/docs/instrumentation/python/cookbook/
        logger.warning('using carrier for tracing', task_id=self.request.id)
        trace_ctx = TraceContextTextMapPropagator().extract(carrier=self.tc.carrier)

        with tracer.start_as_current_span(span_name, context=trace_ctx):
            return func(self, *args, **kwargs)

    return _inner
