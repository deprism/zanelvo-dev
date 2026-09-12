import { useEffect, useState } from "react";
import {
  Folder, File as FileIcon, Search, CheckCircle2, AlertTriangle, XCircle, RefreshCw, FolderGit2,
  Brain, Bot, Github, Settings as SettingsIcon, ChevronRight, ArrowUp, FileCode2, BrainCircuit,
  KeyRound, FlaskConical,
} from "lucide-react";
import devstudio from "@/lib/devstudio";
import { getReduceMotion, setReduceMotion } from "@/lib/motionPreference";
import { toast } from "@/lib/toast";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import type { MemoryItem } from "@/lib/types";

// --- Repository browser ---------------------------------------------------------------

export function RepoBrowser({ projectId, branch }: { projectId: string; branch: string }) {
  const [entries, setEntries] = useState<any[]>([]);
  const [subdir, setSubdir] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<any[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState("");

  useEffect(() => {
    devstudio
      .tree(projectId, branch, subdir)
      .then(({ data }) => setEntries(data.entries))
      .catch((e: any) => toast.error(e?.response?.data?.detail || "Could not load tree"));
  }, [projectId, branch, subdir]);

  async function openFile(path: string) {
    setSelected(path);
    try {
      const { data } = await devstudio.readFile(projectId, branch, path);
      setContent(data.content);
    } catch (e: any) {
      setContent(`<< ${e?.response?.data?.detail || "could not read file"} >>`);
    }
  }

  async function doSearch() {
    if (!query.trim()) {
      setResults(null);
      return;
    }
    const { data } = await devstudio.search(projectId, branch, query);
    setResults(data.results);
  }

  const rows = results ?? entries;

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <PageHeader icon={FolderGit2} title="Repository" description={`branch ${branch}`} />
      <div className="flex-1 flex min-h-0">
        <div className="w-72 border-r border-white/10 flex flex-col flex-shrink-0">
          <div className="p-2 border-b border-white/10 flex gap-1">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && doSearch()}
              placeholder="Search code…"
            />
            <Button size="icon" variant="ghost" onClick={doSearch}>
              <Search className="w-3.5 h-3.5" />
            </Button>
          </div>
          <div className="flex-1 overflow-y-auto p-1">
            {subdir && !results && (
              <button
                className="w-full flex items-center gap-1.5 text-left px-2 py-1 text-xs text-white/45 hover:bg-white/[0.05] hover:text-white/70 rounded transition-colors"
                onClick={() => setSubdir(subdir.split("/").slice(0, -1).join("/"))}
              >
                <ArrowUp className="w-3.5 h-3.5" /> ..
              </button>
            )}
            {rows.map((e) => (
              <button
                key={e.path}
                onClick={() => (e.is_dir ? setSubdir(e.path) : openFile(e.path))}
                className={`w-full flex items-center gap-1.5 text-left px-2 py-1 text-xs rounded transition-colors ${
                  selected === e.path ? "bg-white/[0.09] text-white" : "text-white/65 hover:bg-white/[0.05] hover:text-white/90"
                }`}
              >
                {e.is_dir ? (
                  <Folder className="w-3.5 h-3.5 text-sky-400 flex-shrink-0" />
                ) : (
                  <FileIcon className="w-3.5 h-3.5 text-white/35 flex-shrink-0" />
                )}
                <span className="truncate">{e.path.split("/").pop()}</span>
                {e.line && <span className="text-white/30 ml-auto flex-shrink-0">:{e.line}</span>}
              </button>
            ))}
            {!rows.length && (
              <EmptyState compact icon={Search} title={results ? "No matches" : "Empty directory"} />
            )}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          {content ? (
            <pre className="p-4 text-xs text-white/80 whitespace-pre-wrap font-mono leading-relaxed">{content}</pre>
          ) : (
            <EmptyState icon={FileCode2} title="No file open" description="Select a file from the tree to view its contents." />
          )}
        </div>
      </div>
    </div>
  );
}

// --- Project memory --------------------------------------------------------------------

export function MemoryPanel({ projectId }: { projectId: string }) {
  const [items, setItems] = useState<MemoryItem[]>([]);

  async function load() {
    const { data } = await devstudio.listMemory(projectId);
    setItems(data.memory);
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <PageHeader icon={Brain} title="Project Memory" description="Accumulates automatically as tasks run" />
      <div className="flex-1 overflow-y-auto">
        <div className="p-6 max-w-3xl mx-auto space-y-2.5">
          {items.map((m) => (
            <div key={m.id} className="rounded-lg border border-white/10 bg-white/[0.03] p-3 hover:border-white/15 hover:-translate-y-0.5 hover:shadow-soft transition-all duration-150">
              <div className="flex items-center gap-2 mb-1.5">
                <Badge tone="brand">{m.category}</Badge>
                {m.stale && <Badge tone="warning" dot>stale</Badge>}
                <span className="text-xs font-medium text-white/85">{m.title}</span>
              </div>
              <div className="text-xs text-white/55 whitespace-pre-wrap leading-relaxed">{m.content}</div>
            </div>
          ))}
          {!items.length && (
            <EmptyState icon={BrainCircuit} title="No memory recorded yet" description="It accumulates as tasks run — decisions, gotchas, and context worth remembering across sessions." />
          )}
        </div>
      </div>
    </div>
  );
}

// --- Agents config -----------------------------------------------------------------------

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  gemini: "Gemini",
  gemini_enterprise: "Gemini Enterprise Agent Platform",
  bedrock: "Amazon Bedrock",
  emergent: "Emergent",
};

const EDITABLE_FIELDS = [
  "enabled", "primary_provider", "primary_model", "fallback_provider", "fallback_model",
  "reasoning_level", "max_attempts", "automatic_fallback", "mcp_servers", "tools_enabled",
] as const;

