from pathlib import Path

old = 'if ! docker inspect -f "{{.State.Running}}" "$NAME" 2>/dev/null | grep -qi true; then'
new = 'if ! docker ps --format "{{.Names}}" | grep -qx "$NAME"; then'
for path in ("tools/bench/ticket_probe.sh", "tools/bench/eviction_probe.sh"):
    p = Path(path)
    t = p.read_text(encoding="utf-8")
    if old not in t:
        raise SystemExit(f"{path}: running-check missing:\n" + "\n".join(l for l in t.splitlines() if "running" in l.lower())[:500])
    # Avoid go-templates entirely in the running check.
    new2 = 'if ! docker ps --format "{{.Names}}" 2>/dev/null | grep -qx "$NAME"; then'
    # Wait - {{.Names}} is also a go-template. Use plain docker ps names.
    new2 = 'if ! docker ps --format "{{.Names}}" | grep -qx "$NAME"; then'
    new2 = 'if [ -z "$(docker ps -q --filter "name=^/${NAME}$")" ] && [ -z "$(docker ps -q --filter "name=^${NAME}$")" ]; then'
    # Simplest: docker inspect Status via powershell too
    new2 = '''running="$(powershell.exe -NoProfile -Command "(docker inspect '$NAME' | ConvertFrom-Json).State.Status" | tr -d '\\r\\n')"
if [ "$running" != "running" ]; then'''
    p.write_text(t.replace(old, new2, 1), encoding="utf-8", newline="\n")
    print(path, "ok")
