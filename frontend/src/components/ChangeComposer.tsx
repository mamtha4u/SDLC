import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { motion } from "framer-motion";
import { FileText, Info, Paperclip, Send, UploadCloud, X } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "../lib/api";
import { flowApi } from "../lib/flow";
import { intakeApi } from "../lib/intake";
import { AgentAvatar } from "./AgentAvatar";
import { Overlay } from "./Overlay";
import { Button } from "./ui";


/** The ONE way to add or change anything after sign-off. It goes to Orion, who routes it (Echo / himself / Atlas).
 *  If a pending approval exists, the change replaces it. If Echo already has a change open, this goes into that
 *  conversation instead of opening a second one. */
export function ChangeComposer({ projectId, open, onClose, onSent }: {
  projectId: string; open: boolean; onClose: () => void; onSent?: (where: "requirement" | "top") => void;
}) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [drag, setDrag] = useState(false);
  const [sending, setSending] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const { data: intake } = useQuery({ queryKey: ["intake", projectId], queryFn: () => intakeApi.get(projectId), enabled: open });
  const { data: approvals } = useQuery({ queryKey: ["approvals", projectId], queryFn: () => flowApi.approvals(projectId), enabled: open });
  const { data: changes } = useQuery({ queryKey: ["changes", projectId], queryFn: () => flowApi.changes(projectId), enabled: open });
  const pending = approvals?.find((a) => a.status === "pending") ?? null;
  const infraGate = !!pending && ["infra", "infra_check", "deploy"].includes(pending.stage);
  const amending = intake?.status === "amending";
  const openCr = amending ? changes?.find((c) => c.id === intake?.active_cr) : null;

  const add = (list: FileList | null) => list && setFiles((f) => [...f, ...Array.from(list).filter((x) => !f.some((y) => y.name === x.name))]);
  const send = async () => {
    setSending(true);
    try {
      if (amending) {
        const names: string[] = [];
        for (const f of files) names.push((await intakeApi.upload(projectId, f)).name);
        await intakeApi.chat(projectId, text.trim(), names);
        toast.success(`Added to ${openCr?.label ?? "the open change"}. Echo is on it`);
        onSent?.("requirement");
      } else {
        const cr = await flowApi.raiseChange(projectId, text.trim(), files, pending?.id);
        toast.success(cr.route === "infra" ? `${cr.label}: Orion reviews the impact first` : `${cr.label} sent to Orion. He'll route it to the right agent`);
        onSent?.("top");
      }
      setText(""); setFiles([]); onClose();
      ["changes", "approvals", "project", "intake", "crew"].forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] }));
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Couldn't send"); }
    finally { setSending(false); }
  };

  return (
    <Overlay open={open} onClose={onClose} label="Change request" z={90}>
          <motion.div role="dialog" aria-modal aria-label="Change request"
            initial={{ y: 30, scale: 0.96, opacity: 0 }} animate={{ y: 0, scale: 1, opacity: 1 }} exit={{ y: 20, opacity: 0 }}
            transition={{ type: "spring", stiffness: 340, damping: 30 }}
            className="relative w-full max-w-[640px] overflow-hidden rounded-[28px] border border-line bg-surface shadow-2xl">
            <div className="relative px-6 pb-4 pt-6" style={{ background: "linear-gradient(160deg, color-mix(in srgb, var(--primary) 22%, var(--surface)), var(--surface) 70%)" }}>
              <button onClick={onClose} aria-label="Close" className="absolute right-4 top-4 grid h-9 w-9 place-items-center rounded-full bg-black/15 hover:bg-black/30"><X className="h-4 w-4" /></button>
              <div className="flex items-center gap-3 pr-10">
                <AgentAvatar agent={amending ? "intake" : "cto"} accent={amending ? "cyan" : "violet"} status="working" size={46} />
                <div>
                  <h2 className="font-display text-xl font-bold">{amending ? `Add to ${openCr?.label ?? "the open change"}` : "Change request"}</h2>
                  <p className="text-sm text-muted">{amending
                    ? "Echo is already updating the requirement. This goes straight into that conversation, and she tells Orion if it matters to him."
                    : "New or extra requirement, or something to fix. Orion reads it, decides who handles it, and tells every affected agent."}</p>
                </div>
              </div>
              {!amending && (
                <ol className="mt-4 grid gap-2 text-[12px] sm:grid-cols-3">
                  {(infraGate ? ["Orion reviews the impact: infrastructure only, the code too, or the requirement? He asks you if it's big",
                    "Terra changes the Terraform; you approve the exact plan", "Terra applies it; you check it again in AWS (Dev follows if the code must change)"]
                    : ["Orion triages: requirement, plan, mapping, design, infra…?", "Echo updates the requirement as a new version (asks you if anything's missing)", "Orion re-plans; only affected agents redo their work"]).map((s, i) => (
                    <li key={s} className="flex gap-2 rounded-[12px] bg-bg-2/70 px-2.5 py-2"><span className="font-bold text-primary">{i + 1}</span><span className="text-muted">{s}</span></li>
                  ))}
                </ol>
              )}
              {pending && !amending && (
                <p className="mt-3 flex items-start gap-2 rounded-[12px] border border-warning/40 bg-warning/10 px-3 py-2 text-[12.5px]">
                  <Info className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                  <span><b>{pending.title}</b> is waiting for your approval. Sending this replaces it: the work is redone with your change.</span>
                </p>
              )}
            </div>
            <div className="space-y-3 px-6 pb-6 pt-4">
              <textarea autoFocus value={text} onChange={(e) => setText(e.target.value)} rows={5}
                placeholder={amending ? "e.g. The logger.py I attached was the wrong file. Use this one instead."
                  : infraGate ? "e.g. The DLQ is called …-dlq but our convention is …-orders-dlq.fifo, and the transform function needs 512 MB, not 128."
                  : "e.g. We must use our own logging format. logger.py (attached) must be packaged as a Lambda layer and used by the Lambda for every log line."}
                className="w-full resize-y rounded-[14px] border border-line bg-bg-2 px-4 py-3 text-[15px] outline-none focus:border-primary" />
              <label onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
                onDrop={(e) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); }}
                className={clsx("flex cursor-pointer items-center gap-3 rounded-[14px] border-2 border-dashed px-4 py-3 transition-colors",
                  drag ? "border-primary bg-primary/10" : "border-line hover:border-primary/60")}>
                <input ref={input} type="file" multiple hidden onChange={(e) => { add(e.target.files); if (input.current) input.current.value = ""; }} />
                <UploadCloud className="h-6 w-6 shrink-0 text-primary" />
                <span className="text-sm"><b>Attach files</b> <span className="text-muted">(code like logger.py, samples, specs, mapping sheets): drop here or click</span></span>
              </label>
              {files.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {files.map((f) => (
                    <span key={f.name} className="inline-flex items-center gap-1.5 rounded-full border border-line bg-bg-2 px-3 py-1 text-xs">
                      <FileText className="h-3.5 w-3.5 text-primary-2" />{f.name}<span className="text-muted">{Math.max(1, Math.round(f.size / 1024))} KB</span>
                      <button onClick={() => setFiles(files.filter((x) => x !== f))} aria-label={`Remove ${f.name}`} className="text-muted hover:text-danger"><X className="h-3.5 w-3.5" /></button>
                    </span>
                  ))}
                </div>
              )}
              <div className="flex items-center justify-between gap-2 pt-1">
                <span className="inline-flex items-center gap-1 text-xs text-muted"><Paperclip className="h-3.5 w-3.5" />Files are stored unchanged in the project's inputs/ folder</span>
                <div className="flex gap-2">
                  <Button variant="ghost" onClick={onClose}>Cancel</Button>
                  <Button variant="primary" className="shimmer" loading={sending} disabled={!text.trim()} icon={<Send className="h-4 w-4" />} onClick={send}>
                    {amending ? "Send to Echo" : "Send to Orion"}
                  </Button>
                </div>
              </div>
            </div>
          </motion.div>
    </Overlay>
  );
}
