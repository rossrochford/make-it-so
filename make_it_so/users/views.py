from django.shortcuts import render
from django.http import HttpResponse


def hello_view(request):
    return HttpResponse('hi')



# https://zipkin.io/zipkin-api/#/
#
#
#
# todo: start the following
#   - start span in __call__
#   - start span in before_start()
#   - in Resource/Transition.log_event() use trace.get_current_span() and add event
#   -       if span id is 000000:
#               flag event as needing further processing to ensure its event gets assinged to a span
#               - ctx = trace.get_current_span().context; ctx.trace_id; ctx.span_id


# @app.route("/roll")
# def roll():
#     with tracer.start_as_current_span(
#         "server_request",
#         attributes={ "endpoint": "/roll" }
#     ):
#
#         sides = int(request.args.get('sides'))
#         rolls = int(request.args.get('rolls'))
#         return roll_sum(sides,rolls)
#
# def roll_sum(sides, rolls):
#     span = trace.get_current_span()
#     sum = 0
#     for r in range(0,rolls):
#         result = randint(1,sides)
#         span.add_event( "log", {
#             "roll.sides": sides,
#             "roll.result": result,
#         })
#         sum += result
#     return  str(sum)