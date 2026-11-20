# Week 5 fleet observability

Run the simulator with `--observability --keep` to start the Prometheus and Grafana services alongside Fleet and Artifact Registry. Prometheus scrapes every two seconds. Docker DNS discovers the agent endpoints without a Docker socket mount. The Registry scrape uses a dedicated bearer credential generated in the simulator's private run directory. Compose mounts the credential read-only into the Registry and Prometheus. The dashboard and Prometheus UI are loopback-only.

Open **InGen Fleet Health** in Grafana. **Robots by artifact version** groups registered robots by their last reported artifact name, version, and digest. **Robots with late heartbeats** shows each stale robot's UUID, site, and elapsed time since its last accepted heartbeat. The default threshold is 15 seconds and can be changed with the dashboard selector. **Robots yet to report** lists registered robots without an accepted heartbeat. The heartbeat panels use Registry data so a stopped agent remains visible even when its own scrape endpoint disappears. [Local Grafana dashboard](http://127.0.0.1:3005/d/ingen-fleet-health/ingen-fleet-health)

The chart shows five-minute increases in agent download, verification, and smoke-test failure counters. Counters reset when the agent restarts, and Prometheus calculates increases across those resets. To import the dashboard into another Grafana instance, upload the Classic JSON and select a Prometheus data source that scrapes the InGen metrics. [Dashboard JSON](grafana/dashboards/fleet-health.json)

## Verify a live run

The simulator publishes its approved target digest and enrolled robot IDs in its convergence report. The commands below use the Compose project and private report path in the [Week 5 reproduction instructions](../week-05/README.md#reproduce-the-checks). Run them after convergence and recording finish.

```bash
report=.week5/convergence/convergence.json
target=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["target_digest"])' "$report")
robot=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["robots"][0]["robot_id"])' "$report")
agent_container=ingen-week5-convergence-agent-01-1
registry_container=ingen-week5-convergence-registry-1
docker logs "$agent_container" > .week5/agent-01.log 2>&1
docker logs "$registry_container" > .week5/registry.log 2>&1
python3 -m scripts.verify_observability --expected-robots 20 --expected-agents 20 \
  --expected-late 0 --version-count "$target" 20 \
  --agent-log .week5/agent-01.log --registry-log .week5/registry.log

docker stop "$agent_container"
python3 -m scripts.verify_observability --expected-robots 20 --expected-agents 19 \
  --expected-late 1 --version-count "$target" 20 --stale-robot "$robot"
docker start "$agent_container"
python3 -m scripts.verify_observability --expected-robots 20 --expected-agents 20 \
  --expected-late 0 --version-count "$target" 20
```

The verification script loads the provisioned dashboard and runs its panel queries through the Grafana datasource proxy. It can also join the two JSON logs on `request-id`, `robot-id`, and HTTP status. The Registry assigns robot IDs after authenticating their credentials. [Recorded verification](../week-05/evidence/grafana-verification.json)
