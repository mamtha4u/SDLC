import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { motion } from "framer-motion";
import { AlertTriangle, Check, GitPullRequestArrow, ListChecks, Lock, Paperclip, Radar, RefreshCw, ShieldCheck, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { AgentAvatar } from "../../components/AgentAvatar";
import { Markdown } from "../../components/Markdown";
import { Button, Modal, Skeleton } from "../../components/ui";
import { WorkProgress } from "../../components/WorkProgress";
import { api, ApiError } from "../../lib/api";
import { fileUrl, flowApi, type ChangeRequest } from "../../lib/flow";
import { intakeApi, type IntakeState } from "../../lib/intake";
import { EchoChat } from "./EchoChat";
import { LivingDoc } from "./LivingDoc";
import { ReviewPanel } from "./ReviewPanel";
import { RequirementGuide } from "./RequirementGuide";
import { SignedOff } from "./SignedOff";

type Pane = "chat" | "doc" | "review";

/** Echo Studio: talk on the left, the requirement writes itself on the right. */
export function RequirementTab({ projectId, streaming }: { projectId: string; streaming?: string }) {
  const qc = useQueryClient();
  // Poll while Echo works, so progress shows even when a proxy blocks the live event stream.
  const { data: state, isLoading } = useQuery({
    queryKey: ["intake", projectId], queryFn: () => intakeApi.get(projectId),
    refetchInterval: (q) => (q.state.data?.busy ? 5000 : false),
  });
  const setState = (s: IntakeState) => qc.setQueryData(["intake", projectId], s);
  // While Echo works, poll a tiny endpoint for the live text (typewriter / progress); reload everything when done.
  const busyNow = state?.busy ?? null;
  const { data: live } = useQuery({
    queryKey: ["intake-draft", projectId], queryFn: () => intakeApi.draft(projectId),
    enabled: !!busyNow, refetchInterval: busyNow ? 600 : false,
  });
  useEffect(() => {
    if (busyNow && live && !live.busy) qc.invalidateQueries({ queryKey: ["intake", projectId] });
  }, [live, busyNow, qc, projectId]);
  // Prefer the server draft (always current); fall back to SSE deltas only before the first poll returns.
  // When Echo finishes, the draft is cleared a moment before the final message loads: keep showing the last text
  // meanwhile, otherwise the reply blinks back to "thinking…" and then appears a second time.
  const lastDraft = useRef("");
  const raw = busyNow ? (live ? live.draft ?? "" : streaming ?? "") : "";
  if (raw) lastDraft.current = raw;
  if (!busyNow) lastDraft.current = "";
  const liveText = busyNow ? raw || (live && !live.busy ? lastDraft.current : raw) : "";
  const amending = state?.status === "amending";
  const { data: changes } = useQuery({
    queryKey: ["changes", projectId], queryFn: () => flowApi.changes(projectId), enabled: amending,
  });
  const cr = changes?.find((x) => x.id === state?.active_cr) ?? null;

  const [pane, setPane] = useState<Pane>("chat");
  const [right, setRight] = useState<"doc" | "review">("doc");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [importOpen, setImportOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const desktop = useDesktop();
  const pending = useRef<Record<string, string>>({});
  const timer = useRef<number>();

  useEffect(() => { if (state && !Object.keys(pending.current).length) setAnswers(state.answers); }, [state]);
  const rounds = state?.rounds.length ?? 0;
  useEffect(() => { if (rounds) { setRight("review"); } }, [rounds]);

  if (isLoading || !state) return <div className="grid gap-4 lg:grid-cols-2"><Skeleton className="h-[560px]" /><Skeleton className="h-[560px]" /></div>;
  if (state.status === "signed_off") return <SignedOff projectId={projectId} state={state} />;

  const locked = !!state.busy;
  const c = state.completeness;
  const openBlocking = rounds ? state.rounds[rounds - 1].gaps.filter((g) => g.blocking && !state.gap_answers[g.id]).length : 0;

  const setAnswer = (id: string, v: string) => {
    setAnswers((a) => ({ ...a, [id]: v }));
    pending.current[id] = v;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      const batch = pending.current;
      pending.current = {};
      try {
        const r = await intakeApi.saveAnswers(projectId, batch);
        qc.setQueryData<IntakeState>(["intake", projectId], (old) => old && { ...old, answers: r.answers, completeness: r.completeness });
      } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't save"); }
    }, 500);
  };
  const act = async (fn: () => Promise<IntakeState>, ok: string) => {
    try { setState(await fn()); toast.success(ok); } catch (e) { toast.error(e instanceof ApiError ? e.message : "Something went wrong"); }
  };
  const decide = (d: Record<string, string>, g: Record<string, string>) =>
    intakeApi.decide(projectId, d, g).then(setState).catch((e) => toast.error(e instanceof ApiError ? e.message : "Couldn't save"));

  const steps = amending ? [
    { label: "Orion triaged the change", done: true },
    { label: "Clarify with Echo", done: state.busy === "finalizing" },
    { label: `Sign off ${cr?.version_to ?? "the new version"}`, done: false },
  ] : [
    { label: "Talk to Echo", done: c.required_done === c.required_total || rounds > 0 },
    { label: "Echo reviews", done: rounds >= 1 },
    { label: `Confirm (${Math.min(rounds, state.min_rounds)}/${state.min_rounds})`, done: rounds >= state.min_rounds },
    { label: "Sign off", done: false },
  ];
  const current = steps.findIndex((s) => !s.done);

  return (
    <div className="space-y-3">
      {/* slim header: progress + actions */}
      <div className="glass flex flex-wrap items-center gap-3 rounded-[20px] px-3 py-2.5">
        <ol className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto no-scrollbar">
          {steps.map((s, i) => (
            <li key={s.label} className="flex shrink-0 items-center gap-1.5">
              <span className={clsx("grid h-6 w-6 place-items-center rounded-full text-[11px] font-bold",
                s.done ? "bg-success text-bg" : i === current ? "bg-primary text-on-primary" : "bg-bg-2 text-muted")}>
                {s.done ? <Check className="h-3.5 w-3.5" strokeWidth={3} /> : i + 1}
              </span>
              <span className={clsx("text-[13px] font-medium", i === current ? "text-text" : "text-muted")}>{s.label}</span>
              {i < steps.length - 1 && <span className="mx-1 h-px w-5 bg-line" />}
            </li>
          ))}
        </ol>
        <div className="flex flex-wrap gap-2">
          {amending ? (
            <Button size="sm" variant="primary" className="shimmer" disabled={locked} icon={<ShieldCheck className="h-4 w-4" />}
              title="Echo writes the new requirement version; Orion re-plans and tells the affected agents"
              onClick={() => setConfirmOpen(true)}>Sign off amendment</Button>
          ) : <>
          <Button size="sm" variant="ghost" icon={<ListChecks className="h-4 w-4" />} onClick={() => setImportOpen(true)}
            title="The questions a good requirement answers: write it as one message, or upload the document you have">What to include</Button>
          {rounds === 0 ? (
            <Button size="sm" variant="primary" className="shimmer" disabled={locked} icon={<Sparkles className="h-4 w-4" />}
              title="Echo reads everything and tells you what's missing"
              onClick={() => act(() => intakeApi.review(projectId, "review"), "Echo is reviewing your requirement")}>Ask Echo to review</Button>
          ) : (
            <>
              <Button size="sm" disabled={locked} icon={<RefreshCw className="h-4 w-4" />}
                onClick={() => act(() => intakeApi.review(projectId, "review"), "Echo is reviewing again")}>Review again</Button>
              <Button size="sm" disabled={locked} icon={<Radar className="h-4 w-4" />}
                onClick={() => act(() => intakeApi.review(projectId, "reflect"), "Echo is looking for anything missed")}>What did we miss?</Button>
              <Button size="sm" variant="primary" disabled={locked || rounds < state.min_rounds} icon={<ShieldCheck className="h-4 w-4" />}
                title={rounds < state.min_rounds ? `Echo confirms at least ${state.min_rounds} times first` : "Freeze the requirement"}
                onClick={() => setConfirmOpen(true)}>Sign off</Button>
            </>
          )}
          </>}
        </div>
      </div>

      {amending && <AmendBanner cr={cr} projectId={projectId} />}

      {state.last_error && !state.busy && (
        <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
          className="flex flex-wrap items-center gap-3 rounded-[16px] border border-danger/40 bg-danger/10 px-4 py-3">
          <AlertTriangle className="h-5 w-5 shrink-0 text-danger" />
          <p className="min-w-0 flex-1 text-sm"><b>Echo couldn't finish.</b> {state.last_error.message}</p>
          <Button size="sm" variant="primary" icon={<RefreshCw className="h-4 w-4" />}
            onClick={() => act(() => intakeApi.retry(projectId), "Trying again")}>Try again</Button>
        </motion.div>
      )}

      {/* phone / tablet pane switch */}
      <div className="grid grid-cols-3 rounded-[14px] border border-line bg-surface p-1 lg:hidden">
        {([["chat", "Chat"], ["doc", "Requirement"], ["review", `Review${rounds ? ` (${rounds})` : ""}`]] as const).map(([k, l]) => (
          <button key={k} onClick={() => setPane(k)} disabled={k === "review" && (!rounds || amending)}
            className={clsx("relative h-9 rounded-[10px] text-sm font-medium disabled:opacity-40", pane === k ? "text-on-primary" : "text-muted")}>
            {pane === k && <motion.span layoutId="studio-pane" className="absolute inset-0 rounded-[10px] bg-primary" />}
            <span className="relative">{l}</span>
          </button>
        ))}
      </div>

      <div className="grid gap-4 lg:h-[calc(100dvh-15.5rem)] lg:min-h-[560px] lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
        <section className={clsx("h-[72dvh] min-h-0 rounded-[24px] border border-line bg-bg-2/60 p-3 sm:p-4 lg:h-auto", pane !== "chat" && "hidden lg:block")}>
          <EchoChat projectId={projectId} state={state} onState={setState} live={state.busy === "chatting" ? liveText : ""} onGuide={() => setImportOpen(true)} />
        </section>

        <section className={clsx("flex min-h-0 flex-col rounded-[24px] border border-line bg-bg-2/60", pane === "chat" && "hidden lg:flex")}>
          {rounds > 0 && !amending && (
            <div className="hidden gap-1 border-b border-line p-2 lg:flex">
              {([["doc", "Requirement"], ["review", `Echo's review · round ${rounds}`]] as const).map(([k, l]) => (
                <button key={k} onClick={() => setRight(k)}
                  className={clsx("relative rounded-[10px] px-3.5 py-2 text-sm font-medium", right === k ? "text-text" : "text-muted hover:text-text")}>
                  {right === k && <motion.span layoutId="studio-right" className="absolute inset-0 rounded-[10px] bg-surface shadow-sm" />}
                  <span className="relative">{l}</span>
                </button>
              ))}
            </div>
          )}
          <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto p-3 sm:p-4">
            {(state.busy === "reviewing" || state.busy === "finalizing") && <LiveWork kind={state.busy} text={liveText} version={amending ? cr?.version_to : undefined} projectId={projectId} />}
            {amending ? (
              state.busy !== "finalizing" && (
                <div>
                  <p className="mb-3 inline-flex items-center gap-1.5 rounded-full bg-bg-2 px-3 py-1 text-[11px] font-semibold uppercase tracking-wider text-muted">
                    <Lock className="h-3.5 w-3.5" />Baseline {cr?.version_from ?? ""}: Echo edits only what the change needs
                  </p>
                  <Markdown>{state.requirement_md ?? ""}</Markdown>
                </div>
              )
            ) : rounds > 0 && (desktop ? right === "review" : pane === "review")
              ? <ReviewPanel state={state} onDecide={decide} locked={locked} />
              : <LivingDoc state={state} answers={answers} setAnswer={setAnswer} locked={locked} />}
          </div>
        </section>
      </div>

      <Modal open={importOpen} onClose={() => setImportOpen(false)} title="What a good requirement answers" width={920}>
        <RequirementGuide projectId={projectId} state={state} onState={setState} locked={locked} onDone={() => setImportOpen(false)} />
      </Modal>

      <Modal open={confirmOpen && amending} onClose={() => setConfirmOpen(false)} title={`Sign off ${cr?.label ?? "the amendment"}?`} width={520}>
        <p className="text-sm text-muted">
          Echo writes <b className="text-text">00_requirement.md</b> as <b className="text-text">{cr?.version_to ?? "a new version"}</b>,
          changing only what this request needs. {cr?.version_from ?? "The previous version"} stays untouched.
        </p>
        <ul className="mt-4 space-y-2 text-sm">
          <Tick ok>A diff of exactly what changed is saved with the change request</Tick>
          <Tick ok>Orion re-plans with the impact and tells every affected agent to use the new version</Tick>
          <Tick ok>You approve Orion's revised plan before anyone continues</Tick>
        </ul>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setConfirmOpen(false)}>Not yet</Button>
          <Button variant="primary" icon={<Lock className="h-4 w-4" />} onClick={async () => {
            setConfirmOpen(false);
            await act(() => intakeApi.signoff(projectId), `Signed off. Echo is writing ${cr?.version_to ?? "the new version"}`);
          }}>Sign off & write {cr?.version_to ?? "new version"}</Button>
        </div>
      </Modal>

      <Modal open={confirmOpen && !amending} onClose={() => setConfirmOpen(false)} title="Sign off the requirement?" width={500}>
        <p className="text-sm text-muted">Echo writes <b className="text-text">00_requirement.md</b> from everything agreed. It becomes the frozen source of truth for the crew; later changes go through a change request.</p>
        <ul className="mt-4 space-y-2 text-sm">
          <Tick ok={rounds >= state.min_rounds}>{rounds} review round{rounds === 1 ? "" : "s"} (minimum {state.min_rounds})</Tick>
          <Tick ok={c.missing_required.length === 0}>{c.required_done}/{c.required_total} must-haves covered</Tick>
          <Tick ok={openBlocking === 0}>{openBlocking === 0 ? "No blocking questions open" : `${openBlocking} blocking question(s) still open`}</Tick>
        </ul>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setConfirmOpen(false)}>Not yet</Button>
          <Button variant="primary" icon={<Lock className="h-4 w-4" />} onClick={async () => {
            setConfirmOpen(false);
            await act(() => intakeApi.signoff(projectId, openBlocking > 0), "Signed off. Echo is writing the final document");
          }}>{openBlocking ? "Sign off anyway" : "Sign off & freeze"}</Button>
        </div>
      </Modal>
    </div>
  );
}

