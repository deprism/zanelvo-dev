"""Deterministic tests for GitHubProvider.create_repo — the request it builds and how it maps a
real GitHub API response, with the actual HTTP call replaced by a monkeypatched `_post` (no
network). Covers the new "create a brand-new GitHub repository" capability (previously this app
could only connect to a repo that already existed on github.com).
"""
import asyncio

from app.devstudio.services import github_provider


def test_create_repo_sends_the_right_request_body(monkeypatch):
    captured = {}

    async def fake_post(path, body):
        captured["path"] = path
        captured["body"] = body
        return {"owner": {"login": "founder"}, "name": "my-plugin", "full_name": "founder/my-plugin",
                 "private": True, "default_branch": "main", "html_url": "https://github.com/founder/my-plugin"}

    monkeypatch.setattr(github_provider, "_post", fake_post)

    result = asyncio.run(github_provider.create_repo("my-plugin", private=True, description="A plugin"))

    assert captured["path"] == "/user/repos"
    assert captured["body"] == {"name": "my-plugin", "private": True, "auto_init": True,
                                  "description": "A plugin"}
    assert result == {"owner": "founder", "repo": "my-plugin", "full_name": "founder/my-plugin",
                        "private": True, "default_branch": "main",
                        "html_url": "https://github.com/founder/my-plugin"}


def test_create_repo_defaults_to_private_and_auto_init_and_omits_empty_description(monkeypatch):
    captured = {}

    async def fake_post(path, body):
        captured["body"] = body
        return {"owner": {"login": "x"}, "name": "y", "full_name": "x/y", "private": True,
                 "default_branch": "main", "html_url": "https://github.com/x/y"}

    monkeypatch.setattr(github_provider, "_post", fake_post)
    asyncio.run(github_provider.create_repo("y"))

    assert captured["body"]["private"] is True
    assert captured["body"]["auto_init"] is True
    assert "description" not in captured["body"]  # never sent when not provided


def test_create_repo_falls_back_to_main_if_default_branch_missing(monkeypatch):
    async def fake_post(path, body):
        return {"owner": {"login": "x"}, "name": "y", "full_name": "x/y", "private": False,
                 "default_branch": None, "html_url": "https://github.com/x/y"}

    monkeypatch.setattr(github_provider, "_post", fake_post)
    result = asyncio.run(github_provider.create_repo("y", private=False))
    assert result["default_branch"] == "main"


def test_create_repo_can_be_made_public(monkeypatch):
    captured = {}

    async def fake_post(path, body):
        captured["body"] = body
        return {"owner": {"login": "x"}, "name": "y", "full_name": "x/y", "private": False,
                 "default_branch": "main", "html_url": "https://github.com/x/y"}

    monkeypatch.setattr(github_provider, "_post", fake_post)
    result = asyncio.run(github_provider.create_repo("y", private=False))
    assert captured["body"]["private"] is False
    assert result["private"] is False
