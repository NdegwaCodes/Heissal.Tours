# Quick Setup: Replicating This Architecture

Source reference for how this repo's Claude context is organised — the essential 20%. Kept
here so the graduation triggers at the bottom are checkable later.

## The core idea

Everything here exists to solve one problem: **keep Claude's context small and correct across
many sessions**, instead of re-explaining the project every time or letting one giant prompt
file grow without bound. Six pieces do that job:

| Piece | Problem it solves |
|---|---|
| `CLAUDE.md` | One routing file, not scattered tribal knowledge |
| `rules/*.md` | Split CLAUDE.md by concern; load only what's relevant |
| Memory (`~/.claude/.../memory/`) | Facts persist across sessions without re-explaining |
| Contracts | Agent-to-agent handoffs are typed, not re-parsed prose |
| Skills/agents | Task instructions load on demand, not always-in-context |
| Hooks | Repetitive reminders run as code, not as prompt text |

## The minimal kit (what this repo has)

```
CLAUDE.md              routing + architecture, kept under ~200 lines
rules/
  shell-gotchas.md     machine/stack quirks that cost a debugging session once
.claude/
  agents/              add one agent only when a task repeats 3+ times
docs/
  decision-log.md      why, not what — git history already has "what"
```

Do **not** start with `contracts/`, `skills/`, `intellectual-foundation/`, or a multi-stage
pipeline (`extract-requirements -> generate-plan -> verify-plan`). Those earn their keep only
after specific pain (drift between agents, plans that looked right but weren't).

## Token-efficiency rules adopted immediately

1. **Retrieve, don't recall.** Any fact that can change (an ID, a status, today's date) gets
   looked up at run time, never hardcoded into a prompt or CLAUDE.md. A wrong recalled fact
   is worse than a missing one.
2. **Single source of truth.** If a fact lives in two files, one of them is already wrong.
   Write a pointer, not a copy.
3. **Fix reliability structurally, not with prose.** If the model keeps getting X wrong, the
   fix is a lookup helper, a lint, or a fail-safe default — not another sentence telling it
   not to.
4. **Generated indexes over full documents.** For a large reference corpus, generate a small
   digest that routes to the one relevant deep section.
5. **Structural over prose.** A markdown table or a schema is cheaper to read and harder to
   misparse than the equivalent paragraph.

## Setup checklist

1. Write `CLAUDE.md`: what the project is, key directories, one routing rule per task type.
   Stop at ~100 lines to start.
2. Add one `rules/` file for anything already explained twice. Don't pre-write rules for
   problems not yet hit.
3. Turn memory on and let it accumulate — don't seed it manually.
4. Add hooks only for things genuinely repetitive and script-checkable (uncommitted work at
   session end, a stale-cache warning) — not as a place to restate CLAUDE.md.
5. Add the first agent/skill only after doing the same multi-step task by hand 3+ times.

## When to graduate

Add heavier machinery once you observe: multiple agents disagreeing about a handoff format
(-> contracts), plans that pass review but miss the actual ask (-> a verify-plan step in a
fresh context), or a reference corpus too big to load whole (-> digest generation). Each
piece should answer one observed failure, never a speculative one.
