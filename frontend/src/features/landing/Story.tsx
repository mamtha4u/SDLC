import clsx from "clsx";
import { AnimatePresence, motion, useInView, useMotionValueEvent, useScroll, useTransform, type MotionValue } from "framer-motion";
import {
  ArrowRight, Check, Coins, Eye, FileText, KeyRound, Lock, Rocket, ShieldCheck, Sparkles, Trash2, UserRound, X,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { AgentAvatar } from "../../components/AgentAvatar";
import { LogoMark, Wordmark } from "../../components/Logo";
import { Button } from "../../components/ui";
import { CREW, ORION } from "../../lib/crew";
import { celebrate } from "../../lib/fx";
import { ACCENT } from "../../lib/themes";

/** Below the fold of the landing page: what the crew does (a pinned scroll story), who they are, why it's safe, and
 *  a way in. Loaded lazily, after the first screen. */
export default function Story({ onPick, onStart }: {
  onPick: (index: number, e: React.MouseEvent) => void; onStart: (mode: "login" | "register") => void;
}) {
  return (
    <>
      <Marquee />
      <HowItWorks />
      <CrewSection onPick={onPick} />
      <Safety />
      <Finale onStart={onStart} />
      <footer className="mx-auto flex max-w-[1280px] flex-col items-center justify-between gap-3 px-6 pb-10 pt-4 text-xs text-muted sm:flex-row">
        <Wordmark size={28} />
        <span>Sandbox POC · Claude on Amazon Bedrock · every AWS action audited</span>
      </footer>
    </>
  );
}

/* ── shared ──────────────────────────────────────────────────────────────── */
function Heading({ kicker, title, sub, center, size = "clamp(1.9rem, 1.2rem + 2.6vw, 3.4rem)" }: { kicker: string; title: ReactNode; sub?: string; center?: boolean; size?: string }) {
  return (
    <motion.div initial={{ opacity: 0, y: 24 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-80px" }}
      transition={{ type: "spring", stiffness: 120, damping: 20 }} className={clsx("max-w-3xl", center && "mx-auto text-center")}>
      <p className="text-gradient text-xs font-bold uppercase tracking-[0.22em]">{kicker}</p>
      <h2 className="mt-3 font-display font-bold leading-[1.05] tracking-tight" style={{ fontSize: size }}>{title}</h2>
      {sub && <p className="mt-4 text-[clamp(0.95rem,0.85rem+0.3vw,1.1rem)] text-muted">{sub}</p>}
    </motion.div>
  );
}

/* ── 1 · marquee: things the crew writes and builds ─────────────────────── */
const FLOWS = ["XML orders → JSON events on SQS FIFO", "Invoices → S3 + a DynamoDB index", "Standby aircraft → the crew app",
  "Roster changes → EventBridge", "Bag scans → the tracking API", "Partner files → validated events"];
const FILES = ["00_requirement.md", "01_data_mapping.md", "02_hld.md", "03_lld.md", "architecture.drawio", "infra/ · Terraform or CloudFormation",
  "src/ · your language", "tests · 52 passed", "deploy_plan.md · 13 to add", "live_qa.md · 5/5 ✓", "CHANGELOG.md"];

function Marquee() {
  const row = (items: string[], reverse: boolean, speed: string, icon: ReactNode) => (
    <div className="marquee fade-x overflow-hidden py-1.5">
      <div className="marquee-track flex w-max gap-3" style={{ ["--marquee-speed" as string]: speed, animationDirection: reverse ? "reverse" : "normal" }}>
        {[...items, ...items].map((t, i) => (
          <span key={i} className="glass inline-flex items-center gap-2 whitespace-nowrap rounded-full px-4 py-2 text-sm">
            {icon}{t}
          </span>
        ))}
      </div>
    </div>
  );
  return (
    <section className="relative py-10" aria-label="What the crew builds">
      {row(FLOWS, false, "52s", <span className="h-1.5 w-1.5 rounded-full bg-primary-2" />)}
      {row(FILES, true, "64s", <FileText className="h-3.5 w-3.5 text-primary" />)}
    </section>
  );
}

/* ── 2 · how it works: a pinned scroll story ─────────────────────────────── */
const STEPS = [
  { agent: "intake", who: "You + Echo", title: "Say what you need, in plain words",
    body: "Echo interviews you one question at a time, with tap-to-answer options, and plays it back until you sign it off." },
  { agent: "ba", who: "Atlas · BA/DA", title: "Every field mapped. Every example checked.",
    body: "Source-to-target rules and worked examples, checked against the mapping table before you ever see them." },
  { agent: "ta", who: "Archie · TA", title: "A real architecture, in draw.io",
    body: "HLD, LLD and an editable diagram with real AWS icons, plus the quality gates the code must clear." },
  { agent: "tp", who: "Terra · TP", title: "Infrastructure, planned before anything changes",
    body: "Infrastructure as code in the tool your team uses (Terraform, CloudFormation…), validated, shown as an exact plan for your approval, then applied with Terra's own role." },
  { agent: "de", who: "Dev · DE", title: "Code that clears the gate, in your stack",
    body: "Code and tests in the language the requirement asks for (Python with pytest, Java with JUnit, Node with Jest…), run in a sealed sandbox until coverage clears Archie's gate." },
  { agent: "qa", who: "Quinn · QA", title: "Tested live, with evidence",
    body: "Real HTTPS calls, real queues, real logs. Every failure becomes a bug for Dev, then a retest." },
  { agent: "cto", who: "You", title: "You approve. It's live.",
    body: "Nothing moves without your click. Every AWS action is audited, and one Tear down removes it all." },
];
const ACC: Record<string, string> = { intake: "cyan", ba: "emerald", ta: "amber", tp: "orange", de: "blue", qa: "rose", cto: "violet" };

/** One segment of the story's progress bar: fills continuously while you scroll through its step. */
function Segment({ i, progress }: { i: number; progress: MotionValue<number> }) {
  const n = STEPS.length;
  const fill = useTransform(progress, [i / n, (i + 1) / n], [0, 1], { clamp: true });
  return (
    <span className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-[color-mix(in_srgb,var(--text)_12%,transparent)]">
      <motion.span className="absolute inset-0 origin-left rounded-full bg-[linear-gradient(90deg,var(--primary),var(--primary-2))]" style={{ scaleX: fill }} />
    </span>
  );
}

function HowItWorks() {
  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start start", "end end"] });
  const [step, setStep] = useState(0);
  useMotionValueEvent(scrollYProgress, "change", (v) => setStep(Math.min(STEPS.length - 1, Math.max(0, Math.floor(v * STEPS.length * 0.999)))));
  const pct = useTransform(scrollYProgress, (v) => `${Math.round(Math.min(1, Math.max(0, v)) * 100)}%`);
  const jump = (i: number) => {
    const el = ref.current;
    if (!el) return;
    const top = el.getBoundingClientRect().top + window.scrollY;
    window.scrollTo({ top: top + ((el.offsetHeight - window.innerHeight) * (i + 0.5)) / STEPS.length, behavior: "smooth" });
  };
  const s = STEPS[step];
  return (
    <section id="how" className="relative scroll-mt-20">
      {/* desktop: pinned, the stage changes as you scroll */}
      <div ref={ref} className="relative hidden lg:block" style={{ height: `${STEPS.length * 48 + 100}vh` }}>
        <div className="sticky top-0 flex h-dvh items-center pb-4 pt-[84px]">
          <div className="mx-auto grid w-full max-w-[1320px] grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] items-center gap-14 px-10">
            <div>
              <Heading kicker="How it works" title={<>From one sentence to a <span className="text-gradient-anim">live AWS flow</span></>}
                size="clamp(1.6rem, min(1.2rem + 2.6vw, 6.4vh), 3.4rem)" />
              <ol className="relative mt-[clamp(0.75rem,3vh,2rem)] space-y-[clamp(0px,0.6vh,4px)]">
                <div className="absolute bottom-3 left-[16px] top-3 w-[4px] rounded-full bg-[color-mix(in_srgb,var(--text)_12%,transparent)]" />
                <motion.div className="absolute left-[16px] top-3 w-[4px] origin-top rounded-full bg-[linear-gradient(var(--primary),var(--primary-2))]"
                  style={{ scaleY: scrollYProgress, height: "calc(100% - 24px)", boxShadow: "0 0 12px var(--primary-2)" }} />
                {STEPS.map((x, i) => (
                  <li key={x.title}>
                    <button onClick={() => jump(i)} className={clsx("relative flex w-full items-center gap-3 rounded-[14px] px-1 py-[clamp(2px,0.8vh,6px)] text-left transition-opacity",
                      i === step ? "opacity-100" : "opacity-50 hover:opacity-80")}>
                      <span className={clsx("relative z-10 grid h-9 w-9 shrink-0 place-items-center rounded-full border-2 text-xs font-bold transition-colors",
                        i < step ? "border-transparent bg-[linear-gradient(135deg,var(--primary),var(--primary-2))] text-on-primary"
                          : i === step ? "border-primary-2 bg-surface text-primary-2" : "border-line bg-surface text-muted")}>
                        {i < step ? <Check className="h-4 w-4" strokeWidth={3} /> : i + 1}
                      </span>
                      <span className="min-w-0">
                        <span className="block text-[11px] font-semibold uppercase tracking-wider text-muted">{x.who}</span>
                        <span className="block truncate font-display text-[15px] font-semibold">{x.title}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ol>
              <AnimatePresence>
                {step < STEPS.length - 1 && (
                  <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                    className="mt-[clamp(0.5rem,2vh,1.25rem)] flex items-center gap-2 pl-1 text-xs font-medium text-muted">
                    <span className="relative grid h-6 w-4 justify-center rounded-full border-2 border-current pt-1">
                      <span className="float-y h-1.5 w-1 rounded-full bg-current" />
                    </span>
                    Keep scrolling: step {step + 1} of {STEPS.length} · <motion.span className="tabular">{pct}</motion.span>
                  </motion.p>
                )}
              </AnimatePresence>
            </div>
            <div className="glass sheen elev relative h-[min(560px,calc(100dvh-110px))] overflow-hidden rounded-[30px] p-6">
              <div className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full opacity-30 blur-3xl transition-colors duration-700"
                style={{ background: ACCENT[ACC[s.agent]] }} />
              <div className="relative flex items-center gap-3">
                {s.agent === "cto" ? <span className="grid h-11 w-11 place-items-center rounded-full bg-[linear-gradient(135deg,var(--primary),var(--primary-2))] text-on-primary"><UserRound className="h-5 w-5" /></span>
                  : <AgentAvatar agent={s.agent} accent={ACC[s.agent]} status="working" size={44} />}
                <div className="min-w-0 flex-1">
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">Step {step + 1} of {STEPS.length} · {s.who}</p>
                  <p className="font-display text-xl font-semibold">{s.title}</p>
                </div>
              </div>
              {/* progress through the whole story, one segment per step, filling as you scroll */}
              <div className="relative mt-3 flex gap-1.5" aria-hidden>
                {STEPS.map((x, i) => <Segment key={x.title} i={i} progress={scrollYProgress} />)}
              </div>
              <p className="relative mt-3 max-w-xl text-sm text-muted">{s.body}</p>
              <div className="relative mt-4 h-[calc(100%-160px)]">
                {/* a crossfade (not "wait for the old one to leave"): fast scrolling, or scrolling back up, can never
                    leave the stage showing another step's mock-up */}
                <AnimatePresence initial={false}>
                  <motion.div key={step} className="absolute inset-0" initial={{ opacity: 0, y: 18 }}
                    animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12, transition: { duration: 0.18 } }} transition={{ duration: 0.32 }}>
                    <Artifact i={step} />
                  </motion.div>
                </AnimatePresence>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* phones and tablets: the same story as cards */}
      <div className="mx-auto max-w-[720px] space-y-5 px-4 py-14 lg:hidden">
        <Heading kicker="How it works" title={<>From one sentence to a <span className="text-gradient-anim">live AWS flow</span></>} />
        {STEPS.map((x, i) => (
          <motion.div key={x.title} initial={{ opacity: 0, y: 30 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-60px" }}
            className="glass sheen rounded-[24px] p-4">
            <div className="flex items-center gap-3">
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-[linear-gradient(135deg,var(--primary),var(--primary-2))] text-xs font-bold text-on-primary">{i + 1}</span>
              <div className="min-w-0">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">{x.who}</p>
                <p className="font-display font-semibold">{x.title}</p>
              </div>
            </div>
            <p className="mt-2 text-sm text-muted">{x.body}</p>
            <div className="relative mt-3 h-[300px]"><Artifact i={i} /></div>
          </motion.div>
        ))}
      </div>
    </section>
  );
}

/* the mock-ups: what each step really produces */
function Artifact({ i }: { i: number }) {
  return [<ChatArt key="c" />, <MappingArt key="m" />, <DiagramArt key="d" />, <TerraformArt key="t" />, <PytestArt key="p" />, <LiveArt key="l" />, <ApproveArt key="a" />][i];
}
const appear = (d: number) => ({ initial: { opacity: 0, y: 10 }, animate: { opacity: 1, y: 0 }, transition: { delay: d, type: "spring" as const, stiffness: 260, damping: 24 } });

function ChatArt() {
  const bubble = (me: boolean, text: ReactNode, d: number) => (
    <motion.div {...appear(d)} className={clsx("flex", me && "justify-end")}>
      <div className={clsx("max-w-[82%] rounded-[16px] px-3.5 py-2 text-[13.5px]", me ? "rounded-br-[6px] bg-primary text-on-primary" : "rounded-tl-[6px] border border-line bg-surface")}>{text}</div>
    </motion.div>
  );
  return (
    <div className="flex h-full flex-col justify-center gap-3">
      {bubble(false, <>Hi! What should this flow do, in a sentence or two?</>, 0.1)}
      {bubble(true, <>Partners POST XML orders. Map them to our JSON event and queue them, FIFO per order.</>, 0.5)}
      {bubble(false, <>Clear. Which fields are <b>mandatory</b>?</>, 0.9)}
      <motion.div {...appear(1.2)} className="flex flex-wrap gap-2 pl-1">
        {["OrderId, Name, Amount", "All of them", "Not sure yet"].map((q, k) => (
          <span key={q} className={clsx("rounded-full border px-3 py-1 text-xs font-medium", k === 0 ? "border-primary bg-primary/15 text-primary" : "border-line text-muted")}>{q}</span>
        ))}
      </motion.div>
      <motion.div {...appear(1.7)} className="mt-1 flex items-center gap-2 rounded-[14px] border border-success/40 bg-success/10 px-3 py-2 text-sm">
        <Check className="h-4 w-4 text-success" strokeWidth={3} /><b>00_requirement.md</b><span className="text-muted">· v1.1 · signed off after 2 reviews</span>
      </motion.div>
    </div>
  );
}

function MappingArt() {
  const rows = [["Order/OrderId", "order_id", "copy"], ["Order/Customer/Name", "customer.name", "copy · trim"],
    ["Order/Amount", "amount", "derived · 2 decimals"], ["Order/Customer/Email", "customer.email", "derived · PII masked"], ["–", "currency", "constant · EUR"]];
  return (
    <div className="flex h-full flex-col justify-center gap-2">
      {rows.map(([a, b, r], k) => (
        <motion.div key={b} {...appear(0.1 + k * 0.12)} className="grid grid-cols-[minmax(0,1fr)_18px_minmax(0,1fr)_auto] items-center gap-2">
          <span className="truncate rounded-[10px] border border-line bg-bg-2/70 px-2.5 py-1.5 font-mono text-[12px]">{a}</span>
          <ArrowRight className="h-4 w-4 text-primary-2" />
          <span className="truncate rounded-[10px] border border-primary/35 bg-primary/[0.07] px-2.5 py-1.5 font-mono text-[12px] font-semibold">{b}</span>
          <span className="hidden rounded-full bg-bg-2 px-2 py-0.5 text-[10.5px] text-muted sm:inline">{r}</span>
        </motion.div>
      ))}
      <motion.div {...appear(0.9)} className="mt-2 flex flex-wrap gap-1.5">
        {["valid_order", "currency_given", "whitespace_name", "not_xml", "wrong_root", "missing_amount"].map((e, k) => (
          <motion.span key={e} initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ delay: 1 + k * 0.1 }}
            className="inline-flex items-center gap-1 rounded-full bg-success/12 px-2 py-0.5 font-mono text-[11px] text-success">
            <Check className="h-3 w-3" strokeWidth={3} />{e}
          </motion.span>
        ))}
      </motion.div>
    </div>
  );
}

const AWS = { api: "#8c4fff", lambda: "#ed7100", sqs: "#e7157b", logs: "#e7157b" }; // AWS service colours (data, not theme)
function DiagramArt() {
  const node = (label: string, sub: string, color: string, glyph: string, d: number) => (
    <motion.div {...appear(d)} className="flex w-[30%] flex-col items-center gap-1.5 text-center">
      <span className="grid h-14 w-14 place-items-center rounded-[14px] font-bold text-white shadow-lg" style={{ background: color }}>{glyph}</span>
      <span className="text-[13px] font-semibold">{label}</span>
      <span className="text-[11px] text-muted">{sub}</span>
    </motion.div>
  );
  return (
    <div className="relative flex h-full flex-col justify-center rounded-[18px] p-4"
      style={{ background: "radial-gradient(color-mix(in srgb, var(--text) 12%, transparent) 1px, transparent 1.2px) 0 0 / 16px 16px" }}>
      <div className="relative flex items-start justify-between">
        <div className="absolute left-[15%] right-[15%] top-7 h-[3px] overflow-hidden rounded-full bg-line"><div className="rail-flow h-full w-full" /></div>
        {node("API Gateway", "REST · API key", AWS.api, "API", 0.1)}
        {node("λ transform", "python3.14", AWS.lambda, "λ", 0.3)}
        {node("SQS FIFO", "dedup by OrderId", AWS.sqs, "SQS", 0.5)}
      </div>
      <motion.div {...appear(0.8)} className="mt-6 flex justify-center gap-3">
        <span className="rounded-[12px] border border-dashed border-line px-3 py-1.5 text-xs">Logger layer · mask_pii()</span>
        <span className="rounded-[12px] border border-dashed border-line px-3 py-1.5 text-xs">CloudWatch · 14 days</span>
      </motion.div>
      <motion.div {...appear(1.1)} className="mt-5 flex items-center justify-center gap-2 text-xs text-muted">
        <ShieldCheck className="h-3.5 w-3.5 text-success" /> Quality gate: coverage ≥ 80% · editable in draw.io
      </motion.div>
    </div>
  );
}

function Terminal({ lines }: { lines: [string, string?][] }) {
  return (
    <div className="flex h-full flex-col overflow-hidden rounded-[18px] border border-line bg-bg-2/80">
      <div className="flex items-center gap-1.5 border-b border-line px-3 py-2">
        {["var(--danger)", "var(--warning)", "var(--success)"].map((c) => <span key={c} className="h-2.5 w-2.5 rounded-full" style={{ background: c }} />)}
        <span className="ml-2 font-mono text-[11px] text-muted">orkestra · sandbox</span>
      </div>
      <div className="flex-1 space-y-1 overflow-hidden p-4 font-mono text-[12.5px]">
        {lines.map(([t, tone], k) => (
          <motion.p key={k} className="truncate" initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.15 + k * 0.16 }}
            style={{ color: tone ?? "var(--text)" }}>{t}</motion.p>
        ))}
      </div>
    </div>
  );
}
const ok = "var(--success)", dim = "var(--text-muted)", hi = "var(--primary-2)";

/** The stack follows the requirement: the mock-up cycles through the options, with tabs showing which one is on. */
function Stacks({ options, render }: { options: { label: string }[]; render: (i: number) => ReactNode }) {
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => setI((v) => (v + 1) % options.length), 4200);
    return () => window.clearInterval(t);
  }, [options.length]);
  return (
    <div className="flex h-full flex-col gap-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-[11px] font-semibold uppercase tracking-wider text-muted">Your stack</span>
        {options.map((o, k) => (
          <button key={o.label} type="button" onClick={() => setI(k)}
            className={clsx("rounded-full px-2.5 py-1 text-[11.5px] font-semibold transition-colors duration-300",
              k === i ? "bg-primary text-on-primary shadow-[0_6px_16px_-8px_var(--primary)]" : "bg-bg-2/70 text-muted hover:text-text")}>
            {o.label}
          </button>
        ))}
        <span className="ml-auto text-[11px] text-muted">chosen by the requirement</span>
      </div>
      <div className="relative min-h-0 flex-1">
        <AnimatePresence initial={false}>
          <motion.div key={i} className="absolute inset-0" initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -16 }} transition={{ duration: 0.3 }}>
            {render(i)}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}

