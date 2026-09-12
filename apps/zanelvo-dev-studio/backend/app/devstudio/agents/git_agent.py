"""Git agent — deterministic (no LLM). Branch/status/diff/checkpoint/commit/push/PR, via
GitService + GitHubProvider. Never force-pushes (GitService.push refuses force=True by policy)."""
from __future__ import annotations


from ..models import Project, Task, Workspace
from ..services import github_provider
from ..services.diff_service import get_diff_summary
from ..services.git_service import GitService


async def commit(task: Task, workspace: Workspace, message: str) -> str:
    git = GitService(task_id=task.id)
    identity = await github_provider.git_identity()
    await git.commit(workspace.local_path, message, author=identity)
    return await git.current_sha(workspace.local_path)


async def push(task: Task, workspace: Workspace) -> None:
    git = GitService(task_id=task.id)
    await git.push(workspace.local_path, workspace.working_branch)


async def open_pull_request(task: Task, project: Project, workspace: Workspace,
                              title: str, body: str) -> dict:
    return await github_provider.create_pull_request(
        project.github_owner, project.github_repo, title=title, body=body,
        head=workspace.working_branch, base=workspace.base_branch,
    )


async def diff_against_base(workspace: Workspace) -> dict:
    summary = await get_diff_summary(workspace.local_path, workspace.base_commit_sha)
    return summary.to_dict()
