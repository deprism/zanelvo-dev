"""RepositoryService — Project CRUD + repository browsing (tree/read/search) against a
fast-forwarded local checkout, independent of any task workspace."""
from __future__ import annotations

from typing import List, Optional

from bson import ObjectId

from ...db import get_db
from ..models import CreateProjectRequest, CreateRepoAndProjectRequest, Project
from . import github_provider
from .file_service import FileService
from .git_service import GitService
from .workspace_manager import ensure_browse_checkout


async def create_new_repo_and_project(req: CreateRepoAndProjectRequest) -> Project:
    """Creates a brand-new GitHub repository (not a connection to an existing one — see
    create_project below for that) and immediately turns it into a Dev Studio Project, so a
    founder starting from nothing never has to leave Dev Studio to first go create the repo by
    hand on github.com. Reuses create_project for the actual Project bookkeeping so both paths
    stay in sync."""
    repo_meta = await github_provider.create_repo(
        req.repo_name, private=req.private, description=req.description)
    return await create_project(CreateProjectRequest(
        github_owner=repo_meta["owner"], github_repo=repo_meta["repo"],
        default_branch=repo_meta["default_branch"], name=req.name or repo_meta["full_name"],
        description=req.description,
    ))


async def create_project(req: CreateProjectRequest) -> Project:
    repo_meta = await github_provider.get_repo(req.github_owner, req.github_repo)
    project = Project(
        name=req.name or f"{req.github_owner}/{req.github_repo}",
        github_owner=req.github_owner, github_repo=req.github_repo,
        default_branch=req.default_branch or repo_meta["default_branch"],
        description=req.description,
    )
    res = await get_db().ds_projects.insert_one(project.to_mongo())
    project.id = str(res.inserted_id)
    return project


async def list_projects() -> List[Project]:
    docs = get_db().ds_projects.find({"archived": {"$ne": True}}).sort("created_at", -1)
    return [Project.from_mongo(d) async for d in docs]


async def get_project(project_id: str) -> Optional[Project]:
    doc = await get_db().ds_projects.find_one({"_id": ObjectId(project_id)})
    return Project.from_mongo(doc)


async def list_branches(project: Project) -> list:
    return await github_provider.list_branches(project.github_owner, project.github_repo)


async def browse_tree(project: Project, branch: str, subdir: str = ""):
    path = await ensure_browse_checkout(project, branch)
    return FileService(path).list_tree(subdir)


async def browse_read_file(project: Project, branch: str, rel_path: str) -> str:
    path = await ensure_browse_checkout(project, branch)
    return FileService(path).read_file(rel_path)


async def browse_search(project: Project, branch: str, query: str, glob: Optional[str] = None):
    path = await ensure_browse_checkout(project, branch)
    return FileService(path).search_repo(query, glob=glob)


async def current_commit_sha(project: Project, branch: str) -> str:
    path = await ensure_browse_checkout(project, branch)
    return await GitService().current_sha(path)
