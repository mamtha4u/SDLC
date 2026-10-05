import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { talkApi, type TalkAgent } from "../../lib/talk";
import { KickoffRecord, PhaseTalk } from "./PhaseTalk";

/** On an agent's tab: while it's interviewing its specialist, the conversation takes the tab; afterwards the tab shows
 *  the agent's work as before, with the kickoff folded underneath. `agents`: the tab's agents in phase order (the Build
 *  tab has Terra and Dev). */
export function TalkGate({ projectId, agents, children }: { projectId: string; agents: TalkAgent[]; children: ReactNode }) {
  const { data } = useQuery({
    queryKey: ["talks", projectId], queryFn: () => talkApi.overview(projectId),
    refetchInterval: (q) => (q.state.data && Object.values(q.state.data).some((t) => t.status === "talking") ? 4000 : 15000),
  });
  const active = agents.find((a) => data?.[a]?.status === "talking");
  if (active) return <PhaseTalk projectId={projectId} agent={active} />;
  return (
    <>
      {children}
      {agents.map((a) => <KickoffRecord key={a} projectId={projectId} agent={a} summary={data?.[a]} />)}
    </>
  );
}
