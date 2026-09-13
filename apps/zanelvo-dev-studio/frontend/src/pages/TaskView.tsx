import { useCallback, useEffect, useRef, useState } from "react";
import {
  Play, Square, GitCommit, UploadCloud, Send, Bot, Wrench, FileText, TestTube2,
  ScrollText, FlagTriangleRight, ListTodo, FolderOpen, GitCompareArrows, Camera, AlertTriangle,
} from "lucide-react";
import devstudio from "@/lib/devstudio";
import { toast } from "@/lib/toast";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";
import { Badge } from "@/components/ui/Badge";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { EmptyState } from "@/components/ui/EmptyState";
import { PromptDialog } from "@/components/ui/PromptDialog";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import type { Task, DiffSummary, PlanItem, TestRun } from "@/lib/types";
import { CheckpointsPanel, PreviewPanel } from "./task-extra-panels";

// Statuses where the Supervisor/agents are actively working — used to flip the composer into a
// "working… (Stop)" state and to spin the Run button, so a long run never looks silently stuck.
const RUNNING_STATES = new Set([
  "UNDERSTANDING", "ANALYZING_REPOSITORY", "PLANNING", "READY", "IMPLEMENTING", "DEBUGGING",
  "TESTING", "REVIEWING", "FINAL_VERIFICATION", "COMMITTING", "PUSHING",
]);

const AGENT_ICON: Record<string, any> = {
  supervisor: Bot, repository_analyst: FileText, planner: FlagTriangleRight,
  design: Wrench, frontend: Wrench, backend: Wrench, integration: Wrench, qa: TestTube2,
  reviewer: FileText, git: GitCommit,
};

