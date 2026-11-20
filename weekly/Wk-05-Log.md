# Week 5 Decision Log

I chose to keep two model slots on each robot and switch them through one
atomic symlink. The agent downloads the approved bundle into staging, verifies
it, and then prepares the inactive slot. After it switches the link, it loads
the candidate and runs the synthetic fixture inputs. The agent reports the
new digest only after that smoke test succeeds. If the test fails, it restores
the last committed slot without waiting for the Registry or a rollout command.

The simplest alternative was to replace the model in place. It would use less
disk, but a crash in the middle of a write could leave the robot without a
usable model. Even if the replacement were downloaded first, overwriting the
only good model would make recovery depend on getting its bytes back to the
robot. That is a poor fit for Rover sites with intermittent links. I also
considered keeping every verified digest. That would make older versions easy
to find, but it would add garbage collection and make disk usage depend on the
number of deployments. Two slots keep the current and previous usable versions
available with a predictable storage requirement.

The subtle part was deciding when a switch counts as complete. The symlink and
the post-activation smoke test are separate operations. I keep the committed
slot record on disk so a restart during smoke can restore it. Heartbeats read
the committed record while the candidate is under test. The same distinction
applies to the download. The partial file and the HTTP representation identity
stay in staging, and the agent appends only after a matching range response.
The signed manifest digest and payload checks still run on the completed bundle.

Fari added another condition. The Registry’s update window and the local
`safe_to_update` signal both have to permit a switch. I kept that check in the
reconciliation path so a `304` can reuse the target and still observe a change
in the robot’s flag. The separate heartbeat loop keeps reporting while activation
waits. This gives the Week 6 controller an observation it can use when judging
fleet progress, including the last accepted sequence, active digest, and health
summary. The local slot decision and the restart and fleet checks are recorded
in the Week 5 review page.
