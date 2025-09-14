import logging
import os
from typing import Optional

from fastapi import FastAPI
from pythonjsonlogger import jsonlogger

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from prometheus_fastapi_instrumentator import Instrumentator


_INITIALIZED = False


def _setup_json_logging(service_name: str) -> None:
    logger = logging.getLogger()
    if any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        # Avoid duplicate handlers
        logger.handlers.clear()

    logger.setLevel(logging.INFO)

    log_handler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter(
        fmt='%(asctime)s %(levelname)s %(name)s %(message)s %(trace_id)s %(span_id)s',
        rename_fields={
            'levelname': 'level',
        },
    )
    log_handler.setFormatter(formatter)
    logger.addHandler(log_handler)


def _setup_tracing(service_name: str) -> None:
    resource = Resource.create({SERVICE_NAME: service_name})

    provider = TracerProvider(resource=resource)

    # Prefer OTLP exporter if endpoint is provided, else log to console
    otlp_endpoint = os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT')
    if otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)
    else:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)


def _setup_metrics(app: FastAPI) -> None:
    # Expose Prometheus metrics at /metrics
    Instrumentator().instrument(app).expose(app, include_in_schema=False)


def init_observability(app: FastAPI, service_name: str) -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return

    _setup_json_logging(service_name)
    _setup_tracing(service_name)

    # Auto-instrument FastAPI (traces requests)
    FastAPIInstrumentor.instrument_app(app)

    _setup_metrics(app)

    _INITIALIZED = True
