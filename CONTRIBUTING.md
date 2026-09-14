# Contributing

SketchNarrator is distributed as one Codex Skill with an internal renderer. Keep `SKILL.md` as the only user-facing Skill entry and do not split `renderer/` into a second Skill.

Before opening a change:

1. Use a supported Python 3.10+ interpreter and install the core project dependencies.
2. Run `python -m unittest discover -s renderer/tests -p "test*.py"`.
3. Run `python -m unittest discover -s tests -p "test*.py"`.
4. Run the UTF-8 Skill validator when it is available in your Codex installation.
5. Regenerate `release-manifest.json`, then verify it with `python scripts/update_release_manifest.py --check`.
6. Build the public archive with `python scripts/package_release.py`; it must contain only the manifest set plus `release-manifest.json` itself.
7. Before the first Git commit, record the POSIX launcher mode with `git update-index --chmod=+x scripts/run.sh`; CI rejects a checkout where `scripts/run.sh` is not executable.

Do not commit virtual environments, downloaded models, FFmpeg binaries, user projects, credentials, private presenter packs, or rendered media. Default SFX under `assets/sfx/classic-light/` are the only public WAV exception.

Compatibility CI covers the minimum Python and the latest stable Python (`3.x`) on each OS. Passing one matrix does not promise compatibility with future releases. Record reproducible failures before introducing targeted dependency exclusions.
