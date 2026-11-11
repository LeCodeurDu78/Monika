# Updating Monika

Monika never updates itself. The command only reads the local commit and the remote one, then
prints what to do:

```bash
python -m monika --update
```

- **Up to date** → exit code `0`.
- **Update available** → exit code `2`, with the `git pull --ff-only` and `pip install` lines.
- **Offline / no remote** → exit code `1`, with the reason. Nothing is changed.

The status compares the checkout with the branch's upstream (`git rev-list`, local), falling back
to `git ls-remote origin HEAD` when no upstream is configured, so it works without fetching into
your checkout. Local commits that are not pushed count as nothing to pull. Local modifications are
reported (`git status --porcelain`): commit or stash them before pulling, otherwise `git pull` may
refuse or mix your edits with upstream.

## Applying an update

```bash
# from the project folder
git pull --ff-only
.venv/bin/pip install -r requirements.txt     # dependencies may have changed
.venv/bin/python -m monika --config           # check the merged settings
.venv/bin/python -m monika
```

On Windows use `.venv\Scripts\pip.exe` and `.venv\Scripts\python.exe`.

Back up `~/.config/monika` (or `MONIKA_HOME`) first if you care about the journal, sessions and
memory. The document index can be rebuilt with `--rag-index`; the voice pack is yours to keep.

## If the pull refuses

```bash
git status                 # see what is modified
git stash                  # keep your edits aside
git pull --ff-only
git stash pop              # reapply them, resolve conflicts if any
```

If a value was removed or renamed, `python -m monika --config` and the startup message name the
setting; the settings page also refuses an out-of-range value before writing it.
