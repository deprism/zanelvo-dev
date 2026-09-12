"""CheckpointService — git-backed task checkpoints. Purely within the task's isolated workspace
clone; never touches GitHub or the running application's own repository. Restore is destructive
to the workspace's uncommitted state, so it always requires an explicit `confirm=True`."""
from __future__ import annotations

from typing import List, Optional

from bson import ObjectId

from ...db import get_db
from ..models import Checkpoint, Workspace
from . import github_provider
from .git_service import GitService


class RestoreNotConfirmed(Exception):
    pass


async def create_checkpoint(task_id: str, workspace: Workspace, label: str,
                             reason: Optional[str] = None) -> Checkpoint:
    git = GitService(task_id=task_id)
    identity = await github_provider.git_identity()
    await git.commit(workspace.local_path, f"[checkpoint] {label}", allow_empty=True, author=identity)
    sha = await git.current_sha(workspace.local_path)
    tag = f"ds-checkpoint-{sha[:10]}"
    await git.tag_checkpoint(workspace.local_path, tag)
    cp = Checkpoint(task_id=task_id, label=label, commit_sha=sha, branch=workspace.working_branch,
                     reason=reason)
    res = await get_db().ds_checkpoints.insert_one(cp.to_mongo())
    cp.id = str(res.inserted_id)
    return cp


async def list_checkpoints(task_id: str) -> List[Checkpoint]:
    docs = get_db().ds_checkpoints.find({"task_id": task_id}).sort("created_at", -1)
    return [Checkpoint.from_mongo(d) async for d in docs]


async def restore_checkpoint(task_id: str, workspace: Workspace, checkpoint_id: str,
                              confirm: bool = False) -> Checkpoint:
    if not confirm:
        raise RestoreNotConfirmed("Restoring a checkpoint discards uncommitted work in the "
                                    "workspace. Pass confirm=true to proceed.")
    doc = await get_db().ds_checkpoints.find_one({"_id": ObjectId(checkpoint_id), "task_id": task_id})
    if not doc:
        raise ValueError("Checkpoint not found")
    cp = Checkpoint.from_mongo(doc)
    git = GitService(task_id=task_id)
    await git.reset_hard(workspace.local_path, cp.commit_sha)
    return cp
