import api, { API_ORIGIN } from "./api";

const base = "/devstudio";

export const devstudio = {
  capabilities: () => api.get(`${base}/capabilities`),

  getSettings: () => api.get(`${base}/settings`),
  updateSettings: (body: Record<string, unknown>) => api.put(`${base}/settings`, body),
  setSecret: (name: string, value: string) => api.post(`${base}/settings/secrets`, { name, value }),

  githubWhoami: () => api.get(`${base}/github/whoami`),
  githubRepos: () => api.get(`${base}/github/repos`),
  githubBranches: (owner: string, repo: string) => api.get(`${base}/github/${owner}/${repo}/branches`),

  createProject: (body: Record<string, unknown>) => api.post(`${base}/projects`, body),
  createRepoAndProject: (body: Record<string, unknown>) => api.post(`${base}/projects/new-repo`, body),
  listProjects: () => api.get(`${base}/projects`),
  getProject: (id: string) => api.get(`${base}/projects/${id}`),
  projectBranches: (id: string) => api.get(`${base}/projects/${id}/branches`),
  syncProject: (id: string, branch: string) =>
    api.post(`${base}/projects/${id}/sync?branch=${encodeURIComponent(branch)}`),
  tree: (id: string, branch: string, subdir = "") =>
    api.get(`${base}/projects/${id}/tree`, { params: { branch, subdir } }),
  readFile: (id: string, branch: string, path: string) =>
    api.get(`${base}/projects/${id}/file`, { params: { branch, path } }),
  search: (id: string, branch: string, q: string) =>
    api.get(`${base}/projects/${id}/search`, { params: { branch, q } }),

  listMemory: (id: string, category?: string) =>
    api.get(`${base}/projects/${id}/memory`, { params: { category } }),
  addMemory: (id: string, body: Record<string, unknown>) => api.post(`${base}/projects/${id}/memory`, body),
  updateMemory: (memoryId: string, content: string, reason: string) =>
    api.patch(`${base}/memory/${memoryId}`, { content, reason }),
  markMemoryStale: (memoryId: string) => api.post(`${base}/memory/${memoryId}/stale`),
  deleteMemory: (memoryId: string) => api.delete(`${base}/memory/${memoryId}`),

  agentConfigs: () => api.get(`${base}/agents/config`),
  updateAgentConfig: (role: string, body: Record<string, unknown>) =>
    api.put(`${base}/agents/config/${role}`, body),
  applyPreset: (preset: string) => api.post(`${base}/agents/preset/${preset}`),
  providerModels: () => api.get(`${base}/providers/models`),
  testProviderModel: (provider: string, model: string) =>
    api.post(`${base}/providers/test`, { provider, model }),

  listBuiltinTools: () => api.get(`${base}/tools/builtin`),

  listAgentRoles: () => api.get(`${base}/agents/roles`),
  createAgentRole: (body: Record<string, unknown>) => api.post(`${base}/agents/roles`, body),
  updateAgentRole: (role: string, body: Record<string, unknown>) => api.put(`${base}/agents/roles/${role}`, body),
  deleteAgentRole: (role: string) => api.delete(`${base}/agents/roles/${role}`),

  listMcpPresets: () => api.get(`${base}/mcp/presets`),
  listMcpServers: () => api.get(`${base}/mcp/servers`),
  createMcpServer: (body: Record<string, unknown>) => api.post(`${base}/mcp/servers`, body),
  updateMcpServer: (id: string, body: Record<string, unknown>) => api.put(`${base}/mcp/servers/${id}`, body),
  deleteMcpServer: (id: string) => api.delete(`${base}/mcp/servers/${id}`),
  testMcpServer: (id: string) => api.post(`${base}/mcp/servers/${id}/test`),

  createTask: (body: Record<string, unknown>) => api.post(`${base}/tasks`, body),
  listTasks: (projectId?: string | null) => api.get(`${base}/tasks`, { params: { project_id: projectId } }),
  getTask: (id: string) => api.get(`${base}/tasks/${id}`),
  renameTask: (id: string, title: string) => api.patch(`${base}/tasks/${id}`, { title }),
  deleteTask: (id: string) => api.delete(`${base}/tasks/${id}`),
  messages: (id: string) => api.get(`${base}/tasks/${id}/messages`),
  postMessage: (id: string, text: string) => api.post(`${base}/tasks/${id}/messages`, { text }),
  runTask: (id: string) => api.post(`${base}/tasks/${id}/run`),
  stopTask: (id: string) => api.post(`${base}/tasks/${id}/stop`),
  plan: (id: string) => api.get(`${base}/tasks/${id}/plan`),
  diff: (id: string) => api.get(`${base}/tasks/${id}/diff`),
  filesTree: (id: string, subdir = "") => api.get(`${base}/tasks/${id}/files/tree`, { params: { subdir } }),
  fileRead: (id: string, path: string) => api.get(`${base}/tasks/${id}/files/read`, { params: { path } }),
  tests: (id: string) => api.get(`${base}/tasks/${id}/tests`),
  runTests: (id: string) => api.post(`${base}/tasks/${id}/tests/run`),
  screenshots: (id: string) => api.get(`${base}/tasks/${id}/screenshots`),
  runBrowser: (id: string, body: Record<string, unknown>) => api.post(`${base}/tasks/${id}/browser/run`, body),
  checkpoints: (id: string) => api.get(`${base}/tasks/${id}/checkpoints`),
  createCheckpoint: (id: string, label: string, reason?: string) =>
    api.post(`${base}/tasks/${id}/checkpoints`, { label, reason }),
  restoreCheckpoint: (id: string, checkpointId: string, confirm: boolean) =>
    api.post(`${base}/tasks/${id}/checkpoints/${checkpointId}/restore`, { confirm }),
  commit: (id: string, message: string) => api.post(`${base}/tasks/${id}/commit`, { message }),
  push: (id: string, forceAfterDrift = false) =>
    api.post(`${base}/tasks/${id}/push`, { force_after_drift: forceAfterDrift }),
  openPr: (id: string, title?: string, body?: string) => api.post(`${base}/tasks/${id}/pr`, { title, body }),
  usage: (id: string) => api.get(`${base}/tasks/${id}/usage`),

  previewLiveLocal: (id: string, subdir = "frontend") =>
    api.post(`${base}/tasks/${id}/preview/live-local`, { subdir }),
  previewStop: (id: string) => api.post(`${base}/tasks/${id}/preview/stop`),
  previewExternal: (id: string, url: string) => api.post(`${base}/tasks/${id}/preview/external`, { url }),
  previewScreenshot: (id: string) => api.get(`${base}/tasks/${id}/preview/screenshot`),
  previewStaticInfo: (id: string) => api.get(`${base}/tasks/${id}/preview/static-info`),
  previewServeUrl: (id: string) => `${API_ORIGIN}/api${base}/tasks/${id}/preview/serve/`,

  eventsUrl: (id: string) => `${API_ORIGIN}/api${base}/tasks/${id}/events`,
};

export default devstudio;