export default function TaskView({ taskId, onTaskChanged }: { taskId: string; onTaskChanged?: () => void }) {
  const [task, setTask] = useState<Task | null>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [message, setMessage] = useState("");
  const [diff, setDiff] = useState<DiffSummary | null>(null);
  const [plan, setPlan] = useState<PlanItem[]>([]);
  const [tests, setTests] = useState<TestRun[]>([]);
  const [screenshots, setScreenshots] = useState<any[]>([]);
  const [files, setFiles] = useState<any[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState("");
  const [commitOpen, setCommitOpen] = useState(false);
  const [sseConnected, setSseConnected] = useState(true);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const refreshAll = useCallback(async () => {
    const [t, d, p, ts, sc] = await Promise.all([
      devstudio.getTask(taskId), devstudio.diff(taskId), devstudio.plan(taskId),
      devstudio.tests(taskId), devstudio.screenshots(taskId),
    ]);
    setTask(t.data);
    setDiff(d.data);
    setPlan(p.data.plan);
    setTests(ts.data.runs);
    setScreenshots(sc.data.screenshots);
    onTaskChanged?.();
  }, [taskId, onTaskChanged]);

  useEffect(() => {
    setTask(null);
    setEvents([]);
    refreshAll();
    devstudio
      .filesTree(taskId)
      .then(({ data }) => setFiles(data.entries))
      .catch(() => setFiles([]));

    setSseConnected(true);
    const es = new EventSource(devstudio.eventsUrl(taskId), { withCredentials: true });
    const onAny = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        setEvents((prev) => [...prev, { ...data, kind: e.type }]);
        if (["state_change", "plan_created", "plan_item_status", "agent_finished", "plan_item_reassigned",
          "item_implemented"].includes(e.type)) refreshAll();
      } catch {
        /* ignore malformed event */
      }
    };
    const kinds = [
      "task_created", "state_change", "message", "agent_started", "agent_finished", "plan_created",
      "plan_item_status", "supervisor_note", "stop_requested", "tool_call", "plan_item_reassigned",
      "analysis_complete", "item_implemented",
    ];
    kinds.forEach((k) => es.addEventListener(k, onAny as EventListener));
    // The browser's EventSource reconnects on its own after a drop (network blip, backend
    // restart) — this just surfaces that state so a long agent run doesn't look silently stuck,
    // and refreshes plan/diff/test state once back (a missed event during the gap would otherwise
    // never be reflected, since this backend doesn't implement SSE's Last-Event-ID replay).
    let wasDisconnected = false;
    es.onopen = () => {
      setSseConnected(true);
      if (wasDisconnected) { wasDisconnected = false; refreshAll(); }
    };
    es.onerror = () => { wasDisconnected = true; setSseConnected(false); };
    return () => es.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [events]);

  async function send() {
    if (!message.trim()) return;
    const text = message.trim();
    setMessage("");
    const { data } = await devstudio.postMessage(taskId, text);
    // First message on a blank chat backfills request_text + auto-titles server-side — refresh
    // so the sidebar/header pick up the new title immediately instead of waiting on the next
    // state_change SSE event.
    if (data?.task) {
      setTask(data.task);
      onTaskChanged?.();
    }
  }

  async function run() {
    await devstudio.runTask(taskId);
    toast.success("Supervisor started");
    refreshAll();
  }
  async function stop() {
    await devstudio.stopTask(taskId);
    toast.info("Stop requested");
  }
  async function commit(msg: string) {
    try {
      await devstudio.commit(taskId, msg);
      toast.success("Committed");
      refreshAll();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Commit failed");
    }
  }
  async function push() {
    try {
      await devstudio.push(taskId);
      toast.success("Pushed to GitHub");
      refreshAll();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Push failed");
    }
  }
  async function openFile(path: string) {
    setSelectedFile(path);
    const { data } = await devstudio.fileRead(taskId, path);
    setFileContent(data.content);
  }
  async function runTests() {
    try {
      const { data } = await devstudio.runTests(taskId);
      setTests(data.runs);
      toast.success("Tests ran");
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not run tests");
    }
  }

  if (!task) {
    return (
      <div className="flex-1 grid place-items-center">
        <div className="flex items-center gap-2 text-white/40 text-sm animate-fade-in">
          <div className="w-4 h-4 rounded-full border-2 border-white/20 border-t-white/60 animate-spin" />
          Loading task…
        </div>
      </div>
    );
  }

  const isRunning = RUNNING_STATES.has(task.status);

  return (
    <div className="flex-1 flex min-h-0">
      {/* CENTER */}
      <div className="flex-1 min-w-0 flex flex-col border-r border-white/10">
        <div className="h-14 flex items-center gap-3 px-4 border-b border-white/10 flex-shrink-0">
          <div className="min-w-0">
            <div className="text-sm font-medium truncate">{task.title}</div>
            <div className="text-[11px] text-white/40 flex items-center gap-2 mt-0.5">
              <StatusBadge status={task.status} />
              <span className="capitalize">{task.mode}</span>
              <span className="text-white/20">·</span>
              <span className="font-mono">{task.branch}</span>
              {diff && (
                <>
                  <span className="text-white/20">·</span>
                  <span>{diff.changed_file_count} files changed</span>
                </>
              )}
              {!sseConnected && (
                <>
                  <span className="text-white/20">·</span>
                  <span className="flex items-center gap-1 text-amber-300">
                    <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse" />
                    Reconnecting…
                  </span>
                </>
              )}
            </div>
          </div>
          <div className="ml-auto flex items-center gap-1.5">
            <Button size="sm" variant="secondary" onClick={run} loading={isRunning} disabled={isRunning}>
              <Play className="w-3.5 h-3.5" /> {isRunning ? "Running" : "Run"}
            </Button>
            <Button size="sm" variant="outline" onClick={stop} disabled={!isRunning}>
              <Square className="w-3.5 h-3.5" /> Stop
            </Button>
            <Button size="sm" variant="outline" onClick={() => setCommitOpen(true)} disabled={task.status !== "READY_FOR_APPROVAL"}>
              <GitCommit className="w-3.5 h-3.5" /> Commit
            </Button>
            <Button size="sm" onClick={push} disabled={task.status !== "COMMITTING" && task.status !== "READY_FOR_APPROVAL"}>
              <UploadCloud className="w-3.5 h-3.5" /> Push
            </Button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto" ref={scrollRef}>
          <div className="p-4 space-y-2">
            {!events.length && (
              <EmptyState
                icon={Bot}
                title={task.request_text ? "No activity yet" : "Start the conversation"}
                description={
                  task.request_text
                    ? "Hit Run, or message the Supervisor below to get started."
                    : "Describe the feature or bug below — the Supervisor plans and runs it the moment you send."
                }
              />
            )}
            {events.map((e, i) => (
              <ActivityRow key={i} event={e} />
            ))}
            {task.blocked_reason && (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200 flex items-start gap-2 animate-fade-up">
                <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                <div>
                  <strong className="font-medium">Blocked</strong>
                  <div className="mt-0.5 text-amber-200/80">{task.blocked_reason}</div>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="p-3 border-t border-white/10 flex gap-2 flex-shrink-0">
          <Textarea
            autoFocus
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            rows={1}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder={
              task.request_text
                ? 'Message the Supervisor — e.g. "Continue.", "Only change frontend.", "Run tests again."'
                : "Describe the feature or bug you want built…"
            }
            className="min-h-9"
          />
          <Button size="icon" onClick={send} disabled={!message.trim() || isRunning} title={isRunning ? "Agents are working…" : "Send"}>
            {isRunning ? (
              <div className="w-4 h-4 rounded-full border-2 border-white/30 border-t-white animate-spin" />
            ) : (
              <Send className="w-4 h-4" />
            )}
          </Button>
        </div>
        {isRunning && (
          <div className="px-3 pb-2 -mt-1 flex items-center gap-2 text-[11px] text-white/45 flex-shrink-0">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
            Agents are working — you can queue a message, or
            <button onClick={stop} className="text-amber-300 hover:text-amber-200 underline underline-offset-2">
              stop
            </button>
            . Everything so far is kept.
          </div>
        )}
      </div>

      {/* RIGHT INSPECTOR */}
      <Tabs defaultValue="plan" className="w-[420px] flex-shrink-0 flex flex-col">
        <TabsList className="mx-2 mt-2">
          <TabsTrigger value="plan">plan</TabsTrigger>
          <TabsTrigger value="files">files</TabsTrigger>
          <TabsTrigger value="diff">diff</TabsTrigger>
          <TabsTrigger value="tests">tests</TabsTrigger>
          <TabsTrigger value="screenshots">shots</TabsTrigger>
          <TabsTrigger value="preview">preview</TabsTrigger>
          <TabsTrigger value="checkpoints">checkpoints</TabsTrigger>
        </TabsList>

        <TabsContent value="plan" className="flex-1 min-h-0 overflow-y-auto p-3">
          {plan.map((p) => (
            <div key={p.id} className="rounded-lg border border-white/10 bg-white/[0.03] p-2.5 mb-2 text-xs hover:border-white/15 hover:-translate-y-0.5 hover:shadow-soft transition-all duration-150">
              <div className="flex items-center gap-1.5">
                <StatusBadge status={p.status} />
                <span className="text-white/40 capitalize">{(p.assigned_agent || "").replace(/_/g, " ")}</span>
              </div>
              <div className="text-white/85 font-medium mt-1.5">{p.title}</div>
              <div className="text-white/50 mt-0.5 leading-relaxed">{p.description}</div>
            </div>
          ))}
          {!plan.length && (
            <EmptyState compact icon={FlagTriangleRight} title="No plan yet" description="Run the task to generate one." />
          )}
        </TabsContent>

        <TabsContent value="files" className="flex-1 min-h-0 flex">
          <div className="w-1/2 border-r border-white/10 overflow-y-auto py-1">
            {files
              .filter((f) => !f.is_dir)
              .map((f) => (
                <button
                  key={f.path}
                  onClick={() => openFile(f.path)}
                  className={`w-full text-left px-2 py-1 text-[11px] rounded hover:bg-white/[0.05] truncate transition-colors ${
                    selectedFile === f.path ? "bg-white/[0.08] text-white" : "text-white/55"
                  }`}
                >
                  {f.path}
                </button>
              ))}
            {!files.length && <EmptyState compact icon={FolderOpen} title="No files yet" />}
          </div>
          <div className="w-1/2 overflow-y-auto">
            <pre className="p-2 text-[10px] text-white/70 whitespace-pre-wrap font-mono leading-relaxed">
              {fileContent || "Select a file"}
            </pre>
          </div>
        </TabsContent>

        <TabsContent value="diff" className="flex-1 min-h-0 overflow-y-auto p-3">
          <div className="text-xs text-white/50 mb-2.5">
            <span className="text-emerald-400 font-medium">+{diff?.total_additions || 0}</span>{" "}
            <span className="text-red-400 font-medium">-{diff?.total_deletions || 0}</span>{" "}
            across {diff?.changed_file_count || 0} files
          </div>
          {(diff?.files || []).map((f: any) => (
            <div key={f.path} className="text-xs mb-1.5 flex items-center gap-2">
              <Badge className="border-white/15 text-white/50 flex-shrink-0">{f.change_type}</Badge>
              <span className="text-white/70 truncate font-mono">{f.path}</span>
              <span className="ml-auto text-emerald-400 flex-shrink-0">+{f.additions}</span>
              <span className="text-red-400 flex-shrink-0">-{f.deletions}</span>
            </div>
          ))}
          {!diff?.files?.length && <EmptyState compact icon={GitCompareArrows} title="No changes yet" />}
        </TabsContent>

        <TabsContent value="tests" className="flex-1 min-h-0 overflow-y-auto p-3">
          <Button size="sm" variant="outline" className="mb-2.5" onClick={runTests}>
            <TestTube2 className="w-3.5 h-3.5" /> Run tests now
          </Button>
          {tests.map((r) => (
            <div key={r.id} className="rounded-lg border border-white/10 bg-white/[0.03] p-2.5 mb-2 text-xs hover:border-white/15 hover:-translate-y-0.5 hover:shadow-soft transition-all duration-150">
              <div className="flex items-center gap-1.5">
                <StatusBadge status={r.status} />
                <span className="text-white/50">{r.test_type}</span>
                <span className="text-white/30 ml-auto">{r.duration_ms}ms</span>
              </div>
              <div className="text-white/70 font-mono mt-1.5 truncate">{r.command}</div>
              {r.status !== "passed" && r.stderr_tail && (
                <pre className="text-red-300/80 mt-1.5 whitespace-pre-wrap max-h-28 overflow-auto bg-red-500/5 rounded p-1.5">
                  {r.stderr_tail}
                </pre>
              )}
            </div>
          ))}
          {!tests.length && <EmptyState compact icon={TestTube2} title="No test runs yet" />}
        </TabsContent>

        <TabsContent value="screenshots" className="flex-1 min-h-0 overflow-y-auto p-3">
          {screenshots.map((s) => (
            <div key={s.id} className="text-xs text-white/60 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-2 mb-1.5">
              <span className="text-white/80">{s.label}</span>
              <span className="text-white/35"> — {s.path.split("/").pop()}</span>
            </div>
          ))}
          {!screenshots.length && <EmptyState compact icon={Camera} title="No browser QA screenshots yet" />}
        </TabsContent>

        <TabsContent value="preview" className="flex-1 min-h-0">
          <PreviewPanel taskId={taskId} />
        </TabsContent>

        <TabsContent value="checkpoints" className="flex-1 min-h-0">
          <CheckpointsPanel taskId={taskId} />
        </TabsContent>
      </Tabs>

      <PromptDialog
        open={commitOpen}
        onOpenChange={setCommitOpen}
        title="Commit changes"
        label="Commit message"
        defaultValue={task?.title || "Dev Studio changes"}
        submitLabel="Commit"
        onSubmit={commit}
      />
    </div>
  );
}

function ActivityRow({ event }: { event: any }) {
  const kind = event.kind;
  if (kind === "message") {
    const isUser = event.payload.role === "user";
    return (
      <div
        className={`text-xs rounded-lg px-3 py-2 max-w-[85%] animate-fade-up leading-relaxed ${
          isUser ? "bg-indigo-500/20 ml-auto text-indigo-100" : "bg-white/[0.05] text-white/80"
        }`}
      >
        {event.payload.text}
      </div>
    );
  }
  if (kind === "agent_started" || kind === "agent_finished") {
    const Icon = AGENT_ICON[event.payload.role] || Bot;
    const summary =
      kind === "agent_finished" && event.payload.summary && event.payload.summary !== "ok"
        ? event.payload.summary
        : null;
    return (
      <div className="animate-fade-up">
        <div className="flex items-center gap-2 text-[11px] text-white/50">
          <Icon className="w-3.5 h-3.5 flex-shrink-0" />
          <span className="capitalize">{(event.payload.role || "").replace(/_/g, " ")}</span>
          <span>{event.payload.action || event.payload.status}</span>
          {event.payload.model && (
            <span className="text-white/30 font-mono">
              · {event.payload.provider}/{event.payload.model}
            </span>
          )}
        </div>
        {summary && (
          <div className="ml-5 mt-0.5 text-[11px] text-white/60 leading-relaxed whitespace-pre-wrap">
            {summary}
          </div>
        )}
      </div>
    );
  }
  if (kind === "analysis_complete") {
    return (
      <div className="animate-fade-up text-[11px]">
        <div className="flex items-center gap-2 text-white/50">
          <FileText className="w-3.5 h-3.5 flex-shrink-0" /> Repository analysis
        </div>
        {event.payload.summary && (
          <div className="ml-5 mt-0.5 text-white/60 leading-relaxed whitespace-pre-wrap">
            {event.payload.summary}
          </div>
        )}
        {!!(event.payload.relevant_files || []).length && (
          <div className="ml-5 mt-0.5 text-white/35 font-mono truncate">
            {(event.payload.relevant_files || []).join(", ")}
          </div>
        )}
      </div>
    );
  }
  if (kind === "item_implemented") {
    return (
      <div className="animate-fade-up text-[11px]">
        <div className="flex items-center gap-2 text-white/55">
          <Wrench className="w-3.5 h-3.5 flex-shrink-0" />
          <span className="capitalize">{(event.payload.role || "").replace(/_/g, " ")}</span>
          <span className="text-white/75">{event.payload.title}</span>
          <span className="text-white/30">
            · {event.payload.op_count} file{event.payload.op_count === 1 ? "" : "s"}
          </span>
        </div>
        {event.payload.summary && (
          <div className="ml-5 mt-0.5 text-white/60 leading-relaxed whitespace-pre-wrap">
            {event.payload.summary}
          </div>
        )}
        {!!(event.payload.files || []).length && (
          <div className="ml-5 mt-1 flex flex-wrap gap-1">
            {(event.payload.files || []).map((f: string) => (
              <span key={f} className="font-mono text-[10px] text-emerald-300/80 bg-emerald-500/10 rounded px-1.5 py-0.5">
                {f}
              </span>
            ))}
          </div>
        )}
      </div>
    );
  }
  if (kind === "state_change") {
    return (
      <div className="flex items-center gap-2 text-[11px] text-white/40 animate-fade-up">
        <ScrollText className="w-3.5 h-3.5" /> {event.payload.from} → <strong className="text-white/60 font-medium">{event.payload.to}</strong>
      </div>
    );
  }
  if (kind === "plan_created") {
    return (
      <div className="text-[11px] text-white/40 flex items-center gap-2 animate-fade-up">
        <FlagTriangleRight className="w-3.5 h-3.5" /> Plan created ({event.payload.item_count} items)
      </div>
    );
  }
  if (kind === "plan_item_status") {
    return (
      <div className="text-[11px] text-white/40 flex items-center gap-2 animate-fade-up">
        <ListTodo className="w-3.5 h-3.5 flex-shrink-0" />
        Plan item "{event.payload.title}": {event.payload.from} → {event.payload.to}
      </div>
    );
  }
  if (kind === "tool_call") {
    const args = event.payload.args || {};
    const argStr = Object.entries(args)
      .map(([k, v]) => `${k}: ${v}`)
      .join(", ");
    return (
      <div className="animate-fade-up">
        <div className="text-[11px] text-white/40 flex items-center gap-2">
          <Wrench className="w-3.5 h-3.5 flex-shrink-0" />
          <span className="capitalize">{(event.payload.role || "").replace(/_/g, " ")}</span> called
          <span className="font-mono text-white/60">{event.payload.tool}</span>
          {event.payload.server && <span className="text-white/30">via MCP "{event.payload.server}"</span>}
        </div>
        {argStr && <div className="ml-5 text-[11px] text-white/45 truncate">↳ {argStr}</div>}
        {event.payload.result && (
          <div className="ml-5 mt-0.5 text-[11px] text-white/55 whitespace-pre-wrap max-h-24 overflow-auto bg-white/[0.03] rounded p-1.5">
            {event.payload.result}
          </div>
        )}
      </div>
    );
  }
  if (kind === "plan_item_reassigned") {
    return (
      <div className="text-[11px] text-amber-300/70 flex items-center gap-2 animate-fade-up">
        <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
        "{event.payload.title}" reassigned: {(event.payload.from_agent || "").replace(/_/g, " ")} →{" "}
        <strong className="text-amber-200/90 font-medium">
          {(event.payload.to_agent || "").replace(/_/g, " ")}
        </strong>{" "}
        — {event.payload.reason}
      </div>
    );
  }
  return <div className="text-[11px] text-white/30 animate-fade-up">{event.payload?.note || kind}</div>;
}
