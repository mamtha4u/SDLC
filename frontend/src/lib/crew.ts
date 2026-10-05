/** The crew, described for humans. Shared by the login story and (Phase 1) the pipeline view. */
export interface CrewMember {
  key: string;
  persona: string;
  abbr: string;
  role: string;
  accent: string;
  model: string;
  phrase: string; // headline: "The crew <phrase>"
  doing: string; // live ticker line
  receives: { from: string; what: string };
  does: string[];
  produces: string[];
  gate?: string; // what the user approves before the hand-off
  handsTo: string;
}

export const ORION: CrewMember = {
  key: "cto", persona: "Orion", abbr: "CTO", role: "Chief Technology Officer · Orchestrator", accent: "violet",
  model: "Claude Opus 5.5", phrase: "", doing: "",
  receives: { from: "You", what: "Your goal, budget and approvals" },
  does: [
    "Plans the run and assigns work to each agent in order",
    "Checks every output against the requirement before the next agent starts",
    "Creates a least-privilege AWS role per agent and handles access requests",
    "Decides retries and escalations, and runs impact analysis for change requests",
  ],
  produces: ["plan", "access_plan.md", "CHANGELOG.md"],
  gate: "Anything destructive or costly waits for your confirmation",
  handsTo: "Every agent (conducts the whole crew)",
};

export const CREW: CrewMember[] = [
  {
    key: "intake", persona: "Echo", abbr: "BA", role: "Business Analyst", accent: "cyan", model: "Claude Opus 5.5",
    phrase: "captures it.", doing: "Asking who must be told when an order fails",
    receives: { from: "Your business analyst", what: "The business need, in their words, and any documents" },
    does: [
      "Interviews your business analyst in plain English: what the flow does, from where to where, how much, what if it fails",
      "Asks no technical questions: data, technology, platform, code and testing each have their own specialist later",
      "Plays back its understanding until you sign off",
    ],
    produces: ["00_requirement.md"], gate: "You sign off the requirement", handsTo: "Atlas · BA/DA",
  },
  {
    key: "ba", persona: "Atlas", abbr: "BA/DA", role: "Data Analyst", accent: "emerald", model: "Claude Sonnet 5",
    phrase: "maps it.", doing: "Mapping 14 source fields to the target schema",
    receives: { from: "Echo · BA, then your data analyst", what: "00_requirement.md + samples, mapping and rules from the kickoff chat" },
    does: [
      "Asks your data analyst whether the data changes on the way, and how (samples, mapping sheet, rules)",
      "Maps every source field to its target field in the team's mapping format",
      "Defines transformation, validation and default rules precisely",
      "Writes worked examples (happy path + error cases) that Dev and Quinn reuse as tests. No code",
    ],
    produces: ["01_data_mapping.md"], gate: "You approve the mapping", handsTo: "Archie · TA",
  },
  {
    key: "ta", persona: "Archie", abbr: "TA", role: "Technical Architect", accent: "amber", model: "Claude Opus 5.5",
    phrase: "architects it.", doing: "Rendering the architecture diagram with real AWS icons",
    receives: { from: "Atlas · BA/DA, then your technical lead", what: "Requirement + data mapping + the agreed tech stack" },
    does: [
      "Proposes the architecture to your technical lead and agrees the tech stack with them",
      "Writes the HLD with an architecture diagram (real AWS icons, editable in draw.io)",
      "Writes the LLD: exact settings for every service (memory, timeouts, DLQ, IAM…)",
      "Records key decisions and non-functional requirements",
    ],
    produces: ["02_hld.md", "03_lld.md", "architecture.drawio"], gate: "You approve HLD, LLD and diagram", handsTo: "Terra · TP",
  },
  {
    key: "tp", persona: "Terra", abbr: "TP", role: "Platform Engineer", accent: "orange", model: "Claude Sonnet 5",
    phrase: "builds it.", doing: "Infrastructure plan ready: 13 resources to add, waiting for your approval",
    receives: { from: "Archie · TA, then your platform engineer", what: "03_lld.md + platform details from the kickoff chat" },
    does: [
      "Checks existing resources, tags, sizes and standards with your platform engineer",
      "Turns the LLD into infrastructure as code (Terraform, CloudFormation…), validated before you see it",
      "Creates it in AWS first, with placeholder code, once you approve: its own least-privilege role",
      "Lists every resource for you to check in the AWS console; any change comes back as an exact plan",
    ],
    produces: ["infra/", "AWS resources"], gate: "You approve the infrastructure, then check it in AWS", handsTo: "Dev · DE (via Orion and Archie)",
  },
  {
    key: "de", persona: "Dev", abbr: "DE", role: "Data Engineer", accent: "blue", model: "Claude Sonnet 5",
    phrase: "codes it.", doing: "Tests: 52 passed · coverage 99.5% (gate 70%)",
    receives: { from: "Archie · TA, then your developer", what: "The ready infrastructure + mapping + HLD/LLD + coding standards" },
    does: [
      "Agrees code style, libraries, logging and error handling with your developer",
      "Writes the code and its tests in the requirement's stack (pytest, JUnit, Jest…): every worked example, plus one per ticket",
      "Deploys it into Terra's functions himself, with his own role, once Archie's coverage gate is met",
      "Sanity-checks the deploy: one test message through the live flow, then the logs",
    ],
    produces: ["src/", "tests/", "deploy + sanity report"], gate: "You approve the deployed code", handsTo: "Quinn · QA",
  },
  {
    key: "qa", persona: "Quinn", abbr: "QA", role: "QA Engineer", accent: "rose", model: "Claude Sonnet 5",
    phrase: "tests it.", doing: "Live: POST /orders → 200 · message in SQS matches the mapping ✓",
    receives: { from: "Dev · DE, then your tester", what: "The flow, deployed and sanity-checked in AWS + test priorities" },
    does: [
      "Agrees priorities, test data and exit criteria with your tester",
      "Tests every scenario live in AWS with its own role, checking each output against the data mapping",
      "Files every failure as a ticket: code for Dev, infrastructure for Terra",
      "Retests what comes back fixed, then closes or reopens the ticket",
    ],
    produces: ["live_qa.md", "tickets"], gate: "You send the tickets to the fixers, and confirm the final green", handsTo: "Dev / Terra for fixes, or live ✓",
  },
];