const IAC: { label: string; lines: [string, string?][] }[] = [
  { label: "Terraform", lines: [["$ terraform plan", hi], ["  + aws_api_gateway_rest_api.api", ok], ["  + aws_lambda_function.transform", ok],
    ["  + aws_sqs_queue.orders (fifo)", ok], ["  + aws_iam_role.transform (boundary ✓)", ok], ["  … 9 more", dim],
    ["Plan: 13 to add, 0 to change, 0 to destroy.", "var(--text)"], ["✓ approved by you · applying with Terra's role", hi], ["Apply complete! Resources: 13 added.", ok]] },
  { label: "CloudFormation", lines: [["$ aws cloudformation create-change-set", hi], ["  + AWS::ApiGateway::RestApi   Api", ok], ["  + AWS::Lambda::Function      Transform", ok],
    ["  + AWS::SQS::Queue            Orders (FIFO)", ok], ["  + AWS::IAM::Role             TransformRole (boundary ✓)", ok], ["  … 9 more", dim],
    ["Change set: 13 to add, 0 to modify, 0 to remove.", "var(--text)"], ["✓ approved by you · executing with Terra's role", hi], ["CREATE_COMPLETE · 13 resources", ok]] },
];
function TerraformArt() {
  return <Stacks options={IAC} render={(i) => <Terminal lines={IAC[i].lines} />} />;
}

