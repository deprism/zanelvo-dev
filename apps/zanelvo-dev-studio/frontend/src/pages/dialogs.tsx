import { useState } from "react";
import devstudio from "@/lib/devstudio";
import { toast } from "@/lib/toast";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Textarea";
import { Dialog } from "@/components/ui/Dialog";

function slugify(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);
}

function ConnectExistingForm({ onCreated }: { onCreated: () => void }) {
  const [owner, setOwner] = useState("");
  const [repo, setRepo] = useState("");
  const [branch, setBranchName] = useState("main");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!owner.trim() || !repo.trim()) return;
    setBusy(true);
    try {
      await devstudio.createProject({
        github_owner: owner.trim(), github_repo: repo.trim(), default_branch: branch.trim() || "main",
      });
      toast.success("Repository connected");
      onCreated();
      setOwner("");
      setRepo("");
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not connect repository");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div>
        <label className="text-xs text-white/50 block mb-1">Owner</label>
        <Input value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="e.g. your-username" />
      </div>
      <div>
        <label className="text-xs text-white/50 block mb-1">Repository</label>
        <Input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="Repository name" />
      </div>
      <div>
        <label className="text-xs text-white/50 block mb-1">Default branch</label>
        <Input value={branch} onChange={(e) => setBranchName(e.target.value)} placeholder="main" />
      </div>
      <div className="flex justify-end pt-1">
        <Button loading={busy} disabled={!owner.trim() || !repo.trim()} onClick={submit}>
          Connect
        </Button>
      </div>
    </div>
  );
}

function CreateNewRepoForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [repoName, setRepoName] = useState("");
  const [repoNameTouched, setRepoNameTouched] = useState(false);
  const [description, setDescription] = useState("");
  const [isPrivate, setIsPrivate] = useState(true);
  const [busy, setBusy] = useState(false);

  const effectiveRepoName = repoNameTouched ? repoName : slugify(name);

  async function submit() {
    if (!effectiveRepoName.trim()) return;
    setBusy(true);
    try {
      const { data } = await devstudio.createRepoAndProject({
        repo_name: effectiveRepoName.trim(), private: isPrivate,
        description: description.trim() || undefined, name: name.trim() || undefined,
      });
      toast.success(`Created github.com/${data.github_owner}/${data.github_repo}`);
      onCreated();
      setName("");
      setRepoName("");
      setRepoNameTouched(false);
      setDescription("");
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not create the repository");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="text-[11px] text-white/40">
        Creates a brand-new, empty GitHub repository under your account (with a starter commit)
        and connects it — no need to go create it on github.com first.
      </div>
      <div>
        <label className="text-xs text-white/50 block mb-1">Project name</label>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. My Minecraft Plugin" />
      </div>
      <div>
        <label className="text-xs text-white/50 block mb-1">GitHub repository name</label>
        <Input value={effectiveRepoName}
          onChange={(e) => { setRepoName(e.target.value); setRepoNameTouched(true); }}
          placeholder="auto-generated from the project name" />
      </div>
      <div>
        <label className="text-xs text-white/50 block mb-1">Description (optional)</label>
        <Textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)}
          placeholder="What is this project?" />
      </div>
      <label className="flex items-center gap-2 text-xs text-white/70 cursor-pointer select-none">
        <input type="checkbox" checked={isPrivate} onChange={(e) => setIsPrivate(e.target.checked)}
          className="accent-indigo-500" />
        Private repository
      </label>
      <div className="flex justify-end pt-1">
        <Button loading={busy} disabled={!effectiveRepoName.trim()} onClick={submit}>
          Create repository
        </Button>
      </div>
    </div>
  );
}

export function NewProjectDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: () => void;
}) {
  const [mode, setMode] = useState<"connect" | "create">("connect");

  function handleCreated() {
    onOpenChange(false);
    onCreated();
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={mode === "connect" ? "Connect a GitHub repository" : "Create a new GitHub repository"}
      description="Dev Studio needs read/write access — configure a token in Settings if you haven't."
    >
      <div className="flex rounded-lg border border-white/10 overflow-hidden text-xs mb-1">
        <button type="button" onClick={() => setMode("connect")}
          className={`flex-1 py-1.5 ${mode === "connect" ? "bg-white/10 text-white" : "text-white/40"}`}>
          Connect existing
        </button>
        <button type="button" onClick={() => setMode("create")}
          className={`flex-1 py-1.5 ${mode === "create" ? "bg-white/10 text-white" : "text-white/40"}`}>
          Create new
        </button>
      </div>
      {mode === "connect"
        ? <ConnectExistingForm onCreated={handleCreated} />
        : <CreateNewRepoForm onCreated={handleCreated} />}
    </Dialog>
  );
}
