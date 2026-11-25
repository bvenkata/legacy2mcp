# Sample report: microblog

[`index.html`](index.html) is the unedited output of

```bash
legacy2mcp analyze ./microblog --no-ai
```

run against [miguelgrinberg/microblog](https://github.com/miguelgrinberg/microblog)
at commit `a975ef6` (the Flask app from *The Flask Mega-Tutorial*, MIT licence,
© 2017 Miguel Grinberg). It's a real application with authentication, email,
background jobs, full-text search and a third-party translation API, so the report
shows every section with real data. No AI was used, and nothing from the repo was
executed.

Open `index.html` directly in a browser; it works offline. `analysis.json` is
the same data in machine-readable form (`legacy2mcp report analysis.json`
regenerates the HTML).

Regenerate after changing the analyzer:

```bash
git clone --depth 1 https://github.com/miguelgrinberg/microblog /tmp/microblog
legacy2mcp analyze /tmp/microblog --no-ai --out /tmp/microblog-report
cp /tmp/microblog-report/report.html docs/sample-report/index.html
cp /tmp/microblog-report/analysis.json docs/sample-report/analysis.json
```
