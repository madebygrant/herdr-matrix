# herdr-matrix

A Herdr plugin that renames agents after the Agents in The Matrix, gives them Matrix status wording, and runs digital rain when everything is quiet.

## What it does

- Renames each new agent: smith, brown, jones, johnson, jackson, thompson, gray. Then generic surnames (williams, white, adams and so on), then `smith-2`, `smith-3`. Agents you already named keep their names, and a name is free again when its agent closes. Herdr only accepts lowercase names.
- Sets status labels: working is "jacked in", idle is "standing by", blocked is "awaiting operator", done is "exited". Show them with the `state_text` sidebar token.
- Opens a rain overlay after every agent has been "standing by" for 10 minutes. Any key dismisses it, and any status change resets the countdown. It never opens a second overlay.
- Shows a toast, "Agent smith has been deleted.", when an agent's pane goes away. If several close together, one toast lists them all. Toasts need `delivery = "herdr"` in the config below, or Herdr drops them.
- Hides the focused agent from the agents list with `madebygrant.herdr-matrix.toggle-hide`, and brings it back when you run it again on that pane. `madebygrant.herdr-matrix.show-all` unhides everyone. The agent keeps running in its tab. Only the sidebar row goes.
- Prints "N agents in the Matrix" for the tab bar with `tab_bar.py`.

## Install

```sh
herdr plugin install madebygrant/herdr-matrix
```

Or link a local checkout:

```sh
herdr plugin link /path/to/herdr-matrix
```

It needs Herdr 0.8.2 or newer and `python3`. Run `link` again after editing `herdr-plugin.toml`. Edits to the Python files apply on the next event.

## Config

The plugin covers names, labels, rain and the toast text. The window title, tab bar, sidebar rows, keys and toast delivery live in `~/.config/herdr/config.toml`:

```toml
[ui]
window_title = "THE MATRIX · {workspace}"
tab_bar_right = [
  { type = "command", command = "python3 -I /path/to/herdr-matrix/tab_bar.py" },
]

[ui.toast]
delivery = "herdr"

[ui.toast.herdr]
position = "top-right"

[ui.sidebar.agents]
rows = [["state_icon", { token = "agent", fg = "#86BC9E", bold = true, dim = false }, { token = "state_text", dim = true }], [{ token = "workspace", bold = false }, "tab"]]

[[keys.command]]
key = "prefix+m"
type = "plugin_action"
command = "madebygrant.herdr-matrix.toggle-hide"
description = "matrix: hide or show agent"

[[keys.command]]
key = "prefix+shift+m"
type = "plugin_action"
command = "madebygrant.herdr-matrix.show-all"
description = "matrix: show all agents"
```

Set the `tab_bar.py` path to where the plugin lives, then run `herdr server reload-config`.

## Tuning

All three are constants at the top of `matrix.py`: `IDLE_SECONDS` (600), `CANON` and `SURNAMES` for name order, and `LABELS` for status wording.

## Trying it

```sh
herdr plugin pane open --plugin madebygrant.herdr-matrix --entrypoint rain --placement overlay --focus
herdr plugin log list --plugin madebygrant.herdr-matrix
```

The first command opens the rain now. The second shows recent hook runs and their errors. State (remembered names, idle counter, waiter pid) is in `~/.local/state/herdr/plugins/madebygrant.herdr-matrix/`.

## Limits

- The rain overlay takes focus, so a key you press as it opens is lost.
- The plugin only names agents when Herdr detects them. To name one that was already running, run the detect hook by hand from the plugin directory:

  ```sh
  HERDR_PLUGIN_STATE_DIR=~/.local/state/herdr/plugins/madebygrant.herdr-matrix \
  HERDR_PLUGIN_EVENT=pane.agent_detected HERDR_PANE_ID=<pane> python3 -I matrix.py
  ```

- The kill message needs the plugin to have recorded the agent's name first, so agents that predate the plugin get none until a status event fires.
- Herdr shows one toast at a time. A second one returns `busy`, so the plugin retries the kill message up to four times, three seconds apart, and may still drop it.
- Herdr decides how often the tab bar command runs.

## Releasing

```sh
scripts/release.sh patch   # or minor, major
scripts/release.sh current # tag the version already in herdr-plugin.toml
```

Add `--dry-run` to print the steps without running them. The script needs a clean `main` that matches `origin/main`. It bumps `version` in `herdr-plugin.toml`, commits it, tags `vX.Y.Z`, pushes both, and creates a GitHub release with generated notes if `gh` is installed. Users can pin a release with `herdr plugin install --ref vX.Y.Z madebygrant/herdr-matrix`.

## License

MIT. See `LICENSE`.
