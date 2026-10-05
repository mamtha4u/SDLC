# ORKESTRA: AI working agreement (read this first)

**Orkestra** is a web portal where a crew of AI agents, conducted by a CTO agent, turns a business requirement into a documented, deployed and tested AWS integration flow. Every hand-off waits for a human approval.
Live (sandbox POC): `http://<ec2-public-ip>` (currently `http://108.131.49.61`, company network only).

Read in this order when you start: **this file**, then [docs/08-progress-log.md](docs/08-progress-log.md) (where we are, what's next), then whichever doc the task needs:

| Doc | Read it when you need to know… |
|---|---|
| [docs/01-overview.md](docs/01-overview.md) | the concept, the crew, the user's product principles |
| [docs/02-architecture.md](docs/02-architecture.md) | how the backend, agents, jobs, events, storage and frontend fit together |
| [docs/03-infrastructure.md](docs/03-infrastructure.md) | which AWS resources exist, why, the IAM/Bedrock gotchas, and how to recreate them |
| [docs/04-deployment.md](docs/04-deployment.md) | how code gets from this repo onto the EC2 host, rollback, tests on the host |
| [docs/05-operations.md](docs/05-operations.md) | daily ops: keys, start/stop, logs, troubleshooting, costs |
| [docs/06-development.md](docs/06-development.md) | conventions, how to add an agent or a stage, frontend rules, visual QA |
| [docs/07-agents.md](docs/07-agents.md) | each agent's inputs, tools, outputs, approval gate and prompt file |
| [docs/reference/](docs/reference/) | the original spec (`master_prompt.md`) and the team's real MS2I-511 documents (the quality bar) |

---

## Ground rules (non-negotiable)

1. **Never touch existing AWS resources.** The sandbox account `144831534428` is shared with colleagues.
   - Never modify, delete, stop, tag or attach anything we didn't create.
   - Existing VPCs and subnets may be *used* (launch into them) but never changed.
   - Everything we create is named `orkestra-*` and tagged `created_by=orkestra` + `project_id=<id>` (`platform` for the platform itself).
   - Cleanup filters by tag **and** prefix, never by name alone.
2. **Ask before anything destructive or costly** in AWS: delete, stop, new paid resources (the user rejected an Elastic IP on cost).
3. **Secrets never leave the backend.** `keys.txt` (the user's SSO session keys) is local-only: never print it, ship it, put it in prompts or logs, or copy it into docs.
4. **Region `eu-west-1` only.** The account's IAM policy denies every other region.
5. **Agents never invent requirements or facts.** Missing info goes back to the user as a question. Version and compatibility claims must be researched with a cited source (the user caught a false "lxml doesn't support Python 3.14" risk; see docs/07-agents.md).
6. **Every stage ends in a human gate.** Nothing advances silently: preview, then approve or request changes, then next stage.

## Repository map

```
ai_agent_project/
├─ CLAUDE.md                ← you are here (AI entry point)
├─ README.md                ← human entry point
├─ docs/                    ← 01–08 guides + reference/ (spec, MS2I-511 docs, diagram samples)
├─ src/
│  ├─ backend/              ← FastAPI app + agents (Python 3.12)
│  │  ├─ app/               ← main.py · api/ · agents/ · orchestrator/ · tools/ · services/ · core/ · db/
│  │  ├─ prompts/           ← one markdown prompt per agent + org_context.md + intake topic map (tunable)
│  │  ├─ config/agents.yaml ← model, effort, max_tokens, prices per agent
│  │  ├─ tests/             ← pytest (test_*.py) + live_*.py manual checks (real Bedrock, cost money)
│  │  └─ requirements.txt
│  ├─ frontend/             ← React 18 + TypeScript + Vite + Tailwind 4 + Framer Motion
│  ├─ infra/
│  │  ├─ host/              ← EC2 user-data (bootstrap) + sandbox Dockerfile
│  │  ├─ iam/               ← the host role's trust + inline policies (JSON)
│  │  └─ scripts/           ← PowerShell ops: provision, deploy, start/stop, tunnel, ec2_run, … (+ remote/*.sh)
│  └─ tools/diagrams/       ← draw.io prototypes (MCP client, layout builder) for the upcoming TA agent
├─ vendor/                  ← local third-party tools (drawio-mcp clone, SSM plugin); not shipped
└─ keys.txt                 ← user's SSO keys (local only, gitignored)
```
The `commands/` folder and `cc.txt` are the user's personal notes: don't edit them.

## How to work in this repo

- **The product runs on EC2, not locally.** The user prefers "deploy and test there".
  - Typical loop: edit, then `.\src\infra\scripts\deploy.ps1 -RunTests` (builds the frontend, ships, runs pytest on the host), then check http://108.131.49.61.
  - Details are in docs/04-deployment.md.
- **AWS access for you** comes from `keys.txt` (SSO, expires after a few hours). On `ExpiredToken`, ask the user to paste fresh keys. The site keeps running regardless; the host has its own role.
- **Run commands on the host** with `.\src\infra\scripts\ec2_run.ps1 "<bash>"`. It uses SSM, so there's no SSH. For long or quoted bash, put a script in `src/infra/scripts/remote/`, upload it to S3 `tools/` and run it there.
- **Local checks are fine and quick:**
  - `cd src/frontend; npm run build` (type-check + bundle).
  - `cd src/backend; .venv\Scripts\python -m pytest -q`. The 2 sandbox tests skip locally; they need the host's Docker.
- **Live agent checks** (`src/backend/tests/live_*.py`) call real Claude and cost money ($0.05–$1). Run them on the host and tell the user the cost.
- **After every work session, update [docs/08-progress-log.md](docs/08-progress-log.md)** (status, what changed, decisions, next). The user explicitly asked for this so work can resume after interruptions.

## Conventions (short version; full list in docs/06-development.md)

- Prompts live in `src/backend/prompts/*.md`, never hardcoded in Python. Model choices live in `config/agents.yaml`.
- Agents are job handlers (`@handler("agent.job")`) running on the shared tool loop (`agents/base.run_loop`).
  - LLM calls go through `agents/llm.call` (metering, budget, retries, audit log).
  - New stages are registered in `orchestrator/flow.STAGES`.
- Project files go through `services/storage.ProjectStore` (versioned; released versions are read-only).
- Frontend uses design tokens only (CSS variables in `src/frontend/src/styles/index.css`) and must work in all 8 themes. Check `/design?theme=<name>`.
- Never trust live streaming through the user's corporate proxy: always keep a polling fallback.
- Write user-facing text in plain English. The user dislikes jargon-heavy, form-heavy, cluttered UI and wants a premium, animated, "wow" UI that stays easy.
