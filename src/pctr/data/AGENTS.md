<!-- pctr -->
## Desktop automation (pctr)

`pctr` is installed. Drive Windows GUI apps by **element name**, not pixel
coordinates:

```bash
pctr windows                          # list windows
pctr tree  --title "App" --limit 100  # inspect controls
pctr click --title "App" --name "Save"
pctr set   --title "App" --name "Search" --text "hello"   # prefer set for fields
pctr ocrfind --text "Sign in"         # fallback: read text off the screen (single words)
pctr look --for "a red error icon"    # fallback: open-vocab object detection
```

- Prefer `set` (ValuePattern) over `type` for fields - `type` interprets pywinauto key syntax (`{ } + ^ % ~ ( )`).
- Global `type`/`keys` need `pctr focus` first. Prefer `pctr hotkey --keys ctrl+s` over `keys "^s"`.
- `click`/`set`/`wait` accept any selector - `--name` is not required.
- Exit codes: `0` ok, `1` no match, `2` usage error (clean `error:` line, no tracebacks).
- Games ignore synthetic input: add `--direct` to mouse/key commands.
- Electron apps expose only the frame - use `focus` + global keys, or CDP.
- Virtual desktops: `pctr desktop list/windows/where/on-current` (read-only), `next/prev/new/close` (switches the active desktop), `move-window --to N`. UIA actions (`invoke`/`set`) reach windows on other desktops without switching; raw mouse/keys do not. `move-window` is blocked on Win11.
- `--json` (global or per-command) gives machine-readable output for `windows/tree/find/ocr/ocrfind/look/desktop`; progress goes to stderr so stdout stays clean.
- `--file FILE` runs a batch of commands (one per line, `#` comments, stops on first error).
- `pctr ocr --lang de-DE` picks the OCR language (BCP-47).

Run `pctr -h` for all commands. Full skill: `pctr skill`.
MCP: `pip install "pctr[mcp]"`, then `pctr mcp start` (background http) or register `pctr-mcp` as a stdio server. Manage with `pctr mcp start|stop|restart|status|help`.
