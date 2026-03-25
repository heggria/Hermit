"""Hermit Trace-Contract-Driven Assurance System.

This subsystem sits on top of the existing governance chain
(approval → grant → lease → receipt → proof) and turns governance,
isolation, recoverability, and auditability into continuously
verifiable, attributable, and replayable system properties.

Modules
-------
models       – Core data models (ScenarioSpec, TraceEnvelope, FaultSpec, etc.)
recorder     – Additive event sink, trace normalisation, artifact refs
contracts    – Contract DSL, runtime check, post-run check
invariants   – Layered invariants and first-violation records
injection    – Harness-only fault injection
replay       – Historical replay and counterfactual replay
attribution  – Causal graph and root-cause selection
reporting    – JSON + Markdown report emission
lab          – Scenario runner, nightly chaos, certification
"""

from hermit.kernel.verification.assurance.attribution import FailureAttributionEngine
from hermit.kernel.verification.assurance.contracts import AssuranceContractEngine
from hermit.kernel.verification.assurance.injection import FaultInjector
from hermit.kernel.verification.assurance.invariants import InvariantEngine
from hermit.kernel.verification.assurance.lab import AssuranceLab
from hermit.kernel.verification.assurance.models import (
    AssuranceReport,
    AttributionCase,
    ContractViolation,
    FaultSpec,
    InvariantViolation,
    ScenarioSpec,
    TraceContractSpec,
    TraceEnvelope,
)
from hermit.kernel.verification.assurance.recorder import TraceRecorder
from hermit.kernel.verification.assurance.replay import ReplayService
from hermit.kernel.verification.assurance.reporting import AssuranceReporter

__all__ = [
    "AssuranceContractEngine",
    "AssuranceLab",
    "AssuranceReport",
    "AssuranceReporter",
    "AttributionCase",
    "ContractViolation",
    "FailureAttributionEngine",
    "FaultInjector",
    "FaultSpec",
    "InvariantEngine",
    "InvariantViolation",
    "ReplayService",
    "ScenarioSpec",
    "TraceContractSpec",
    "TraceEnvelope",
    "TraceRecorder",
]
