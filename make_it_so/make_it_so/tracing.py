'''

# jaeger architecture:
https://www.jaegertracing.io/docs/1.32/architecture/

# jaeger practical tutorial:
https://medium.com/opentracing/take-opentracing-for-a-hotrod-ride-f6e3141f7941


# celery:
    https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/celery/celery.html
    https://github.com/open-telemetry/opentelemetry-python-contrib/tree/main/instrumentation/opentelemetry-instrumentation-celery
    https://github.com/aspecto-io/aspecto-celery-sample

# redis:
    https://github.com/open-telemetry/opentelemetry-python-contrib/tree/main/instrumentation/opentelemetry-instrumentation-redis


https://github.com/open-telemetry/opentelemetry-python-contrib/issues/609


https://github.com/open-telemetry/opentelemetry-python/blob/main/docs/examples/basic_tracer/basic_trace.py


exporting to jaeger:

pip install opentelemetry-exporter-jaeger

https://opentelemetry-python.readthedocs.io/en/latest/exporter/jaeger/jaeger.html
https://opentelemetry-python.readthedocs.io/en/stable/getting-started.html#configure-exporters-to-emit-spans-elsewhere
'''


# starting jaeger, admin: http://localhost:16686
'''
docker run -d --name jaeger \
  -e COLLECTOR_ZIPKIN_HOST_PORT=:9411 \
  -p 5775:5775/udp \
  -p 6831:6831/udp \
  -p 6832:6832/udp \
  -p 5778:5778 \
  -p 4317:4317 \
  -p 16686:16686 \
  -p 14250:14250 \
  -p 14268:14268 \
  -p 14269:14269 \
  -p 9411:9411 \
  jaegertracing/all-in-one:1.33

'''

# log collector:
'''
docker run \
    -p 4317:4317 \
    -v $(pwd)/otel-collector-config.yaml:/etc/otel/config.yaml \
    otel/opentelemetry-collector-contrib:latest
'''


# docker-compose: https://github.com/mottibec/opentelemetry-collector-contrib/blob/main/examples/demo/docker-compose.yaml


# diagram on how to choose span, vs span-event vs span-attribute:
#   https://docs.google.com/presentation/d/1pg4Vn_gO6LiIX-S0Wy-kkmXC6QNrKz3yYfFDo2XF9nw/edit#slide=id.gf75e9bdc8c_0_32




