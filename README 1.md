# Orkestra: the AI crew that ships your cloud flows

> You write the requirement. The crew builds it.

Orkestra is a proof-of-concept portal that replaces the manual hand-offs of an integration project (BA/DA → TA → TP → DE → QA) with AI agents. A CTO agent conducts them, and you approve every step.

```
 You ─▶ Echo ─▶ Orion ─▶ Atlas ─▶ Archie ─▶ Terra: Terraform → plan → (you: apply) → AWS ─▶ you check AWS
 ─▶ Dev: code + tests ─▶ Archie reviews it, you check it (ask him anything) ─▶ Dev hands his code + layers to Terra
 ─▶ Terra's plan swaps them in for the placeholder (you: apply) ─▶ Dev checks + tests the whole flow ─▶ you check
 ─▶ Quinn: test plan ─▶ (you approve as test lead) ─▶ live tests ─▶ tickets ─▶ Dev / Terra fix ─▶ retest ─▶ Quinn's sign-off ─▶ (you sign off) ✅
```
Every step waits for your approval and leaves a sign-off document in that agent's popup. Code, layers and infrastructure live in one Terraform state, so Terra's **drift watch** spots changes made in the AWS console (code edits, deleted functions or layers, changed settings): **Restore** them in one approved apply, or keep them as a change request. Plus:
- **Build** (infrastructure and code & deploy, with the hand-over from Dev to Terra), **AWS** (every resource and setting; change many at once; drift; your naming convention; per-agent policy changes), **Testing** (test plan, runs, the test sign-off report), **Tickets**.
- Give any agent a task from its popup. The **Crew room** shows the agents talking, and "behind the scenes" shows each agent's work.
- The **Code** view: sections by phase, each file with what it is, a zip per section; diagrams open as a drawing or as XML. The **Design** tab shows the tech stack.
- **Sage** (✨): ask anything about the project.

## Using it

1. Open the portal on the company network: **http://108.131.49.61** (the address changes if the server is stopped and started; ask the owner or see [docs/05-operations.md](docs/05-operations.md)).
2. **Register** with a username and password; projects are private to you.
3. **New project**, then the **Requirement** tab: upload the requirement document you have, or write it as one message (**What to include** lists the questions a good requirement answers), or just chat. Echo asks only what's missing, a few questions at a time (mostly by tapping). Then **Ask Echo to review**, review again, and **Sign off**.
4. Follow the **Next step** bar at the top of the project. Review each agent's work (plan → **Mapping** → **Design** → **Build** → **AWS** → **Testing**) and approve it. A wording- or diagram-only revision can be accepted with **Accept: nothing to rebuild**. For anything new or different, use the one **Change request** button: Orion routes it to the right agent.
5. Every file is in the **Code** view; the agents' conversation is in the **Crew room**; spend is in **Usage** (the $ chip edits the budget). Ask **Sage** (✨, bottom right) anything about the project.

## Running and changing it

| I want to… | Go to |
|---|---|
| understand the idea | [docs/01-overview.md](docs/01-overview.md) |
| understand the code | [docs/02-architecture.md](docs/02-architecture.md) |
| see what's in AWS and how it was built | [docs/03-infrastructure.md](docs/03-infrastructure.md) |
| deploy a change | [docs/04-deployment.md](docs/04-deployment.md) |
| start/stop the server, check logs, fix problems | [docs/05-operations.md](docs/05-operations.md) |
| add a feature or an agent | [docs/06-development.md](docs/06-development.md) · [docs/07-agents.md](docs/07-agents.md) |
| know where the project stands | [docs/08-progress-log.md](docs/08-progress-log.md) |

Quick deploy (Windows PowerShell, from the repo root, with fresh SSO keys in `keys.txt`):
```powershell
.\src\infra\scripts\deploy.ps1 -RunTests
```

## Stack

Python 3.12 · FastAPI · SQLAlchemy (SQLite) · Claude Opus 5.5 / Sonnet 5 on Amazon Bedrock (Anthropic SDK) · React 18 · TypeScript · Vite · Tailwind 4 · Framer Motion · Docker sandbox · one EC2 in eu-west-1.
