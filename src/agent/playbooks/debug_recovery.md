# Debug / Recovery Playbook

## Purpose
Handle failed setup attempts after a specialist agent’s first-pass plan does not verify successfully.

## Inputs
- repo family classification
- selected specialist
- attempted setup steps
- concise verification failure
- bounded error output
- system and hardware signals
- mode and safety constraints

## Failure classes
- dependency install failure
- command not found
- missing compiler or build tools
- pip or venv mismatch
- node or package-manager mismatch
- service not running
- port conflict
- model/runtime missing
- GPU or CUDA mismatch
- unsupported platform
- mixed-stack repo ambiguity

## Allowed recovery decisions
- retry_same_specialist
- reroute_to_other_specialist
- request_missing_prerequisite
- unsupported_case

## Recovery rules
- prefer the smallest safe recovery step
- prefer reroute when the repo was likely misclassified
- prefer prerequisite request when a missing system dependency blocks progress
- mark unsupported when the platform or stack is not realistically recoverable within current guardrails
- do not repeat the same failed plan without a concrete revision

## Verification rules
- every recovery plan must include a verification check
- never claim success unless verification passes
- if verification fails again, escalate back to the supervisor

## Memory rules
- store only concise generalized learnings
- do not store raw logs
- do not store one-off noise that does not generalize