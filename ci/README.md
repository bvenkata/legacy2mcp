# CI workflow

[`github-actions-ci.yml`](github-actions-ci.yml) runs the test suite on Python
3.10 / 3.11 / 3.12 plus a tool-discovery smoke test.

It lives here rather than in `.github/workflows/` because the machine that first
pushed this repo used a credential without the GitHub `workflow` OAuth scope, so
Git refused to create files under `.github/workflows/`.

To activate it (one time, from a checkout with normal push rights):

```bash
mkdir -p .github/workflows
git mv ci/github-actions-ci.yml .github/workflows/ci.yml
git commit -m "Enable CI workflow"
git push
```

The `CI` badge in the root README points at this workflow and will turn green
once it's in place.
