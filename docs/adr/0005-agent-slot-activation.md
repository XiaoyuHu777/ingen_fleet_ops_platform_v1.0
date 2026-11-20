# ADR 0005 Durable staging and A/B activation on the robot

Status: Implemented for Week 5

The agent downloads a candidate to its local staging directory. An interrupted
transfer preserves bytes and their HTTP representation identity across process
restarts. A subsequent request uses `Range` and `If-Range`, and appends only
when a `206` response matches the offset and representation stored on disk.
The bundle ETag identifies transferred bytes. The target digest identifies the
signed manifest. Each has a different role in the update.

Only a candidate that passes the existing manifest digest, Ed25519 signature,
and payload checks can enter an inactive slot. Before the agent changes the
active link, it rechecks the target and the local safety flag. For a configured
Fari window it also checks the Registry's window and local time. The active
link is replaced atomically on the local filesystem. A durable activation
record lets the agent recover the previous link after a failed smoke test or an
interruption during the switch. Heartbeats report the last successful slot
until the candidate has passed smoke.

An in-place overwrite would use less storage but could expose a partial model
and would require another download when the link is unavailable. Keeping every
verified version under a digest-specific path would simplify long-range
rollback, but would also need garbage collection and a separate retention
policy. Two slots bound the retained model count and let the agent restore its
previous model while offline. Staging space and two complete unpacked model
slots must fit on the same filesystem.

Week 5 uses the published desired-state and heartbeat contracts. The local
safety flag comes from the robot's platform-specific safety integration.
The Week 6 controller will apply rollout policy to persisted heartbeat data.

References: [Agent pull ADR](0003-agent-communication.md),
[Fleet contract](../W02_API_Contract.md),
[Artifact contract](../W03_Artifact_Contract.md),
[Approval contract](../W04_Gate_Contract.md).