type ModelInfo = { id: string; provider: string; label: string };
type AgentDraft = {
  enabled: boolean;
  primary_provider: string;
  primary_model: string;
  fallback_provider: string | null;
  fallback_model: string | null;
  reasoning_level: string | null;
  max_attempts: number;
  automatic_fallback: boolean;
  mcp_servers: string[];
  tools_enabled: string[];
};

const PRESETS = ["ECONOMICAL", "BALANCED", "MAX_QUALITY"] as const;

function NewCustomRoleForm({ onCreated }: { onCreated: () => void }) {
  const [role, setRole] = useState("");
  const [label, setLabel] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    const slug = role.trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
    if (!slug || !label.trim() || !systemPrompt.trim()) {
      toast.error("Role slug, label, and system prompt are all required");
      return;
    }
    setBusy(true);
    try {
      await devstudio.createAgentRole({
        role: slug, label: label.trim(), system_prompt: systemPrompt.trim(), category: "implementer",
      });
      toast.success(`Created ${label.trim()}`);
      onCreated();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not create agent role");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-indigo-500/30 bg-indigo-500/[0.06] p-3 space-y-2 animate-fade-in">
      <div className="text-xs font-medium text-white/80">New custom agent role</div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <div className="text-white/40 mb-1">Slug (unique, used internally)</div>
          <Input value={role} onChange={(e) => setRole(e.target.value)} placeholder="e.g. localization" />
        </div>
        <div>
          <div className="text-white/40 mb-1">Display name</div>
          <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Localization Agent" />
        </div>
      </div>
      <div>
        <div className="text-white/40 mb-1">System prompt</div>
        <Textarea rows={4} value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)}
          placeholder="You are the ... Agent. Given ..., you should ..." />
      </div>
      <div className="text-[11px] text-white/35">
        The Planner can assign plan items directly to this role, exactly like Design/Frontend/Backend —
        it returns file edits using the same contract.
      </div>
      <div className="flex justify-end">
        <Button size="sm" disabled={busy} loading={busy} onClick={submit}>Create role</Button>
      </div>
    </div>
  );
}

