# VHS script for the `analyze` demo: `vhs demo/analyze.tape` -> demo/analyze.gif
# Needs: vhs (https://github.com/charmbracelet/vhs), legacy2mcp[analyze], git.

Output demo/analyze.gif
Set Shell "bash"
Set FontSize 18
Set Width 1300
Set Height 760
Set Theme "Catppuccin Latte"
Set TypingSpeed 40ms

Hide
Type "rm -rf /tmp/microblog /tmp/microblog-report && git clone -q --depth 1 https://github.com/miguelgrinberg/microblog /tmp/microblog && clear"
Enter
Sleep 4s
Show

Type "# Map a real Flask app: flows, risk, tools -- locally, no API key"
Enter
Sleep 800ms
Type "legacy2mcp analyze /tmp/microblog --no-ai --out /tmp/microblog-report"
Enter
Sleep 4s

Type "# Expose only the read-only tools, writes stay hidden"
Enter
Sleep 800ms
Type "legacy2mcp generate /tmp/microblog-report --base-url http://localhost:5000 --out /tmp/microblog-report/config.yaml | head -24"
Enter
Sleep 5s

Type "legacy2mcp inspect --config /tmp/microblog-report/config.yaml | grep '\"name\"' | head -8"
Enter
Sleep 4s
