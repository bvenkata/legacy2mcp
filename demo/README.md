# Demo

A ~25-second terminal demo: a WSDL in, typed + validated MCP tools out.

## Record it (GIF + MP4)

Uses [VHS](https://github.com/charmbracelet/vhs) — a `.tape` file is a
deterministic script, so the recording is reproducible and re-runnable.

```bash
brew install vhs          # one-time (pulls in ttyd + ffmpeg)

# from the repo root, with the package importable
pip install -e ".[dev]"   # or rely on this repo's ./.venv
vhs demo/demo.tape
```

Outputs:

- `demo/legacy2mcp.gif` — drop into the README
- `demo/legacy2mcp.mp4` — for a PR description, social, or a docs site

Tweak pacing/size/theme at the top of [`demo.tape`](demo.tape)
(`Set TypingSpeed`, `Set Width/Height`, `Set Theme`).

## Just run the demo (no recording)

```bash
./demo/demo.sh
```

Starts the bundled mock SOAP service, runs the same commands, cleans up.

## Alternative: asciinema (web player, tiny files)

```bash
brew install asciinema
asciinema rec demo/legacy2mcp.cast -c './demo/demo.sh'
asciinema upload demo/legacy2mcp.cast     # or embed the .cast with asciinema-player
```

## In the README

The root README embeds `demo/legacy2mcp.gif` near the top. Re-running
`vhs demo/demo.tape` overwrites it in place, so refreshing the clip is just
regenerate + commit.
