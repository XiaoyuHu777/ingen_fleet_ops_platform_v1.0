# Week 2 Log

The central design choice this week was to separate what a robot reports from
what an operator wants it to run. I considered a single row with one artifact
version, but that would blur two different facts. After a target is assigned,
the robot may be offline, downloading, unable to activate the artifact, or still
running its previous version after a failed update. Overwriting its observed
version with the target would make the fleet appear converged even though the
robot has not confirmed anything. Keeping a target and an observation separately
lets the registry show the gap and leaves the later agent responsible for
reconciliation. A desired target is an instruction; a heartbeat is a robot's
claim about its state, not proof that the artifact is valid or safe.

An alternative was to record every update as a command and infer current state
from a command history. That could be valuable for audit, but it is more complex
than the Week 2 source-of-truth requirement and does not remove the need for a
current observation. The registry therefore makes the current desired and reported
states explicit. The later artifact lifecycle and rollout audit still need their
own records; this week's registry does not implement the rollout controller,
artifact verification, or physical safety decisions.

The polling contract also matters when a fleet is mostly idle. A robot can send
its last ETag with a desired-state request; when the representation is unchanged,
the API can return 304 without writing a new row just because a robot checked.
Authentication and revocation must still be evaluated for every poll, including
conditional requests. A cached target is not permission to activate it: the
future agent and controller must implement the separate lease and trust behavior
recorded in ADR-0003. An offline robot cannot learn a changed target or credential
revocation until it reconnects.

Rover motivated separate site and cohort information and an indexed view of
stale reports. Fari is a useful check on the schema: both an absolute interval
and a daily local-time interval can be represented for any platform. The registry
does not calculate occurrences or resolve daylight-saving transitions. A configured window
is not itself a verified safety interlock. The registry only carries this
information; deciding when and how to act on it belongs to later agent and rollout
work. The API contract and threat model record the exact field semantics and the
limitations for reviewers and future client developers.