function Tick({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <li className="flex items-center gap-2">
      <span className={clsx("grid h-5 w-5 place-items-center rounded-full text-bg", ok ? "bg-success" : "bg-warning")}>
        {ok ? <Check className="h-3 w-3" strokeWidth={3} /> : "!"}
      </span>{children}
    </li>
  );
}

/** The change request Echo is working on: what you asked, what you attached, what Orion needs clarified. */
function AmendBanner({ cr, projectId }: { cr: ChangeRequest | null; projectId: string }) {
  return (
    <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
      className="relative overflow-hidden rounded-[20px] border border-primary/40 bg-primary/[0.07] px-4 py-3.5 sm:px-5">
      <div className="pointer-events-none absolute inset-y-0 left-0 w-1 bg-primary" />
      <div className="flex flex-wrap items-start gap-3">
        <GitPullRequestArrow className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
        <div className="min-w-0 flex-1 space-y-1.5">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-primary">
            {cr ? `${cr.label} · requirement ${cr.version_from} → ${cr.version_to}` : "Change request"}
          </p>
          <p className="font-display font-semibold leading-snug">{cr?.triage?.summary ?? cr?.text ?? "Loading the change…"}</p>
          {cr?.triage && <p className="whitespace-pre-wrap text-sm text-muted">You asked: “{cr.text}”</p>}
          {!!cr?.attachments.length && (
            <div className="flex flex-wrap gap-1.5">
              {cr.attachments.map((a) => (
                <a key={a} href={fileUrl(projectId, a)} title={`Download ${a}`}
                  className="inline-flex items-center gap-1 rounded-full border border-line bg-surface px-2.5 py-0.5 text-xs hover:border-primary hover:text-primary"><Paperclip className="h-3 w-3" />{a}</a>
              ))}
            </div>
          )}
          {!!cr?.triage?.needs_from_user.length && (
            <div className="text-sm">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">Orion asked Echo to confirm with you</p>
              <ul className="mt-0.5 list-disc space-y-0.5 pl-5">{cr.triage.needs_from_user.map((n) => <li key={n}>{n}</li>)}</ul>
            </div>
          )}
          <p className="text-xs text-muted">Answer Echo in the chat. When it has everything, press <b className="text-text">Sign off amendment</b>.</p>
        </div>
      </div>
    </motion.div>
  );
}

