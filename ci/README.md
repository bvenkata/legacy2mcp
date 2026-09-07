# Workflows staged here

GitHub refuses pushes that create files under `.github/workflows/` when the
pushing credential lacks the `workflow` OAuth scope. Workflows are staged in
this directory and moved into place from a checkout that has push rights.

## `github-actions-release.yml` — publish on tag

Tag-triggered release: builds and publishes the **PyPI package** (via PyPI
Trusted Publishing) and then the **MCP Registry** entry (via GitHub OIDC).
No API tokens stored anywhere.

Enable it:

```bash
git mv ci/github-actions-release.yml .github/workflows/release.yml
git commit -m "Enable release workflow"
git push
```

One-time setup before the first tag:

1. **PyPI Trusted Publisher** — at <https://pypi.org/manage/account/publishing/>
   add a pending publisher: project `legacy2mcp`, owner `bvenkata`, repo
   `legacy2mcp`, workflow `release.yml`, environment `pypi`.
2. **GitHub environment** — create an environment named `pypi` in the repo
   settings (protect it to tags if you like).
3. The MCP Registry `io.github.bvenkata/*` namespace needs no setup — OIDC from
   this repo is authorized automatically.

Then every release is:

```bash
# bump version in pyproject.toml AND server.json (both must match the tag)
git tag v0.1.1 && git push origin v0.1.1
```

> The CI workflow (`.github/workflows/ci.yml`) was bootstrapped the same way.
