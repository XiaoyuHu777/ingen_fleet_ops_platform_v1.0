# Week 1 Requirements and Delivery Map

Prepared for the 26 October 2026 review. The individual SDE Internship Plan is the
primary reference. The mentor's clarification confirms Week 1 scope, all five
platforms in the brief, one shared implementation, Rover as primary and Fari as
secondary. Product SE plans and role-relevant handouts provide supporting evidence.

## Required outputs

| Authority | Requirement | Evidence | Status |
|---|---|---|---|
| Personal plan pp. 5–6 and mentor clarification | Five platforms with deployment, connectivity, update safety, telemetry/privacy, primary challenge and quantified assumptions | W01_Fleet_Ops_Brief.docx, five product sections, baseline and source register | Prepared for review |
| Personal plan p. 6 | Three justified architecture decisions | adr/0001-runtime-target.md, 0002-artifact-storage-and-signing.md, 0003-agent-communication.md | Proposed choices recorded for review |
| Personal plan p. 6 | Component diagram, six responsibilities, trust, five failure cases, initial endpoints and Section 1.4 targets | W01_Platform_Design_Doc.docx | Prepared for review; later behaviours are specifications |
| Personal plan p. 6 | Repository foundation and one-command stack | Repository layout, Makefile, infra/local/compose.yaml | Verified locally |
| Personal plan p. 6 | Python pins, lockfile, environment-based configuration and no committed secrets | pyproject.toml, uv.lock, .python-version, registry/config.py, .env.example | Verified locally |
| Personal plan pp. 4 and 6 | Terraform variable example and no unintended cloud spend | infra/terraform/terraform.tfvars.example; no-provider, no-resource scaffold | Verified locally; no Azure apply |
| Personal plan p. 6 | JSON logs with request-id on every line | registry/logging.py, middleware and correlated request evidence | Verified locally |
| Personal plan pp. 6–7 | Genuine readiness and non-200 when object storage is stopped | Authenticated probes, real dependency-stop smoke and slow-probe tests | Verified locally |
| Personal plan p. 6 | At least three passing tests and a coverage report | 18 tests passed, 89.61 percent branch-aware coverage | Verified locally; coverage.xml generated and ignored |
| Personal plan p. 6 and mentor clarification | Green GitHub Actions with every named gate | .github/workflows/ci.yml includes Python, Terraform, tflint and gitleaks | Defined; real remote run pending |
| Personal plan p. 7 | Deliberately broken branch fails format and secret gates | Isolated local rejection fixtures exited 3 and 1 | Local rejection verified; remote branch evidence pending |
| Personal plan pp. 6 and 14 | 300–500-word architecture reflection | ../weekly/Wk-01-Log.md | 418 body words; 431 including heading/date; reflects Xiaoyu's storage and signing concern |

Local runtime evidence and its limits are in W01_Validation_Report.md. No remote
CI badge or completed rollout benchmark is claimed.

## Decisions to review before submission

1. Treat the fleet counts, packaged sizes, bandwidths and update schedules as
   planning assumptions; replace them if the supervisor supplies authoritative data.
2. Confirm the three choices: Compose locally with later Azure Container Apps;
   content-addressed storage and Ed25519; jittered agent-initiated HTTPS pull.
3. Confirm the weekly reflection describes the reasoning Xiaoyu is prepared to
   explain. The communication protocol is a design decision, not a Week 1 feature.
4. Publish the reviewed repository, inspect an actual green Actions run, and run
   the required deliberately broken remote checks before declaring all gates met.

The original Dropbox documents and personal plan remain outside the repository.
Week 2 robot APIs and subsequent model/agent/rollout features are not added to the
Week 1 implementation. The other platforms provide requirements context rather
than separate systems or duplicated D1–D7 work.
