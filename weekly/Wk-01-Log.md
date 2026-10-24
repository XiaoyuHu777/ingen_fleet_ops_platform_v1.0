# Week 1 Log

Prepared 23 October 2026 for the 26 October review.

The hardest architecture decision for me was Artifact Storage and Signing. The
security mechanisms are relatively unfamiliar to me, and the decision involves
more than deciding where a model should be stored. It also requires considering
how to prevent a model from being tampered with. This makes the trust boundary
an important part of the proposed storage design.

ADR-0002 proposes content-addressed storage in MinIO locally and an Azure Blob
adapter later. SHA-256 gives the manifest a stable identity and binds the payload
hashes recorded inside it. Checking against a trusted digest detects changed
bytes, but a hash alone does not establish an authorized publisher: an attacker
could replace both a file and an untrusted hash. An Ed25519 signature over the
manifest digest adds origin authentication when the robot already trusts the
verification key. A valid signature still does not establish evaluation quality,
operator approval, or safe physical behaviour.

The proposed key boundary keeps the signing private key restricted to a registry
signing component. Robots receive public verification keys, not the signing key.
Rotation requires distributing an authenticated trust bundle, confirming receipt,
changing the signer, and retiring the old key. Compromise requires revocation
without normal overlap. Offline robots cannot receive revocation immediately;
they must refresh trust before a new activation. These are design requirements
for later implementation and drills, not security features already delivered.

Cosign is a genuine alternative. It supports ordinary blobs, self-managed keys,
identity-based signing, and offline verification bundles. The proposed Ed25519
choice keeps the initial Python agent's verification contract small and explicit,
while leaving key distribution and rotation as responsibilities this project must
implement. Cosign's provenance capabilities remain a later option rather than
being excluded on an incorrect always-online assumption.

Rover adds the requirement to resume interrupted downloads while retaining the
last healthy model. Completed bytes must be checked before activation. Sentinel
adds the audit question of which authorized artifact ran on a unit during an
incident. Together, these constraints connect immutable storage, signatures,
release records, and reported version state. The personal plan remains primary:
one shared platform uses Rover first and Fari to check generality.

Week 1 implements the foundation only: the empty registry, PostgreSQL, MinIO,
configuration, logging, tests, and CI definition. Recorded local results were
18 passing tests and 89.61 percent branch-aware coverage. Dependency-stop and
recovery exercises passed. Remote Actions and deliberately broken remote checks
remain pending in this version of the log; their status must come from actual
run evidence. Signing, evaluation, agents, fleet rollout, and cloud deployment
remain assigned to later weeks.
