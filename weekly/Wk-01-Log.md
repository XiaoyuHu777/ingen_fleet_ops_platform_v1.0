# Week 1 Log

Prepared 23 October 2026 for the 26 October review.

The hardest architecture decision in this proposal was how agents should receive
deployment targets. I used my individual SDE plan as the primary reference and
the product documents to identify operating constraints. The five-platform brief
supports one shared platform, with Rover as the implementation target and Fari
testing generality. Sentinel, Humanoid, and Senpai contribute requirements without
adding three separate implementations.

I compared jittered HTTPS polling with persistent WebSocket or MQTT connections.
Persistent connections offer faster notification and can also be initiated
outbound, so a school firewall does not automatically rule them out. However,
they add session recovery and keepalive handling; MQTT also introduces a broker.
After a disconnection, the agent still needs to reconcile the latest desired
state. Deployment correctness therefore depends on durable state and recovery
behaviour rather than on notification alone.

I chose outbound pull with a proposed five-second interval and independent
twenty-percent jitter. Twenty agents would generate approximately four
desired-state reads per second, plus four heartbeat requests per second. These
are planning estimates, not measured capacity. The brief also exposes a more
important constraint: a 256 MiB package takes about 17.9 minutes over an effective
2 Mbit/s link before retries. Polling cannot solve that transfer budget. Resumable
downloads, explicit pending state, caching where appropriate, and recorded test
conditions are necessary. The programme's under-ten-minute rollout target stays
unchanged; a weak-link failure must be reported as a gap.

The failure policy helped settle the communication choice. Agents keep their
last verified model during controller outages. A short activation lease prevents
a stale target from authorizing new activation indefinitely. Local smoke-test
rollback remains independent of controller availability. Fari adds another
condition: completed delivery must wait for an interaction-safe activation window.
These behaviours are specified now and implemented in their later assigned weeks.

For the foundation, I selected Compose locally and Container Apps for a future
Azure sandbox requiring explicit approval. The storage design specifies
content-addressed artifacts and an Ed25519 verification contract. The repository
contains the registry foundation, PostgreSQL, MinIO, locked Python dependencies,
tests, and the CI workflow. Eighteen tests passed with 89.61 percent branch-aware
coverage. Local quality checks and real dependency-outage exercises passed:
readiness fails when either dependency stops and recovers when it returns.

The remaining submission work is to review the assumptions and decisions,
publish to the intended GitHub repository, and inspect actual Actions results,
including deliberately broken remote checks. A workflow file and local passes do
not establish green remote CI. No signing implementation, robot agent, simulated
fleet rollout, or cloud deployment is claimed for Week 1.
