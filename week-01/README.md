# Week 1 — Fleet Operations Foundation

[← Project home](../README.md) · [Week 2 →](../week-02/README.md)

Week 1 established the architecture, local infrastructure, and validation
foundation for one shared fleet-operations platform. Its registry was an empty
service providing health and readiness endpoints; robot business APIs belong to
Week 2.

## Tasks and evidence

| Week 1 task | Result and evidence | Status |
| --- | --- | --- |
| Analyze fleet requirements | [Fleet Ops Brief (DOCX)](../docs/W01_Fleet_Ops_Brief.docx) · [Delivery map](../docs/W01_Delivery_Map.md) | Prepared for review |
| Design the platform | [Platform Design Document (DOCX)](../docs/W01_Platform_Design_Doc.docx) | Prepared for review |
| Record architecture decisions | [Runtime](../docs/adr/0001-runtime-target.md) · [Storage and signing](../docs/adr/0002-artifact-storage-and-signing.md) · [Agent communication](../docs/adr/0003-agent-communication.md) | Proposed decisions documented |
| Build the local foundation | [Week 1 source snapshot](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/tree/7565de4360ab59da3c44d5044ef74fdef3414b5f) · [Compose stack](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/blob/7565de4360ab59da3c44d5044ef74fdef3414b5f/infra/local/compose.yaml) | Verified in the recorded Week 1 run |
| Test infrastructure and CI | [Validation report](../docs/W01_Validation_Report.md) · [GitHub Actions](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/actions/runs/40047956211) | Passed for the recorded Week 1 commit |
| Record weekly reflection | [Week 1 log](../weekly/Wk-01-Log.md) | Prepared for review |

## Inspect the Week 1 code as it was delivered

The service has continued to evolve, so use the fixed Week 1 snapshot when
reviewing the original implementation:

- [Week 1 repository snapshot](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/tree/7565de4360ab59da3c44d5044ef74fdef3414b5f)
- [Registry health and readiness endpoints](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/blob/7565de4360ab59da3c44d5044ef74fdef3414b5f/services/registry/app.py)
- [Local Compose stack](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/blob/7565de4360ab59da3c44d5044ef74fdef3414b5f/infra/local/compose.yaml)
- [Week 1 tests](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/blob/7565de4360ab59da3c44d5044ef74fdef3414b5f/tests/test_health.py)

Week 1 architecture documents include proposals for later work; they do not
claim that an agent, artifact pipeline, rollout controller, or cloud deployment
was already implemented.
