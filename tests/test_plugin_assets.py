"""Repo-structure invariants for the shipped plugin assets."""

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def test_the_claude_and_codex_skill_copies_stay_identical():
    # The summarize skill ships twice -- root skills/ (the Claude Code plugin is
    # embedded at the repo root) and plugins/tubeless/skills/ (Codex requires the
    # plugins/<name>/ subdir layout) -- because the two ecosystems resolve a plugin
    # differently. Lock the two byte-identical so an edit to one that forgets the
    # other fails here instead of silently diverging the two plugins' behavior.
    claude = (_REPO / "skills" / "summarize" / "SKILL.md").read_text(encoding="utf-8")
    codex  = (_REPO / "plugins" / "tubeless" / "skills" / "summarize" / "SKILL.md").read_text(encoding="utf-8")

    assert claude == codex, "the Claude and Codex summarize SKILL.md copies have drifted; re-sync them"
