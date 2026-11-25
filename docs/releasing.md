# Releasing

`legacy2mcp` is distributed to **PyPI** (`pip install legacy2mcp`) and the
**MCP Registry** (`io.github.OWASP/legacy2mcp`). Both are published by
[`.github/workflows/release.yml`](../.github/workflows/release.yml) when a
`v*` tag is pushed — no API tokens are stored anywhere:

- **PyPI** via [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC)
- **MCP Registry** via `mcp-publisher login github-oidc` (OIDC)

## One-time setup

1. **PyPI Trusted Publisher** — <https://pypi.org/manage/account/publishing/> →
   add a *pending publisher*:
   (for an existing project: *Manage project → Publishing → add a new publisher*)
   - PyPI project name: `legacy2mcp`
   - Owner: `OWASP` · Repository: `legacy2mcp`
   - Workflow: `release.yml` · Environment: `pypi`
2. **GitHub environment** — repo *Settings → Environments* → create `pypi`
   (optionally restrict its deployment branches to tags).
3. **MCP Registry** — nothing to do. The `io.github.OWASP/*` namespace is
   authorized for OIDC from this repo automatically. The registry verifies the
   PyPI package via the `<!-- mcp-name: io.github.OWASP/legacy2mcp -->`
   marker in [`README.md`](../README.md) (which becomes the PyPI description).

Releases up to 0.5.1 were published from `bvenkata/legacy2mcp` as
`io.github.bvenkata/legacy2mcp`. That registry entry is frozen; new releases go
to `io.github.OWASP/legacy2mcp`. Remove the old `bvenkata` trusted publisher
from PyPI once a release from `OWASP/legacy2mcp` has succeeded.

## Cutting a release

1. Bump the version in **three** places (they must all match):
   - `pyproject.toml` → `[project] version`
   - `server.json` → `version` **and** `packages[0].version`
2. Commit, then tag and push:

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

3. The workflow builds the package, publishes to PyPI, waits for it to go
   live, then publishes `server.json` to the MCP Registry.

## Verifying

```bash
pip index versions legacy2mcp
curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.OWASP/legacy2mcp"
```

## Publishing manually (fallback)

```bash
python -m build
twine upload dist/*                     # needs a PyPI token
mcp-publisher login github              # interactive device flow
mcp-publisher publish
```
