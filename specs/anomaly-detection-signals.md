---
id: anomaly-detection-signals
title: "Detect unusual execution patterns and emit governance signals"
priority: normal
trust_zone: low
---

## Goal

Build an anomaly detection system monitoring the kernel journal for unusual execution patterns, emitting EvidenceSignals for operator review. Anomalies include: frequency spikes, unexpected transitions, abnormal token consumption, novel high-risk actions, off-hours execution.

## Steps

1. Create `src/hermit/kernel/signals/anomaly_detector.py`:
   - `AnomalyDetector` class:
     - `detect(task_id, store)` → list[AnomalySignal]
     - Detection rules: _check_frequency_spike, _check_unexpected_transition, _check_token_burn_rate, _check_novel_high_risk, _check_off_hours
     - `build_baseline(principal_id, store, window_days=30)` → Baseline

2. Create `src/hermit/kernel/signals/anomaly_models.py`:
   - `AnomalySignal`, `AnomalyType` enum, `Baseline`

3. Convert anomalies to EvidenceSignals with cooldown
4. Integrate into post-step execution

5. Write tests in `tests/unit/kernel/test_anomaly_detection.py` (>= 9 tests)

## Constraints

- MUST NOT block execution — async only
- Baselines require minimum 10 tasks
- 1-hour cooldown per anomaly type per task
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/signals/anomaly_detector.py` exists
- [ ] `src/hermit/kernel/signals/anomaly_models.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_anomaly_detection.py -q` passes with >= 9 tests

## Context

- EvidenceSignal: `src/hermit/kernel/signals/models.py`
- SignalConsumer: `src/hermit/kernel/signals/consumer.py`
- KernelStore: `src/hermit/kernel/ledger/journal/store.py`