const CODE: { label: string; cov: number; lines: [string, string?][] }[] = [
  { label: "Python · pytest", cov: 99.5, lines: [["$ pytest --cov", hi], ["tests/test_examples.py ......", ok], ["tests/test_handler.py ..........", ok],
    ["tests/test_transform.py ....................", ok], ["tests/test_bug_001_currency.py .", ok], ["52 passed in 3.4s", ok]] },
  { label: "Java · JUnit", cov: 96.8, lines: [["$ mvn verify", hi], ["[INFO] Running OrderTransformTest", dim], ["[INFO] Tests run: 38, Failures: 0, Errors: 0", ok],
    ["[INFO] Running HandlerTest", dim], ["[INFO] Tests run: 14, Failures: 0, Errors: 0", ok], ["[INFO] BUILD SUCCESS · JaCoCo 96.8%", ok]] },
  { label: "Node · Jest", cov: 97.2, lines: [["$ npx jest --coverage", hi], ["PASS  tests/transform.test.ts", ok], ["PASS  tests/handler.test.ts", ok],
    ["PASS  tests/examples.test.ts", ok], ["Tests: 52 passed, 52 total", ok], ["All files | 97.2 % lines", ok]] },
];
function PytestArt() {
  return (
    <Stacks options={CODE} render={(i) => (
      <div className="flex h-full flex-col gap-3">
        <div className="min-h-0 flex-1"><Terminal lines={CODE[i].lines} /></div>
        <motion.div {...appear(1.2)} className="rounded-[14px] border border-line bg-surface p-3">
          <div className="mb-1.5 flex justify-between text-xs"><b>Line coverage</b><span className="tabular text-success">{CODE[i].cov}% · gate 70%</span></div>
          <div className="relative h-2.5 rounded-full bg-bg-2">
            <motion.div className="h-full rounded-full bg-[linear-gradient(90deg,var(--success),var(--primary-2))]" initial={{ width: 0 }}
              animate={{ width: `${CODE[i].cov}%` }} transition={{ delay: 1.3, duration: 1 }} />
            <span className="absolute -top-1 w-[2px] rounded bg-warning" style={{ left: "70%", height: 18 }} title="Archie's gate" />
          </div>
        </motion.div>
      </div>
    )} />
  );
}
function LiveArt() {
  const checks = ["POST /orders → 200 in 182 ms", "Message in orders.fifo matches valid_order", "Bad XML → 400, queue stays empty",
    "Correlation id on every log line", "No PII in the logs"];
  return (
    <div className="flex h-full flex-col justify-center gap-2">
      {checks.map((c, k) => (
        <motion.div key={c} {...appear(0.15 + k * 0.25)} className="flex items-center gap-3 rounded-[14px] border border-line bg-surface px-3 py-2.5 text-sm">
          <motion.span initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ delay: 0.35 + k * 0.25, type: "spring", stiffness: 500, damping: 16 }}
            className="grid h-6 w-6 place-items-center rounded-full bg-success text-bg"><Check className="h-3.5 w-3.5" strokeWidth={3} /></motion.span>
          <span className="min-w-0 flex-1">{c}</span><span className="font-mono text-[11px] text-muted">LIVE-0{k + 1}</span>
        </motion.div>
      ))}
      <motion.p {...appear(1.6)} className="mt-1 text-center text-sm font-semibold text-success">5/5 live checks passed · as orkestra-…-quinn</motion.p>
    </div>
  );
}
function ApproveArt() {
  const btn = useRef<HTMLButtonElement>(null);
  const seen = useInView(btn, { once: true, amount: 1 }); // only when it's really on screen (the phone copy mounts hidden on desktop)
  const [done, setDone] = useState(false);
  useEffect(() => {
    if (!seen) return;
    const t = window.setTimeout(() => { setDone(true); celebrate(btn.current, true); }, 1300);
    return () => window.clearTimeout(t);
  }, [seen]);
  return (
    <div className="flex h-full flex-col justify-center gap-4">
      <motion.div {...appear(0.1)} className="beam-border relative overflow-hidden rounded-[20px] border border-warning/50 bg-surface p-4"
        style={{ ["--beam-1" as string]: "var(--warning)" }}>
        <p className="text-[11px] font-semibold uppercase tracking-wider text-warning">Your approval is needed</p>
        <p className="font-display text-lg font-semibold">The flow works live in AWS (5/5 checks)</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <span className="inline-flex h-9 items-center rounded-[12px] border border-line px-3 text-sm">Review</span>
          <motion.button ref={btn} type="button" animate={done ? { scale: [1, 0.94, 1] } : {}}
            className="inline-flex h-9 items-center gap-2 rounded-[12px] bg-[linear-gradient(120deg,var(--primary),var(--primary-2))] px-4 text-sm font-semibold text-on-primary">
            <Rocket className="h-4 w-4" />Approve & continue
          </motion.button>
        </div>
      </motion.div>
      <AnimatePresence>
        {done && (
          <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} className="rounded-[20px] border border-success/40 bg-success/10 p-4 text-center">
            <p className="font-display text-2xl font-bold text-success">Live in AWS ✓</p>
            <p className="mt-1 text-sm text-muted">Partner orders ingest · v1.1 · completed. Tear down any time.</p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/* ── 3 · the crew ────────────────────────────────────────────────────────── */
function CrewSection({ onPick }: { onPick: (index: number, e: React.MouseEvent) => void }) {
  const card = "spotlight sheen elev lift group relative overflow-hidden rounded-[24px] border border-line bg-surface p-5 text-left";
  const enter = (k: number) => ({ initial: { opacity: 0, y: 30 }, whileInView: { opacity: 1, y: 0 }, viewport: { once: true, margin: "-60px" },
    transition: { delay: k * 0.06, type: "spring" as const, stiffness: 140, damping: 20 } });
  return (
    <section id="crew" className="mx-auto max-w-[1320px] scroll-mt-20 px-4 py-20 sm:px-10">
      <Heading kicker="Meet the crew" title={<>Seven specialists. <span className="text-gradient-anim">One conductor.</span></>}
        sub="Each agent does one job, the way your team already works, and hands over to the next only after you approve." />
      <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <motion.button {...enter(0)} onClick={(e) => onPick(-1, e)} className={clsx(card, "sm:col-span-2 lg:row-span-2 lg:p-7")}>
          <div className="pointer-events-none absolute -right-16 -top-16 h-64 w-64 rounded-full opacity-30 blur-3xl" style={{ background: ACCENT.violet }} />
          <div className="flex items-center gap-4">
            <div className="relative"><span className="sonar absolute inset-0 rounded-[16px] border border-primary/60" /><LogoMark size={64} /></div>
            <div>
              <p className="font-display text-3xl font-bold">Orion</p>
              <p className="text-sm text-muted">{ORION.role} · {ORION.model}</p>
            </div>
          </div>
          <ul className="mt-6 space-y-2.5">
            {ORION.does.map((d) => <li key={d} className="flex gap-2.5 text-[15px]"><Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-primary-2" />{d}</li>)}
          </ul>
          <p className="mt-6 inline-flex items-center gap-2 rounded-full border border-line px-3 py-1.5 text-xs text-muted"><Lock className="h-3.5 w-3.5" />Holds the higher AWS permission, fenced to orkestra-* only</p>
        </motion.button>
        {CREW.map((c, i) => (
          <motion.button key={c.key} {...enter(i + 1)} onClick={(e) => onPick(i, e)} className={card}>
            <div className="pointer-events-none absolute -right-10 -top-10 h-32 w-32 rounded-full opacity-0 blur-2xl transition-opacity duration-500 group-hover:opacity-40" style={{ background: ACCENT[c.accent] }} />
            <div className="flex items-center gap-3">
              <AgentAvatar agent={c.key} accent={c.accent} status="done" size={44} plain />
              <div className="min-w-0">
                <p className="font-display text-lg font-semibold leading-tight">{c.persona} <span className="font-mono text-[11px] font-bold" style={{ color: ACCENT[c.accent] }}>{c.abbr}</span></p>
                <p className="truncate text-xs text-muted">{c.role}</p>
              </div>
            </div>
            <p className="mt-3 line-clamp-2 text-[13.5px] text-muted">{c.does[0]}</p>
            <div className="mt-3 flex flex-wrap gap-1">
              {c.produces.map((p) => <span key={p} className="rounded-full bg-bg-2 px-2 py-0.5 font-mono text-[10.5px]">{p}</span>)}
            </div>
          </motion.button>
        ))}
        <motion.div {...enter(7)} className={card}>
          <div className="flex items-center gap-3">
            <AgentAvatar agent="guide" accent="indigo" status="done" size={44} plain />
            <div><p className="font-display text-lg font-semibold">Sage</p><p className="text-xs text-muted">Project guide · Claude Sonnet 5</p></div>
          </div>
          <p className="mt-3 text-[13.5px] text-muted">Ask anything about your project, any time. Sage answers from the real files and cites them.</p>
        </motion.div>
        <motion.div {...enter(8)} className={clsx(card, "border-primary/40")}>
          <div className="flex items-center gap-3">
            <span className="grid h-11 w-11 place-items-center rounded-full bg-[linear-gradient(135deg,var(--primary),var(--primary-2))] text-on-primary"><UserRound className="h-5 w-5" /></span>
            <div><p className="font-display text-lg font-semibold">You</p><p className="text-xs text-muted">The approver</p></div>
          </div>
          <p className="mt-3 text-[13.5px] text-muted">Every hand-off waits for your click. Anything new or different: one Change request.</p>
        </motion.div>
      </div>
    </section>
  );
}

/* ── 4 · safe by design ──────────────────────────────────────────────────── */
function Safety() {
  const rings = [
    { r: 170, label: "Your shared AWS account · everything else untouched", dash: "2 8", spin: 80, tone: "var(--text-muted)" },
    { r: 128, label: "Fence · only what Orkestra created", dash: "10 6", spin: -55, tone: "var(--primary)" },
    { r: 88, label: "Boundary · a ceiling no agent passes", dash: "4 4", spin: 40, tone: "var(--primary-2)" },
    { r: 50, label: "Agent role · one task, one project", dash: "", spin: 0, tone: "var(--success)" },
  ];
  const points = [
    { icon: ShieldCheck, title: "Hands off what isn't ours", text: "Colleagues' resources, other projects, account-wide settings: the crew can't change them. AWS itself refuses." },
    { icon: KeyRound, title: "A role per agent, per task", text: "Orion writes each agent's permissions from the plan: only the services it needs. You read the exact policy and approve it." },
    { icon: Eye, title: "Nothing moves without you", text: "A human gate at every step, from the requirement to live. Change anything, any time, with one Change request." },
    { icon: Trash2, title: "One-click tear down", text: "Everything a project creates is tagged and tracked, so Tear down removes all of it. Nothing is left behind." },
    { icon: Coins, title: "A budget on every project", text: "Exact tokens and dollars for every model call, per agent. The crew pauses at your budget." },
  ];
  return (
    <section id="safety" className="mx-auto grid max-w-[1320px] scroll-mt-20 items-center gap-12 px-4 py-20 sm:px-10 lg:grid-cols-2">
      <div className="space-y-6">
      <motion.div initial={{ opacity: 0, scale: 0.92 }} whileInView={{ opacity: 1, scale: 1 }} viewport={{ once: true, margin: "-80px" }}
        transition={{ type: "spring", stiffness: 90, damping: 18 }} className="relative mx-auto aspect-square w-full max-w-[420px]">
        <svg viewBox="-200 -200 400 400" className="h-full w-full">
          {rings.map((g) => (
            <g key={g.r} style={{ transformOrigin: "0 0", animation: g.spin ? `spin-slow ${Math.abs(g.spin)}s linear infinite ${g.spin < 0 ? "reverse" : ""}` : undefined }}>
              <circle r={g.r} fill="none" stroke={g.tone} strokeOpacity={0.8} strokeWidth={g.r === 50 ? 2.5 : 1.6} strokeDasharray={g.dash} />
            </g>
          ))}
          <circle r={46} fill="color-mix(in srgb, var(--success) 14%, transparent)" />
        </svg>
        <div className="absolute inset-0 grid place-items-center">
          <span className="grid h-16 w-16 place-items-center rounded-full bg-[linear-gradient(135deg,var(--success),var(--primary-2))] text-bg shadow-xl"><KeyRound className="h-7 w-7" /></span>
        </div>
        {rings.map((g) => (
          <span key={g.label} className="glass absolute left-1/2 -translate-x-1/2 -translate-y-1/2 whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] font-semibold"
            style={{ top: `${50 - (g.r / 400) * 100}%`, color: g.tone === "var(--text-muted)" ? "var(--text)" : g.tone }}>{g.label}</span>
        ))}
        <Blocked />
      </motion.div>
      <AuditTrail />
      </div>
      <div>
        <Heading kicker="Safe by design" title={<>Powerful inside a <span className="text-gradient-anim">fence</span>.</>}
          sub="A shared AWS account deserves manners. Orkestra builds only what you approve, only where it's allowed, and shows its working." />
        <div className="mt-8 grid gap-3 sm:grid-cols-2">
          {points.map((p, k) => (
            <motion.div key={p.title} initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ delay: k * 0.08 }}
              className={clsx("spotlight sheen elev rounded-[20px] border border-line bg-surface p-4", k === points.length - 1 && "sm:col-span-2")}>
              <p.icon className="h-5 w-5 text-primary-2" />
              <p className="mt-2 font-semibold">{p.title}</p>
              <p className="mt-1 text-sm text-muted">{p.text}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}

/** The guardrails at work: a rolling audit trail, every action with the role that made it; out-of-bounds ones refused. */
const AUDIT: [string, string, string, string, boolean][] = [
  ["cto", "Orion", "Creates Terra's role", "boundary attached · scoped to one project", true],
  ["tp", "Terra", "Applies the approved plan", "13 resources created, all tagged", true],
  ["qa", "Quinn", "Calls the live API", "POST /orders → 200 in 182 ms", true],
  ["qa", "Quinn", "Tries to create a queue", "refused · outside Quinn's role", false],
  ["de", "Dev", "Reads the live error logs", "read-only · its own project", true],
  ["tp", "Terra", "Tries to delete a colleague's function", "refused · not ours", false],
  ["cto", "Orion", "Tears the project down", "everything removed, roles deleted", true],
];
const AUDIT_ACC: Record<string, string> = { cto: "violet", tp: "orange", qa: "rose", de: "blue" };
function AuditTrail() {
  const [head, setHead] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const seen = useInView(ref, { margin: "-60px" });
  useEffect(() => {
    if (!seen) return;
    const t = window.setInterval(() => setHead((h) => (h + 1) % AUDIT.length), 2200);
    return () => window.clearInterval(t);
  }, [seen]);
  const rows = Array.from({ length: 4 }, (_, k) => (head + k) % AUDIT.length);
  return (
    <div ref={ref} className="glass sheen mx-auto w-full max-w-[520px] overflow-hidden rounded-[22px] p-4">
      <div className="mb-3 flex items-center gap-2 text-xs font-semibold">
        <span className="h-2 w-2 rounded-full bg-success pulse-ring" style={{ ["--ring" as string]: "var(--success)" }} />
        Audit trail <span className="font-normal text-muted">· every action, with the role that made it</span>
      </div>
      <ul className="relative space-y-2">
        <AnimatePresence initial={false} mode="popLayout">
          {rows.map((r) => {
            const [agent, who, what, detail, allowed] = AUDIT[r];
            return (
              <motion.li key={r} layout initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -14 }}
                transition={{ type: "spring", stiffness: 300, damping: 28 }}
                className={clsx("flex items-center gap-3 rounded-[14px] border px-3 py-2", allowed ? "border-line bg-surface/70" : "border-danger/40 bg-danger/[0.07]")}>
                <AgentAvatar agent={agent} accent={AUDIT_ACC[agent]} status="done" size={30} plain />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px]"><b>{who}</b> · {what}</p>
                  <p className={clsx("truncate text-[11.5px]", allowed ? "text-muted" : "font-semibold text-danger")}>{detail}</p>
                </div>
                <span className={clsx("grid h-6 w-6 shrink-0 place-items-center rounded-full", allowed ? "bg-success text-bg" : "bg-danger text-white")}>
                  {allowed ? <Check className="h-3.5 w-3.5" strokeWidth={3} /> : <X className="h-3.5 w-3.5" strokeWidth={3} />}
                </span>
              </motion.li>
            );
          })}
        </AnimatePresence>
      </ul>
    </div>
  );
}

/** Attempts from outside bounce off the fence. */
function Blocked() {
  const ref = useRef<HTMLDivElement>(null);
  const seen = useInView(ref, { once: false, margin: "-100px" });
  const angles = [200, 320, 60, 140];
  return (
    <div ref={ref} className="pointer-events-none absolute inset-0">
      {seen && angles.map((a, k) => {
        const rad = (a * Math.PI) / 180, from = 49, to = 32; // % of the box, from the centre
        return (
          <motion.span key={a} className="absolute grid h-5 w-5 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-danger text-white"
            initial={{ left: `${50 + Math.cos(rad) * from}%`, top: `${50 + Math.sin(rad) * from}%`, opacity: 0, scale: 0.6 }}
            animate={{ left: [`${50 + Math.cos(rad) * from}%`, `${50 + Math.cos(rad) * to}%`, `${50 + Math.cos(rad) * (to + 6)}%`],
              top: [`${50 + Math.sin(rad) * from}%`, `${50 + Math.sin(rad) * to}%`, `${50 + Math.sin(rad) * (to + 6)}%`],
              opacity: [0, 1, 0], scale: [0.6, 1, 0.8] }}
            transition={{ duration: 2.2, delay: 0.6 + k * 1.1, repeat: Infinity, repeatDelay: 3.4, ease: "easeInOut" }}>
            <X className="h-3 w-3" strokeWidth={3} />
          </motion.span>
        );
      })}
    </div>
  );
}

/* ── 5 · finale ──────────────────────────────────────────────────────────── */
function Finale({ onStart }: { onStart: (mode: "login" | "register") => void }) {
  return (
    <section className="px-4 py-20 sm:px-10">
      <motion.div initial={{ opacity: 0, y: 30 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
        className="glass beam-border relative mx-auto max-w-[1100px] overflow-hidden rounded-[36px] px-6 py-16 text-center sm:px-16">
        <div className="pointer-events-none absolute left-1/2 top-0 h-72 w-[42rem] -translate-x-1/2 -translate-y-1/2 rounded-full opacity-40 blur-3xl"
          style={{ background: "radial-gradient(closest-side, var(--primary), transparent)" }} />
        <div className="float-y relative mx-auto w-fit"><LogoMark size={76} /></div>
        <h2 className="relative mt-6 font-display font-bold leading-[1.05] tracking-tight" style={{ fontSize: "clamp(2rem, 1.3rem + 3vw, 3.8rem)" }}>
          Ready to <span className="text-gradient-anim">conduct</span> your first flow?
        </h2>
        <p className="relative mx-auto mt-4 max-w-xl text-muted">Describe it in plain words. Approve each step. Watch it go live in AWS, tested.</p>
        <div className="relative mt-8 flex flex-wrap justify-center gap-3">
          <Button variant="primary" size="lg" className="shimmer" icon={<ArrowRight className="h-5 w-5" />} onClick={() => onStart("login")}>Sign in</Button>
          <Button size="lg" icon={<UserRound className="h-5 w-5" />} onClick={() => onStart("register")}>Create an account</Button>
        </div>
      </motion.div>
    </section>
  );
}