/** What Echo is doing right now: review progress lines, or the requirement document appearing as it's written. */
function LiveWork({ kind, text, version, projectId }: { kind: "reviewing" | "finalizing"; text: string; version?: string | null; projectId: string }) {
  const { data: project } = useQuery({ queryKey: ["project", projectId], queryFn: () => api.project(projectId) });
  const echo = project?.agents.find((a) => a.key === "intake");
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      className="mb-4 overflow-hidden rounded-[18px] border border-primary-2/40 bg-surface">
      <div className="flex items-center gap-3 border-b border-line px-4 py-3">
        <AgentAvatar agent="intake" accent="cyan" status="working" size={32} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">{kind === "reviewing" ? "Echo is reviewing, live" : `Echo is writing 00_requirement.md${version ? ` ${version}` : ""}, live`}</p>
          <p className="text-xs text-muted">Opus 5.5 · high effort · you can keep reading meanwhile</p>
          {echo?.status === "working" && <WorkProgress startedAt={echo.job_started_at ?? echo.started_at} expectedS={echo.expected_s} className="mt-1.5 max-w-sm" />}
        </div>
        <span className="ml-auto h-2 w-2 rounded-full bg-primary-2 pulse-ring" style={{ ["--ring" as string]: "var(--primary-2)" }} />
      </div>
      <div className="max-h-[60vh] overflow-y-auto px-4 py-3">
        {kind === "reviewing" ? (
          <ul className="space-y-1.5 text-sm">
            {(text || "Reading everything you've told me…").split("\n").map((l, i) => (
              <motion.li key={l + i} initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} className={i === 0 ? "font-semibold" : ""}>{l}</motion.li>
            ))}
          </ul>
        ) : text ? <Markdown>{text}</Markdown> : <p className="text-sm text-muted">Starting the document…</p>}
      </div>
    </motion.div>
  );
}

/** True at the lg breakpoint and up (two panes side by side). */
function useDesktop() {
  const q = "(min-width: 1024px)";
  const [on, setOn] = useState(() => window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const f = () => setOn(m.matches);
    m.addEventListener("change", f);
    return () => m.removeEventListener("change", f);
  }, []);
  return on;
}
