# Week 5 Robot Agent Fleet Simulator and Observability

[Project home](../README.md) · [Week 4](../week-04/README.md) · [Weekly index](../weekly/README.md)

| Deliverable | Evidence |
| --- | --- |
| W05 Robot Agent | [Agent source](../agent/), [automated cases](../tests/test_robot_agent.py), [systemd unit and installer](../agent/systemd/), [live Linux check](evidence/systemd.txt), [PR #4](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/pull/4) |
| W05 Fleet Simulator | [Simulator](../sim/), [20-robot convergence](evidence/fleet-convergence.json), [SIGKILL and resume](evidence/fleet-crash.json), [PR #5](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/pull/5) |
| W05 Observability | [Setup and queries](../observability/README.md), [Prometheus config](../observability/prometheus.yaml), [Grafana dashboard JSON](../observability/grafana/dashboards/fleet-health.json), [dashboard verifier](../scripts/verify_observability.py), [live dashboard evidence](evidence/grafana-verification.json) |
| Design and decision log | [Agent design](../docs/W05_Agent_Design.docx), [A/B activation decision](../docs/adr/0005-agent-slot-activation.md), [Week 5 log](../weekly/Wk-05-Log.md) |
| Recorded demo and checks | [Screen recording](W05_Demo_Recording.mp4), [CI workflow](../.github/workflows/ci.yml), [GitHub Actions](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/actions/workflows/ci.yml) |

## Observed results

- Docker Desktop Linux ran 20 independent agents across `lab-east` (7),
  `lab-central` (7) and `lab-west` (6). The test applied `tc/netem loss 10%`
  to each agent’s egress traffic to the Registry. Twenty out of twenty robots
  reported YOLOS-Tiny `1.0.0` in 15.98 seconds after the simulator began assigning
  desired state to the newly enrolled fleet. The convergence report records the
  full digest, UUID and final state of each agent, the ordered timeline and 2,348
  packets dropped by netem.
- A separate run killed `agent-01` during its download. After `SIGKILL`, the
  incomplete bundle remained under `staging/` with 81,920 of 26,204,160 bytes.
  The Registry returned HTTP 206 for a request starting at byte 81,920.
  The run also exercised temporary link loss, added latency, a write delay, and
  the digest-based degraded status.
- The agent and Registry logged the same robot ID and request ID for a real
  `GET /v1/robots/{id}/desired-state` request. Both log records are in
  `log_correlation` in the convergence report.
- Grafana displayed all 20 agents on `1.0.0`. After one agent stopped, the
  dashboard showed 19 active scrape targets and the stopped robot’s UUID in
  the late-heartbeat table. After restart it showed 20 targets and no late robots.
- The installer and unit ran twice in an isolated Ubuntu 24.04 container with
  systemd 255. The agent ran as `ingen-agent` with a read-only root, writable
  state, zero effective capabilities and `NoNewPrivileges`. It restarted after
  `SIGKILL`, advanced its persisted sequence and emitted JSON in the journal.

`make check` passed with Python 3.11.14 on macOS arm64 and PostgreSQL 16 in
Docker Desktop. All 106 tests passed with 89.78% Registry coverage.
`make openapi-check` and `make terraform-check` passed. The
[agent CI run](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/actions/runs/44654178875)
and [simulator CI run](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/actions/runs/44654534980)
show the hosted results for those PRs.

## Reproduce the checks

Run from the repository root with Python 3.11.14, uv, Compose v2, and a Linux
Docker engine with `sch_netem` and container `NET_ADMIN`. Docker Desktop on
macOS provides the Linux engine. Prepare a signed baseline that passed the
Week 4 gate by following the [Week 4 review](../week-04/README.md). The example
uses the baseline bundle and report from that workflow. The simulator creates
fresh credentials in each private run directory and uses newly enrolled robot
identities in a disposable Registry database.

```bash
make setup
make check
make openapi-check
make terraform-check
make secret-scan

python=.venv/bin/python
bundle=.week4/baseline.tar
evaluation=.week4/baseline
trust=.week3/keys/public

"$python" -m sim.fleet --scenario convergence \
  --project ingen-week5-convergence --run-dir .week5/convergence \
  --port 8015 --bundle "$bundle" --evaluation "$evaluation" --trust "$trust" \
  --loss-percent 10 --observability --keep \
  --report .week5/convergence/convergence.json

"$python" -m sim.fleet --scenario crash \
  --project ingen-week5-crash --run-dir .week5/crash \
  --port 8016 --bundle "$bundle" --evaluation "$evaluation" --trust "$trust" \
  --outage-seconds 3 --latency-ms 40 --write-delay-ms 8 --degraded-agent 1 \
  --report .week5/crash/crash.json
```

The CLI requires a fresh run directory and an unused Compose project name.
The convergence scenario starts 20 processes and the crash scenario starts one.
`--outage-seconds` sets temporary 100% loss to the Registry. `--latency-ms` and
`--loss-percent` configure netem. `--write-delay-ms` delays each staged chunk
write. `--degraded-agent <index>` affects just the specified simulator instance
when its committed digest is the target. `--keep` leaves that run’s services
available for inspection.

The `/v1/robots` response exposes the Week 6 signal under
`reported_state.health_summary`. The hook sets `status` to `degraded` and
`status_reason` to `simulated_digest` once the target becomes active. The
controller can associate that signal with `current_digest`, `last_seen` and
`heartbeat_sequence`. The same object carries `inference_latency_ms`,
`synthetic_detection_rate`, `error_count` and `safe_to_update`.

## Inspect the fleet in Grafana

With the kept convergence project running, open the [Grafana fleet dashboard](http://127.0.0.1:3005/d/ingen-fleet-health/ingen-fleet-health).
The version panel groups reported artifacts by model, version and digest. The
late-heartbeat table uses the Registry’s last accepted heartbeat and shows the
robot UUID, site and digest. Robots that have yet to report appear in a separate
table. The dashboard also shows active agent scrapes and the download,
verification and smoke-test failure counters.

```bash
run="$PWD/.week5/convergence"
export W05_RUN_DIR="$run"
compose=(docker compose -p ingen-week5-convergence --env-file "$run/stack.env"
  -f infra/local/compose.yaml -f "$run/agents.json" -f observability/compose.yaml)
target=$(jq -r .target_digest "$run/convergence.json")
robot=$(jq -r '.robots[0].robot_id' "$run/convergence.json")
.venv/bin/python -m scripts.verify_observability \
  --expected-robots 20 --expected-agents 20 --version-count "$target" 20
"${compose[@]}" stop agent-01
.venv/bin/python -m scripts.verify_observability \
  --expected-robots 20 --expected-agents 19 --version-count "$target" 20 \
  --stale-robot "$robot"
"${compose[@]}" start agent-01
"${compose[@]}" down --volumes
```

Set `ARTIFACT_PUBLIC_KEYS_DIR` to the same absolute trust directory passed to
the simulator if recreating Registry containers. Grafana and Prometheus bind
to loopback and the Registry scrape uses a dedicated token kept in the private
run directory. The committed dashboard can also be imported into Grafana and
paired with a Prometheus data source.

For an actual recording, start the same convergence command with
`--start-delay-seconds 12`, run
`.venv/bin/python -m sim.dashboard .week5/convergence/live.json` in a second
terminal and capture the live view from the ready state through 20/20.

## Install on Linux

Run `sudo agent/systemd/install.sh` on a systemd 254+ host with uv available to
root. Place the enrolled robot ID and Registry URL in
`/etc/ingen-agent/agent.env` as `INGEN_AGENT_ROBOT_ID` and
`INGEN_AGENT_REGISTRY_URL`. Save its bearer token as
`/etc/ingen-agent/credential` with owner `root`, group `ingen-agent`, and mode
`0640`. Provision trusted Ed25519 public keys under `/etc/ingen-agent/trust/`.
The robot’s safety integration writes `true` to
`/var/lib/ingen-agent/safe-to-update` only while local conditions permit an
update. Fari requires a configured Registry update window as well.

```bash
sudo systemd-analyze verify /etc/systemd/system/ingen-agent.service
sudo systemctl start ingen-agent
sudo systemctl status ingen-agent
sudo journalctl -u ingen-agent -o cat
curl -fsS http://127.0.0.1:9101/metrics
```

The Linux check exercises the packaged service with an unreachable Registry.
The Compose drill exercises both real Registry APIs and fixture inference. A
physical Fari safety signal and the Week 6 staged rollout and fleet rollback
targets still require their respective integration drills.
