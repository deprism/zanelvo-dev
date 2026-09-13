import { useEffect, useState } from "react";
import { RotateCcw, Save, Play, Square, Link2, Camera, History, MonitorPlay } from "lucide-react";
import devstudio from "@/lib/devstudio";
import { toast } from "@/lib/toast";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import type { Checkpoint } from "@/lib/types";

// --- Checkpoints ---------------------------------------------------------------------------

export function CheckpointsPanel({ taskId }: { taskId: string }) {
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [restoreTarget, setRestoreTarget] = useState<{ id: string; label: string } | null>(null);

  async function load() {
    const { data } = await devstudio.checkpoints(taskId);
    setCheckpoints(data.checkpoints);
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  async function create() {
    if (!label.trim()) return;
    setBusy(true);
    try {
      await devstudio.createCheckpoint(taskId, label.trim());
      toast.success("Checkpoint created");
      setLabel("");
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not create checkpoint");
    } finally {
      setBusy(false);
    }
  }

  async function restore() {
    if (!restoreTarget) return;
    try {
      await devstudio.restoreCheckpoint(taskId, restoreTarget.id, true);
      toast.success("Restored");
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Restore failed");
    }
  }

  return (
    <div className="h-full overflow-y-auto p-3">
      <div className="flex gap-1.5 mb-3">
        <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Checkpoint label…"
          onKeyDown={(e) => e.key === "Enter" && create()} />
        <Button size="sm" disabled={busy || !label.trim()} onClick={create}>
          <Save className="w-3.5 h-3.5" />
        </Button>
      </div>
      {checkpoints.map((c) => (
        <div key={c.id} className="rounded-lg border border-white/10 bg-white/[0.03] p-2.5 mb-2 text-xs hover:border-white/15 hover:-translate-y-0.5 hover:shadow-soft transition-all duration-150">
          <div className="flex items-center justify-between gap-2">
            <span className="text-white/85 font-medium truncate">{c.label}</span>
            <Button size="sm" variant="outline" onClick={() => setRestoreTarget({ id: c.id, label: c.label })}>
              <RotateCcw className="w-3 h-3" /> Restore
            </Button>
          </div>
          <div className="text-white/40 mt-1 font-mono">{c.commit_sha?.slice(0, 10)} · {c.branch}</div>
        </div>
      ))}
      {!checkpoints.length && <EmptyState compact icon={History} title="No checkpoints yet" description="Save one before a risky change." />}

      <ConfirmDialog
        open={!!restoreTarget}
        onOpenChange={(v) => !v && setRestoreTarget(null)}
        title={`Restore "${restoreTarget?.label ?? ""}"?`}
        description="This discards any uncommitted changes made after this checkpoint. It cannot be undone."
        confirmLabel="Restore"
        danger
        onConfirm={restore}
      />
    </div>
  );
}

// --- Preview -----------------------------------------------------------------------------

export function PreviewPanel({ taskId }: { taskId: string }) {
  const [state, setState] = useState<any>(null);
  const [externalUrl, setExternalUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [staticInfo, setStaticInfo] = useState<any>(null);
  const [iframeKey, setIframeKey] = useState(0);

  async function checkStatic() {
    try {
      const { data } = await devstudio.previewStaticInfo(taskId);
      setStaticInfo(data);
    } catch {
      setStaticInfo({ available: false });
    }
  }
  useEffect(() => {
    checkStatic();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  const serveUrl = devstudio.previewServeUrl(taskId);

  async function startLive() {
    setBusy(true);
    try {
      const { data } = await devstudio.previewLiveLocal(taskId);
      setState(data);
      if (data.status !== "running") toast.error(data.detail || "Preview unavailable");
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not start preview");
    } finally {
      setBusy(false);
    }
  }
  async function stopLive() {
    await devstudio.previewStop(taskId);
    setState(null);
  }
  async function attachExternal() {
    if (!externalUrl.trim()) return;
    const { data } = await devstudio.previewExternal(taskId, externalUrl.trim());
    setState(data);
  }
  async function loadScreenshotMode() {
    const { data } = await devstudio.previewScreenshot(taskId);
    setState(data);
  }

  if (staticInfo?.available) {
    return (
      <div className="h-full flex flex-col min-h-0">
        <div className="flex items-center gap-1.5 p-2 border-b border-white/10 flex-shrink-0">
          <Badge tone="success" dot>live</Badge>
          <span className="text-[11px] text-white/45 truncate">static site preview</span>
          <div className="ml-auto flex gap-1.5">
            <Button size="sm" variant="outline" onClick={() => { checkStatic(); setIframeKey((k) => k + 1); }}>
              <RotateCcw className="w-3 h-3" /> Refresh
            </Button>
            <a href={serveUrl} target="_blank" rel="noreferrer">
              <Button size="sm" variant="outline"><Link2 className="w-3 h-3" /> Open</Button>
            </a>
          </div>
        </div>
        <iframe
          key={iframeKey}
          src={serveUrl}
          title="Live preview"
          className="flex-1 w-full bg-white"
          sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-popups allow-pointer-lock"
        />
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-3 space-y-3">
      <EmptyState
        compact
        icon={MonitorPlay}
        title="No live preview yet"
        description="As soon as the agents create an index.html, an interactive preview appears here automatically. For a full dev server or an external URL, use the options below."
      />
      <Button size="sm" variant="outline" onClick={checkStatic}>
        <RotateCcw className="w-3.5 h-3.5" /> Check for site
      </Button>
      <div className="flex gap-1.5">
        <Button size="sm" variant="outline" loading={busy} onClick={startLive}>
          <Play className="w-3.5 h-3.5" /> Live local
        </Button>
        <Button size="sm" variant="outline" onClick={stopLive}>
          <Square className="w-3.5 h-3.5" /> Stop
        </Button>
        <Button size="sm" variant="outline" onClick={loadScreenshotMode}>
          <Camera className="w-3.5 h-3.5" /> Screenshot
        </Button>
      </div>
      <div className="flex gap-1.5">
        <Input value={externalUrl} onChange={(e) => setExternalUrl(e.target.value)}
          placeholder="https://your-preview-env…" onKeyDown={(e) => e.key === "Enter" && attachExternal()} />
        <Button size="sm" variant="outline" onClick={attachExternal}>
          <Link2 className="w-3.5 h-3.5" />
        </Button>
      </div>
      {state && (
        <div className="rounded-lg border border-white/10 bg-white/[0.03] p-3 text-xs space-y-1.5 animate-fade-up">
          <div className="flex items-center gap-2">
            <Badge className="border-white/15 text-white/60">{state.mode}</Badge>
            <Badge tone={state.status === "running" || state.status === "attached" ? "success" : "warning"} dot>
              {state.status}
            </Badge>
          </div>
          {state.url && (
            <a href={state.url} target="_blank" rel="noreferrer" className="text-indigo-300 hover:text-indigo-200 underline block break-all transition-colors">
              {state.url}
            </a>
          )}
          {state.detail && <div className="text-white/50">{state.detail}</div>}
        </div>
      )}
    </div>
  );
}
