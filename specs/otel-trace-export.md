---
id: otel-trace-export
title: "OpenTelemetry span export from kernel journal events"
priority: normal
trust_zone: low
---

## Goal

Export kernel journal events as OpenTelemetry-compatible spans for integration with Grafana, Jaeger, Datadog. Tasks become traces, steps become spans, kernel events become span events.

## Steps

1. Create `src/hermit/kernel/analytics/otel_exporter.py`:
   - `OTelExporter` class:
     - `export_task_trace(task_id, store)` → OTelTrace
     - `export_span(step_attempt, events)` → OTelSpan
     - `to_otlp_json(trace)` → dict
     - `push_to_collector(trace, endpoint)` → bool

2. Create `src/hermit/kernel/analytics/otel_models.py`:
   - `OTelTrace`, `OTelSpan`, `OTelSpanEvent`, `OTelExportConfig`

3. Add DISPATCH_RESULT hook for auto-export (opt-in via config)
4. Add CLI command `hermit task trace <task-id>`

5. Write tests in `tests/unit/kernel/test_otel_exporter.py` (>= 8 tests)

## Constraints

- No opentelemetry SDK dependency — generate OTLP JSON manually
- Trace IDs deterministically derived from task_id
- Nanosecond precision timestamps
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/analytics/otel_exporter.py` exists
- [ ] `src/hermit/kernel/analytics/otel_models.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_otel_exporter.py -q` passes with >= 8 tests

## Context

- KernelStore events: `src/hermit/kernel/ledger/journal/store_v2.py`
- ProofService: `src/hermit/kernel/verification/proofs/proofs.py`