export function AgentsPanel() {
  const [agents, setAgents] = useState<Record<string, AgentDraft>>({});
  const [drafts, setDrafts] = useState<Record<string, AgentDraft>>({});
  const [modelsByProvider, setModelsByProvider] = useState<Record<string, ModelInfo[]>>({});
  const [providers, setProviders] = useState<string[]>([]);
  const [savingRole, setSavingRole] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [bulkProvider, setBulkProvider] = useState("");
  const [bulkModel, setBulkModel] = useState("");
  const [bulkApplying, setBulkApplying] = useState<"primary" | "fallback" | null>(null);
  const [mcpServers, setMcpServers] = useState<{ name: string; enabled: boolean }[]>([]);
  const [builtinTools, setBuiltinTools] = useState<{ name: string; description: string }[]>([]);
  const [builtinRoles, setBuiltinRoles] = useState<string[]>([]);
  const [customRoles, setCustomRoles] = useState<{ role: string; label: string; built_in: boolean }[]>([]);
  const [newRoleOpen, setNewRoleOpen] = useState(false);

  async function load() {
    const [{ data: agentData }, { data: modelData }, { data: mcpData }, { data: toolsData }, { data: rolesData }] =
      await Promise.all([
        devstudio.agentConfigs(),
        devstudio.providerModels(),
        devstudio.listMcpServers(),
        devstudio.listBuiltinTools(),
        devstudio.listAgentRoles(),
      ]);
    setAgents(agentData.agents);
    setDrafts(Object.fromEntries(Object.entries(agentData.agents).map(([r, c]) => [r, { ...(c as AgentDraft) }])));
    setModelsByProvider(modelData.models);
    setProviders(modelData.providers);
    setMcpServers(mcpData.servers);
    setBuiltinTools(toolsData.tools);
    setBuiltinRoles(rolesData.builtin_roles);
    setCustomRoles(rolesData.custom_roles);
  }
  useEffect(() => {
    load();
  }, []);

  function roleLabel(role: string): string {
    return customRoles.find((r) => r.role === role)?.label || role.replace(/_/g, " ");
  }

  function toggleArrayField(role: string, field: "mcp_servers" | "tools_enabled", value: string) {
    setDrafts((d) => {
      const current = d[role][field];
      const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
      return { ...d, [role]: { ...d[role], [field]: next } };
    });
  }

  async function deleteRole(role: string) {
    try {
      await devstudio.deleteAgentRole(role);
      toast.success(`Deleted ${roleLabel(role)}`);
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not delete role");
    }
  }

  async function applyPreset(preset: string) {
    const { data } = await devstudio.applyPreset(preset);
    setAgents(data.agents);
    setDrafts(Object.fromEntries(Object.entries(data.agents).map(([r, c]) => [r, { ...(c as AgentDraft) }])));
    toast.success(`Applied ${preset} preset to all agents`);
  }

  async function applyBulkProvider(target: "primary" | "fallback") {
    if (!bulkProvider || !bulkModel) return;
    setBulkApplying(target);
    try {
      const roles = Object.keys(drafts);
      await Promise.all(
        roles.map((role) =>
          devstudio.updateAgentConfig(role, {
            [`${target}_provider`]: bulkProvider,
            [`${target}_model`]: bulkModel,
          }),
        ),
      );
      await load();
      toast.success(`Set ${target} to ${PROVIDER_LABELS[bulkProvider] || bulkProvider} on every role`);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not switch all roles");
    } finally {
      setBulkApplying(null);
    }
  }

  function setField<K extends keyof AgentDraft>(role: string, field: K, value: AgentDraft[K]) {
    setDrafts((d) => ({ ...d, [role]: { ...d[role], [field]: value } }));
  }

  function isDirty(role: string): boolean {
    const a = agents[role], d = drafts[role];
    if (!a || !d) return false;
    return EDITABLE_FIELDS.some((k) => (a[k] ?? null) !== (d[k] ?? null));
  }

  function toggleExpanded(role: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(role)) next.delete(role);
      else next.add(role);
      return next;
    });
  }

  async function save(role: string) {
    setSavingRole(role);
    try {
      const d = drafts[role];
      const { data } = await devstudio.updateAgentConfig(role, {
        enabled: d.enabled,
        primary_provider: d.primary_provider,
        primary_model: d.primary_model,
        fallback_provider: d.fallback_provider || null,
        fallback_model: d.fallback_model || null,
        reasoning_level: d.reasoning_level || null,
        max_attempts: d.max_attempts,
        automatic_fallback: d.automatic_fallback,
        mcp_servers: d.mcp_servers,
        tools_enabled: d.tools_enabled,
      });
      setAgents((a) => ({ ...a, [role]: data }));
      setDrafts((dr) => ({ ...dr, [role]: { ...data } }));
      toast.success(`Saved ${role.replace(/_/g, " ")}`);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not save agent config");
    } finally {
      setSavingRole(null);
    }
  }

  function modelOptions(provider: string) {
    const models = modelsByProvider[provider] || [];
    if (!models.length) return [{ value: "", label: "(no models)" }];
    return models.map((m) => ({ value: m.id, label: m.label }));
  }

  const providerOptions = providers.map((p) => ({ value: p, label: PROVIDER_LABELS[p] || p }));

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <PageHeader
        icon={Bot}
        title="Agents"
        description="Model routing per role"
        actions={
          <div className="flex gap-1.5">
            {PRESETS.map((p) => (
              <Button key={p} size="sm" variant="outline" onClick={() => applyPreset(p)}>
                {p}
              </Button>
            ))}
            <Button size="sm" onClick={() => setNewRoleOpen((v) => !v)}>
              {newRoleOpen ? "Cancel" : "New custom agent"}
            </Button>
          </div>
        }
      />
      <div className="flex-1 overflow-y-auto">
        <div className="p-6 max-w-3xl mx-auto space-y-2.5">
          <div className="text-[11px] text-white/40 -mt-1 mb-1">
            Presets set every role at once. Expand a role to override it individually — including
            its fallback, MCP servers, and tools — without leaving the preset for everything else.
          </div>

          {newRoleOpen && <NewCustomRoleForm onCreated={() => { setNewRoleOpen(false); load(); }} />}

          <div className="rounded-lg border border-white/10 bg-white/[0.03] p-3 space-y-2">
            <div className="text-xs font-medium text-white/70">Switch every role at once</div>
            <div className="grid grid-cols-2 gap-2">
              <Select
                value={bulkProvider}
                placeholder="Provider…"
                options={providerOptions}
                onChange={(e) => {
                  const provider = e.target.value;
                  setBulkProvider(provider);
                  setBulkModel((modelsByProvider[provider] || [])[0]?.id || "");
                }}
              />
              <Select
                value={bulkModel}
                placeholder="Model…"
                disabled={!bulkProvider}
                options={bulkProvider ? modelOptions(bulkProvider) : [{ value: "", label: "—" }]}
                onChange={(e) => setBulkModel(e.target.value)}
              />
            </div>
            <div className="flex items-center gap-1.5">
              <Button
                size="sm" variant="outline"
                disabled={!bulkProvider || !bulkModel || bulkApplying !== null}
                loading={bulkApplying === "primary"}
                onClick={() => applyBulkProvider("primary")}
              >
                Set all primary
              </Button>
              <Button
                size="sm" variant="outline"
                disabled={!bulkProvider || !bulkModel || bulkApplying !== null}
                loading={bulkApplying === "fallback"}
                onClick={() => applyBulkProvider("fallback")}
              >
                Set all fallback
              </Button>
              <span className="text-[11px] text-white/35 ml-1">Applies to all {Object.keys(drafts).length} roles</span>
            </div>
          </div>

          {Object.entries(drafts).map(([role, cfg], i) => {
            const isOpen = expanded.has(role);
            const dirty = isDirty(role);
            return (
              <div
                key={role}
                style={{ animationDelay: `${i * 25}ms` }}
                className="rounded-lg border border-white/10 bg-white/[0.03] overflow-hidden transition-colors hover:border-white/15 animate-fade-up"
              >
                <button
                  onClick={() => toggleExpanded(role)}
                  className="w-full flex items-center gap-2.5 px-3 py-2.5 text-left"
                >
                  <ChevronRight className={`w-3.5 h-3.5 text-white/35 flex-shrink-0 transition-transform duration-150 ${isOpen ? "rotate-90" : ""}`} />
                  <span className="font-medium text-white/85 text-xs flex-shrink-0">{roleLabel(role)}</span>
                  {!builtinRoles.includes(role) && <Badge tone="brand" className="flex-shrink-0">custom</Badge>}
                  <span className="text-white/35 text-[11px] truncate min-w-0">
                    {cfg.primary_provider}/{cfg.primary_model || "—"}
                    {cfg.fallback_model && <span> → {cfg.fallback_provider}/{cfg.fallback_model}</span>}
                  </span>
                  <div className="ml-auto flex items-center gap-1.5 flex-shrink-0">
                    {(cfg.mcp_servers?.length || cfg.tools_enabled?.length) ? (
                      <Badge tone="info">
                        {(cfg.mcp_servers?.length || 0) + (cfg.tools_enabled?.length || 0)} tools
                      </Badge>
                    ) : null}
                    {dirty && <Badge tone="warning" dot className="animate-scale-in">unsaved</Badge>}
                    {!cfg.enabled && <Badge tone="neutral">disabled</Badge>}
                  </div>
                </button>

                {isOpen && (
                  <div className="px-3 pb-3 pt-1 space-y-2.5 text-xs border-t border-white/[0.06] animate-fade-in">
                    <div className="flex items-center justify-end pt-2.5">
                      <label className="flex items-center gap-1.5 text-white/60 cursor-pointer">
                        <input type="checkbox" className="accent-indigo-500" checked={cfg.enabled}
                          onChange={(e) => setField(role, "enabled", e.target.checked)} />
                        enabled
                      </label>
                    </div>

                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <div className="text-white/40 mb-1">Primary provider</div>
                        <Select value={cfg.primary_provider} options={providerOptions}
                          onChange={(e) => {
                            const provider = e.target.value;
                            const firstModel = (modelsByProvider[provider] || [])[0]?.id || "";
                            setDrafts((d) => ({ ...d, [role]: { ...d[role], primary_provider: provider, primary_model: firstModel } }));
                          }} />
                      </div>
                      <div>
                        <div className="text-white/40 mb-1">Primary model</div>
                        <Select value={cfg.primary_model} options={modelOptions(cfg.primary_provider)}
                          onChange={(e) => setField(role, "primary_model", e.target.value)} />
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <div className="text-white/40 mb-1">Fallback provider</div>
                        <Select value={cfg.fallback_provider || ""}
                          options={[{ value: "", label: "None" }, ...providerOptions]}
                          onChange={(e) => {
                            const provider = e.target.value;
                            const firstModel = provider ? (modelsByProvider[provider] || [])[0]?.id || "" : "";
                            setDrafts((d) => ({
                              ...d,
                              [role]: { ...d[role], fallback_provider: provider || null, fallback_model: firstModel || null },
                            }));
                          }} />
                      </div>
                      <div>
                        <div className="text-white/40 mb-1">Fallback model</div>
                        <Select value={cfg.fallback_model || ""} disabled={!cfg.fallback_provider}
                          options={cfg.fallback_provider ? modelOptions(cfg.fallback_provider) : [{ value: "", label: "—" }]}
                          onChange={(e) => setField(role, "fallback_model", e.target.value)} />
                      </div>
                    </div>

                    <div className="grid grid-cols-3 gap-2">
                      <div>
                        <div className="text-white/40 mb-1">Reasoning level</div>
                        <Select value={cfg.reasoning_level || ""}
                          options={[
                            { value: "", label: "none" }, { value: "low", label: "low" },
                            { value: "medium", label: "medium" }, { value: "high", label: "high" },
                          ]}
                          onChange={(e) => setField(role, "reasoning_level", e.target.value || null)} />
                      </div>
                      <div>
                        <div className="text-white/40 mb-1">Max attempts</div>
                        <Input type="number" min={1} max={5} value={cfg.max_attempts}
                          onChange={(e) => setField(role, "max_attempts", Number(e.target.value) || 1)} />
                      </div>
                      <div>
                        <div className="text-white/40 mb-1">Auto-fallback</div>
                        <label className="flex items-center gap-1.5 text-white/60 h-8 cursor-pointer">
                          <input type="checkbox" className="accent-indigo-500" checked={cfg.automatic_fallback}
                            onChange={(e) => setField(role, "automatic_fallback", e.target.checked)} />
                          on failure
                        </label>
                      </div>
                    </div>

                    {(mcpServers.length > 0 || builtinTools.length > 0) && (
                      <div className="grid grid-cols-2 gap-3 pt-1 border-t border-white/[0.06] mt-1">
                        <div>
                          <div className="text-white/40 mb-1">MCP servers</div>
                          {mcpServers.length === 0 && <div className="text-white/25 text-[11px]">None connected — see Settings.</div>}
                          <div className="space-y-1">
                            {mcpServers.map((s) => (
                              <label key={s.name} className="flex items-center gap-1.5 text-white/60 cursor-pointer">
                                <input type="checkbox" className="accent-indigo-500"
                                  checked={cfg.mcp_servers.includes(s.name)}
                                  onChange={() => toggleArrayField(role, "mcp_servers", s.name)} />
                                {s.name} {!s.enabled && <span className="text-white/25">(disabled)</span>}
                              </label>
                            ))}
                          </div>
                        </div>
                        <div>
                          <div className="text-white/40 mb-1">Tools</div>
                          <div className="space-y-1">
                            {builtinTools.map((t) => (
                              <label key={t.name} className="flex items-center gap-1.5 text-white/60 cursor-pointer" title={t.description}>
                                <input type="checkbox" className="accent-indigo-500"
                                  checked={cfg.tools_enabled.includes(t.name)}
                                  onChange={() => toggleArrayField(role, "tools_enabled", t.name)} />
                                {t.name.replace(/_/g, " ")}
                              </label>
                            ))}
                          </div>
                        </div>
                      </div>
                    )}

                    <div className="flex items-center justify-between pt-1">
                      {!builtinRoles.includes(role) && !customRoles.find((r) => r.role === role)?.built_in ? (
                        <Button size="sm" variant="outline" className="text-red-300 border-red-500/30" onClick={() => deleteRole(role)}>
                          Delete role
                        </Button>
                      ) : <span />}
                      <Button size="sm" disabled={!dirty || savingRole === role} loading={savingRole === role} onClick={() => save(role)}>
                        Save
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// --- GitHub ------------------------------------------------------------------------------

export function GitHubPanel() {
  const [status, setStatus] = useState<any>(null);
  const [repos, setRepos] = useState<any[]>([]);
  const [reposError, setReposError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    setReposError(null);
    try {
      const { data } = await devstudio.githubWhoami();
      setStatus(data);
      if (data.available) {
        try {
          const r = await devstudio.githubRepos();
          setRepos(r.data.repos);
        } catch (e: any) {
          // Connectivity can be fine while listing repos still fails (e.g. a token scoped to
          // specific repos rather than full account access) — surface it instead of going quiet.
          setRepos([]);
          setReposError(e?.response?.data?.detail || "Could not list repositories");
        }
      }
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <PageHeader
        icon={Github}
        title="GitHub"
        actions={
          <Button size="icon" variant="ghost" onClick={load} title="Refresh">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          </Button>
        }
      />
      <div className="flex-1 overflow-y-auto">
        <div className="p-6 max-w-3xl mx-auto space-y-4">
          <div className={`rounded-lg border p-3 flex items-center gap-2.5 ${
            status?.available ? "border-emerald-500/25 bg-emerald-500/[0.06]" : "border-amber-500/25 bg-amber-500/[0.06]"
          }`}>
            {status?.available ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />
            ) : (
              <XCircle className="w-4 h-4 text-amber-400 flex-shrink-0" />
            )}
            <span className="text-xs text-white/80">{status?.detail || "Checking…"}</span>
          </div>
          {!status?.available && (
            <div className="text-xs text-white/50">
              Configure a GitHub Personal Access Token in Settings to browse repositories, index code,
              and commit/push from Dev Studio.
            </div>
          )}
          {reposError && (
            <div className="rounded-lg border border-amber-500/25 bg-amber-500/[0.06] p-3 text-xs text-amber-200">
              {reposError}
            </div>
          )}
          <div className="space-y-1.5">
            {repos.map((r) => (
              <div key={r.full_name} className="rounded-lg border border-white/10 bg-white/[0.03] p-3 text-xs flex items-center justify-between hover:border-white/15 hover:-translate-y-0.5 hover:shadow-soft transition-all duration-150">
                <span className="text-white/80 flex items-center gap-2">
                  <Github className="w-3.5 h-3.5 text-white/35 flex-shrink-0" /> {r.full_name}
                </span>
                <Badge className="border-white/15 text-white/50 font-mono">{r.default_branch}</Badge>
              </div>
            ))}
          </div>
          {!loading && status?.available && !repos.length && !reposError && (
            <EmptyState icon={Github} title="No repositories visible" description="Your token may be scoped to specific repositories." />
          )}
        </div>
      </div>
    </div>
  );
}

// --- Settings ----------------------------------------------------------------------------

const SECRET_FIELDS: { key: string; label: string; placeholder: string; group: string; multiline?: boolean }[] = [
  { key: "github_pat", label: "GitHub Personal Access Token", placeholder: "ghp_…", group: "GitHub" },
  { key: "anthropic_api_key", label: "Anthropic API Key", placeholder: "sk-ant-…", group: "Native API keys" },
  { key: "openai_api_key", label: "OpenAI API Key", placeholder: "sk-…", group: "Native API keys" },
  { key: "gemini_api_key", label: "Gemini API Key", placeholder: "AIza…", group: "Native API keys" },
  { key: "emergent_universal_key", label: "Emergent Universal Key", placeholder: "sk-emergent-…", group: "Emergent" },
  { key: "aws_access_key_id", label: "AWS Access Key ID", placeholder: "AKIA…", group: "Amazon Bedrock" },
  { key: "aws_secret_access_key", label: "AWS Secret Access Key", placeholder: "…", group: "Amazon Bedrock" },
  { key: "aws_session_token", label: "AWS Session Token (optional, for temporary credentials)", placeholder: "…", group: "Amazon Bedrock" },
  { key: "aws_region", label: "AWS Region", placeholder: "us-east-1", group: "Amazon Bedrock" },
  { key: "gcp_project_id", label: "GCP Project ID", placeholder: "my-project-123", group: "Gemini Enterprise Agent Platform" },
  { key: "gcp_location", label: "GCP Location (Gemini models)", placeholder: "us-central1", group: "Gemini Enterprise Agent Platform" },
  { key: "gcp_claude_region", label: "Claude Region (optional — Claude models only; \"global\", \"us\", \"eu\", or a specific region)", placeholder: "global", group: "Gemini Enterprise Agent Platform" },
  { key: "gcp_service_account_json", label: "Service Account JSON (optional — paste the full key file contents)", placeholder: '{"type": "service_account", …}', group: "Gemini Enterprise Agent Platform", multiline: true },
  { key: "perplexity_api_key", label: "Perplexity API Key", placeholder: "pplx-…", group: "Tools" },
];
const SECRET_GROUPS = ["GitHub", "Native API keys", "Amazon Bedrock", "Gemini Enterprise Agent Platform", "Emergent", "Tools"];

type TestOutcome = "idle" | "testing" | "ok" | "error" | "not_configured" | "not_implemented";
type TestState = { state: TestOutcome; detail?: string; latency_ms?: number };

type McpServer = {
  id: string;
  name: string;
  transport: "stdio" | "http";
  command?: string | null;
  args: string[];
  url?: string | null;
  env_keys: string[];
  enabled: boolean;
  preset?: string | null;
  last_tool_count?: number | null;
  last_checked_at?: string | null;
  last_error?: string | null;
};

type McpPreset = {
  label: string;
  transport: "stdio" | "http";
  command?: string;
  args?: string[];
  env_keys: string[];
  description: string;
};

function NewMcpServerForm({ presets, onCreated }: { presets: Record<string, McpPreset>; onCreated: () => void }) {
  const [mode, setMode] = useState<"preset" | "custom">(Object.keys(presets).length ? "preset" : "custom");
  const [presetKey, setPresetKey] = useState<string>(Object.keys(presets)[0] || "");
  const [name, setName] = useState("");
  const [envValues, setEnvValues] = useState<Record<string, string>>({});
  const [transport, setTransport] = useState<"stdio" | "http">("stdio");
  const [command, setCommand] = useState("");
  const [args, setArgs] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);

  const preset = mode === "preset" ? presets[presetKey] : null;

  async function submit() {
    const finalName = (mode === "preset" ? name || presetKey : name).trim();
    if (!finalName) {
      toast.error("Give this server a name");
      return;
    }
    setBusy(true);
    try {
      if (mode === "preset" && preset) {
        const missing = preset.env_keys.filter((k) => !envValues[k]?.trim());
        if (missing.length) {
          toast.error(`Missing required: ${missing.join(", ")}`);
          setBusy(false);
          return;
        }
        await devstudio.createMcpServer({
          name: finalName, transport: preset.transport, command: preset.command,
          args: preset.args || [], env: envValues, preset: presetKey,
        });
      } else {
        if (!command.trim() && transport === "stdio") {
          toast.error("A command is required for a stdio server");
          setBusy(false);
          return;
        }
        if (!url.trim() && transport === "http") {
          toast.error("A URL is required for an http server");
          setBusy(false);
          return;
        }
        await devstudio.createMcpServer({
          name: finalName, transport,
          command: transport === "stdio" ? command.trim() : undefined,
          args: transport === "stdio" ? args.split(/\s+/).filter(Boolean) : [],
          url: transport === "http" ? url.trim() : undefined,
          env: envValues,
        });
      }
      toast.success(`Added MCP server "${finalName}"`);
      setName("");
      setEnvValues({});
      setCommand("");
      setArgs("");
      setUrl("");
      onCreated();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not add MCP server");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-indigo-500/30 bg-indigo-500/[0.06] p-3 space-y-2.5 animate-fade-in">
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium text-white/80">Add MCP server</div>
        <div className="flex rounded-md border border-white/10 overflow-hidden text-[11px]">
          <button type="button" onClick={() => setMode("preset")}
            className={`px-2 py-1 ${mode === "preset" ? "bg-white/10 text-white" : "text-white/40"}`}>
            Preset
          </button>
          <button type="button" onClick={() => setMode("custom")}
            className={`px-2 py-1 ${mode === "custom" ? "bg-white/10 text-white" : "text-white/40"}`}>
            Custom
          </button>
        </div>
      </div>

      {mode === "preset" ? (
        <div className="space-y-2">
          <Select value={presetKey}
            options={Object.entries(presets).map(([key, p]) => ({ value: key, label: p.label }))}
            onChange={(e) => setPresetKey(e.target.value)} />
          {preset && (
            <>
              <div className="text-[11px] text-white/40">{preset.description}</div>
              <Input value={name} onChange={(e) => setName(e.target.value)}
                placeholder={`Name (defaults to "${presetKey}")`} />
              {preset.env_keys.map((k) => (
                <Input key={k} type="password" value={envValues[k] || ""}
                  onChange={(e) => setEnvValues((v) => ({ ...v, [k]: e.target.value }))}
                  placeholder={k} />
              ))}
            </>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Server name" />
          <Select value={transport}
            options={[{ value: "stdio", label: "stdio (command)" }, { value: "http", label: "http (streamable URL)" }]}
            onChange={(e) => setTransport(e.target.value as "stdio" | "http")} />
          {transport === "stdio" ? (
            <>
              <Input value={command} onChange={(e) => setCommand(e.target.value)} placeholder="Command, e.g. npx" />
              <Input value={args} onChange={(e) => setArgs(e.target.value)}
                placeholder="Args, space-separated, e.g. -y @scope/some-mcp-server" />
            </>
          ) : (
            <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…/mcp" />
          )}
          <div className="text-[11px] text-white/35">
            Optional env vars (e.g. an API token), one per line as KEY=value:
          </div>
          <Textarea rows={2} placeholder="TOKEN=…"
            onChange={(e) => {
              const parsed: Record<string, string> = {};
              for (const line of e.target.value.split("\n")) {
                const idx = line.indexOf("=");
                if (idx > 0) parsed[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
              }
              setEnvValues(parsed);
            }} />
        </div>
      )}

      <div className="flex justify-end">
        <Button size="sm" disabled={busy} loading={busy} onClick={submit}>Add server</Button>
      </div>
    </div>
  );
}

function McpServersTab() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [presets, setPresets] = useState<Record<string, McpPreset>>({});
  const [formOpen, setFormOpen] = useState(false);
  const [testing, setTesting] = useState<Record<string, boolean>>({});
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; tools?: string[]; error?: string }>>({});

  async function load() {
    const [{ data: serverData }, { data: presetData }] = await Promise.all([
      devstudio.listMcpServers(),
      devstudio.listMcpPresets(),
    ]);
    setServers(serverData.servers);
    setPresets(presetData.presets);
  }
  useEffect(() => {
    load();
  }, []);

  async function test(id: string) {
    setTesting((t) => ({ ...t, [id]: true }));
    try {
      const { data } = await devstudio.testMcpServer(id);
      setTestResults((r) => ({
        ...r,
        [id]: data.ok ? { ok: true, tools: (data.tools || []).map((t: any) => t.name) } : { ok: false, error: data.error },
      }));
      await load();
    } catch (e: any) {
      setTestResults((r) => ({ ...r, [id]: { ok: false, error: e?.response?.data?.detail || "Request failed" } }));
    } finally {
      setTesting((t) => ({ ...t, [id]: false }));
    }
  }

  async function remove(server: McpServer) {
    try {
      await devstudio.deleteMcpServer(server.id);
      toast.success(`Removed "${server.name}"`);
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not remove server");
    }
  }

  return (
    <div className="space-y-3">
      <div className="text-[11px] text-white/40 flex items-start gap-1.5">
        <Bot className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
        <span>
          Any MCP (Model Context Protocol) server — presets for Memory (no credentials) and Notion,
          or a fully custom stdio/http server. Assign servers to a role's tools in the Agents tab.
          "Test" connects for real and lists the server's actual tools.
        </span>
      </div>

      {servers.map((s) => {
        const result = testResults[s.id];
        return (
          <div key={s.id} className="rounded-lg border border-white/10 bg-white/[0.03] p-3 text-xs space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="text-white/80 font-medium flex-1 truncate">{s.name}</span>
              {s.preset && <Badge tone="neutral">{s.preset} preset</Badge>}
              <Badge tone="neutral">{s.transport}</Badge>
              {s.last_tool_count != null && !result && (
                <Badge tone="success">{s.last_tool_count} tools</Badge>
              )}
              {result?.ok && <Badge tone="success">{result.tools?.length ?? 0} tools</Badge>}
              {result && !result.ok && <Badge tone="danger">failed</Badge>}
              {!result && s.last_error && <Badge tone="danger">failed</Badge>}
              <Button size="sm" variant="outline" loading={!!testing[s.id]} onClick={() => test(s.id)}>
                Test
              </Button>
              <Button size="sm" variant="outline" onClick={() => remove(s)}>Remove</Button>
            </div>
            <div className="text-white/40 truncate">
              {s.transport === "stdio" ? `${s.command} ${(s.args || []).join(" ")}` : s.url}
              {s.env_keys.length ? ` · env: ${s.env_keys.join(", ")}` : ""}
            </div>
            {(result?.ok && result.tools?.length) ? (
              <div className="text-white/40 truncate">tools: {result.tools.join(", ")}</div>
            ) : null}
            {((result && !result.ok && result.error) || (!result && s.last_error)) && (
              <div className="text-red-400/80 truncate">{result?.error || s.last_error}</div>
            )}
          </div>
        );
      })}
      {!servers.length && (
        <EmptyState compact icon={Bot} title="No MCP servers yet" description="Add a preset or a custom server below." />
      )}

      {formOpen ? (
        <NewMcpServerForm presets={presets} onCreated={() => { setFormOpen(false); load(); }} />
      ) : (
        <Button size="sm" variant="outline" onClick={() => setFormOpen(true)}>+ Add MCP server</Button>
      )}
    </div>
  );
}

function AppearanceTab() {
  const [reduceMotion, setReduceMotionState] = useState(() => getReduceMotion());

  function toggle() {
    const next = !reduceMotion;
    setReduceMotion(next);
    setReduceMotionState(next);
  }

  return (
    <div className="space-y-3">
      <div className="text-[11px] text-white/40">
        Personal display preferences — stored on this device only, not synced to your account.
      </div>
      <div className="flex items-center justify-between gap-4 rounded-lg border border-white/10 bg-white/[0.03] px-4 py-3.5">
        <div>
          <div className="text-sm text-white/85 font-medium">Reduce motion</div>
          <div className="text-[11px] text-white/40 mt-0.5">
            Turns off panel transitions, fades, and other animation throughout Dev Studio. Your
            OS-level "reduce motion" setting is always respected too — this is for anyone who wants
            it off here specifically, either direction.
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={reduceMotion}
          onClick={toggle}
          className={`relative flex-shrink-0 h-6 w-11 rounded-full transition-colors duration-150 ${
            reduceMotion ? "bg-indigo-500" : "bg-white/15"
          }`}
        >
          <span
            className={`absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white transition-transform duration-150 ${
              reduceMotion ? "translate-x-5" : "translate-x-0"
            }`}
          />
        </button>
      </div>
    </div>
  );
}

export function SettingsPanel() {
  const [caps, setCaps] = useState<any[]>([]);
  const [secretsConfigured, setSecretsConfigured] = useState<Record<string, boolean>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [models, setModels] = useState<Record<string, { id: string; label: string }[]>>({});
  const [providers, setProviders] = useState<string[]>([]);
  const [testStatus, setTestStatus] = useState<Record<string, TestState>>({});

  async function load() {
    const { data } = await devstudio.capabilities();
    setCaps(data.capabilities);
    const s = await devstudio.getSettings();
    setSecretsConfigured(s.data.secrets_configured);
    const m = await devstudio.providerModels();
    setModels(m.data.models);
    setProviders(m.data.providers);
  }
  useEffect(() => {
    load();
  }, []);

  async function saveSecret(name: string) {
    const value = draft[name];
    if (!value) return;
    await devstudio.setSecret(name, value);
    toast.success("Saved. It is encrypted at rest and never shown again.");
    setDraft((d) => ({ ...d, [name]: "" }));
    load();
  }

  async function testModel(provider: string, model: string) {
    const key = `${provider}:${model}`;
    setTestStatus((t) => ({ ...t, [key]: { state: "testing" } }));
    try {
      const { data } = await devstudio.testProviderModel(provider, model);
      if (data.ok) {
        setTestStatus((t) => ({
          ...t,
          [key]: { state: "ok", detail: data.response_text, latency_ms: data.latency_ms },
        }));
      } else {
        setTestStatus((t) => ({
          ...t,
          [key]: { state: (data.error_type as TestOutcome) || "error", detail: data.detail, latency_ms: data.latency_ms },
        }));
      }
    } catch (e: any) {
      setTestStatus((t) => ({
        ...t,
        [key]: { state: "error", detail: e?.response?.data?.detail || "Request failed" },
      }));
    }
  }

  const configuredCount = Object.values(secretsConfigured).filter(Boolean).length;
  const availableCount = caps.filter((c) => c.available).length;

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <PageHeader icon={SettingsIcon} title="Settings" />
      <div className="flex-1 overflow-y-auto">
        <div className="p-6 max-w-2xl mx-auto">
          <Tabs defaultValue="capabilities">
            <TabsList className="mb-5">
              <TabsTrigger value="capabilities">
                capabilities {caps.length ? `(${availableCount}/${caps.length})` : ""}
              </TabsTrigger>
              <TabsTrigger value="secrets">secrets {configuredCount ? `(${configuredCount})` : ""}</TabsTrigger>
              <TabsTrigger value="test">test models</TabsTrigger>
              <TabsTrigger value="mcp">MCP servers</TabsTrigger>
              <TabsTrigger value="appearance">appearance</TabsTrigger>
            </TabsList>

            <TabsContent value="capabilities" className="space-y-1.5">
              {caps.map((c) => (
                <div key={c.name} className="flex items-center gap-2.5 text-xs rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2.5">
                  {c.available ? (
                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />
                  ) : (
                    <AlertTriangle className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />
                  )}
                  <span className="text-white/75 w-44 flex-shrink-0 capitalize">{c.name.replace(/_/g, " ")}</span>
                  <span className="text-white/40 truncate">{c.detail || (c.available ? "available" : "unavailable")}</span>
                </div>
              ))}
              {!caps.length && <EmptyState compact icon={SettingsIcon} title="Loading capabilities…" />}
            </TabsContent>

            <TabsContent value="secrets" className="space-y-5">
              {SECRET_GROUPS.map((group) => (
                <div key={group} className="space-y-3">
                  <div className="text-[11px] uppercase tracking-wide text-white/40">{group}</div>
                  {group === "Amazon Bedrock" && (
                    <div className="text-[11px] text-white/40 -mt-1">
                      Access key/secret are optional — if left blank, Bedrock falls back to the
                      standard AWS default credential chain (environment variables, an EC2/ECS/Lambda
                      IAM role). A region is always required.
                    </div>
                  )}
                  {group === "Gemini Enterprise Agent Platform" && (
                    <div className="text-[11px] text-white/40 -mt-1">
                      The same Gemini models, routed through Google Cloud (formerly Vertex AI) for
                      GCP-native governance and data residency. Service account JSON is optional —
                      if left blank, it falls back to Google's Application Default Credentials
                      (gcloud login or an attached GCP service account). A project ID is always
                      required; location defaults to us-central1.
                    </div>
                  )}
                  {group === "Tools" && (
                    <div className="text-[11px] text-white/40 -mt-1">
                      Powers the perplexity_research built-in agent tool (Settings → Agents →
                      per-role Tools). Only used when a role has that tool enabled.
                    </div>
                  )}
                  {SECRET_FIELDS.filter((f) => f.group === group).map((f) => (
                    <div key={f.key}>
                      <label className="text-xs text-white/50">
                        {f.label}{" "}
                        {secretsConfigured[f.key] && (
                          <Badge tone="success" className="ml-1">configured</Badge>
                        )}
                      </label>
                      {f.multiline ? (
                        <div className="space-y-1.5 mt-1">
                          <Textarea rows={4} value={draft[f.key] || ""}
                            onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
                            placeholder={f.placeholder} />
                          <Button size="sm" disabled={!draft[f.key]} onClick={() => saveSecret(f.key)}>Save</Button>
                        </div>
                      ) : (
                        <div className="flex gap-1.5 mt-1">
                          <Input type="password" value={draft[f.key] || ""}
                            onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
                            placeholder={f.placeholder} />
                          <Button size="sm" disabled={!draft[f.key]} onClick={() => saveSecret(f.key)}>Save</Button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              ))}
              <div className="flex items-start gap-1.5 text-[11px] text-white/40 rounded-lg border border-white/10 bg-white/[0.03] p-2.5">
                <KeyRound className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                <span>
                  Secrets are encrypted at rest and never re-displayed. Environment variables
                  (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, DEVSTUDIO_GITHUB_TOKEN,
                  EMERGENT_UNIVERSAL_KEY, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
                  AWS_SESSION_TOKEN, AWS_REGION, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION,
                  GOOGLE_APPLICATION_CREDENTIALS_JSON) override these if set.
                </span>
              </div>
            </TabsContent>

            <TabsContent value="test" className="space-y-4">
              <div className="text-[11px] text-white/40 flex items-start gap-1.5">
                <FlaskConical className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                <span>
                  Makes one real, minimal request per click using whatever credentials are
                  currently saved in the Secrets tab — not unsaved draft values. Useful for
                  confirming a Bedrock or Gemini Enterprise model ID actually resolves before
                  pointing a role at it.
                </span>
              </div>
              {providers.map((p) => (
                <div key={p} className="space-y-1.5">
                  <div className="text-[11px] uppercase tracking-wide text-white/40">
                    {PROVIDER_LABELS[p] || p}
                  </div>
                  {!(models[p] || []).length && (
                    <div className="text-[11px] text-white/30">No models listed.</div>
                  )}
                  {(models[p] || []).map((m) => {
                    const key = `${p}:${m.id}`;
                    const status = testStatus[key] || { state: "idle" as TestOutcome };
                    return (
                      <div key={m.id} className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-xs">
                        <div className="flex items-center gap-2">
                          <span className="text-white/70 flex-1 truncate">{m.label}</span>
                          {status.state === "ok" && (
                            <Badge tone="success">
                              ok{status.latency_ms != null ? ` · ${status.latency_ms}ms` : ""}
                            </Badge>
                          )}
                          {status.state === "error" && <Badge tone="danger">failed</Badge>}
                          {status.state === "not_configured" && <Badge tone="warning">not configured</Badge>}
                          {status.state === "not_implemented" && <Badge tone="neutral">stub</Badge>}
                          <Button size="sm" variant="outline" loading={status.state === "testing"}
                            onClick={() => testModel(p, m.id)}>
                            Test
                          </Button>
                        </div>
                        {status.detail && status.state !== "ok" && (
                          <div className="text-white/40 mt-1 truncate" title={status.detail}>{status.detail}</div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ))}
            </TabsContent>

            <TabsContent value="mcp">
              <McpServersTab />
            </TabsContent>

            <TabsContent value="appearance">
              <AppearanceTab />
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </div>
  );
}
