# Package templates

Templates for the files of a plan package. Replace `<…>` placeholders; keep the structure, because agents and the checker (`scripts/check_package.py`) rely on it.

## Contents

1. README
2. Index
3. Shared abstractions
4. Coordination (status line, cutover, decisions, prompt addendum, dispatch)

---

## 1. README

```markdown
# <Project> refactor<, round N>

<One sentence: what these plans cover.> Written against `<repo>` `main` at `<sha>` (#<pr>).

**Put this directory in the repository at `docs/refactor/<name>/`,** so every agent's worktree has it.

## Read in this order

1. **[00-RULES.md](00-RULES.md): binding on every agent.**
2. **[01-INDEX.md](01-INDEX.md):** where things stand, the surfaces, order and crossings.
3. **[02-SHARED-ABSTRACTIONS.md](02-SHARED-ABSTRACTIONS.md):** mechanisms more than one surface uses, each defined once.
4. **[03-COORDINATION.md](03-COORDINATION.md):** who starts when, cutover, decisions, the prompt addendum.
5. **Surface files,** written one at a time, just before dispatch.

## What the owner does

<Answer the decisions without defaults; do the cutover installs; read the status channel. Nothing else.>
```

---

## 2. Index

```markdown
# <Project> refactor index

**Head:** `<sha>` (#<pr>). **Rules:** [00-RULES.md](00-RULES.md).

Surface files are written one at a time against the head of the day; re-verify yours before editing.

## Evidence

<A table of the measurements that matter, with a baseline column: upstream against fork, before against after, or refactor-created against feature-created. State which Python parsed the code.>

<Two or three sentences on where the debt comes from, attributed by origin.>

## Surfaces

| ID | Surface | Origin | Evidence | Target |
|---|---|---|---|---|
| <ID> | <owned concept> | <upstream / fork / both> | <numbers, files, functions> | <owned structure, in one or two sentences> |

## Order

| Step | In parallel | Why |
|---|---|---|
| 1 | <CI and ratchet if missing; abstraction builders; disjoint surfaces> | <reason> |

## Crossings

| Shared thing | Resolved by |
|---|---|

## Decisions

| ID | Question | Default |
|---|---|---|

## Surface files

<Written one per prompt, just in time: list, linked as each is written.>
```

---

## 3. Shared abstractions

One section per abstraction, plus a table the checker compares against every surface file's header. Keep the table's columns exactly: ID link, name, builder, wave, users.

```markdown
# Shared abstractions

**Read this before any surface file.** Each mechanism is defined once, here. A surface file says what it builds and uses and links back; it never re-specifies an abstraction. If a surface needs an extension, the builder makes it; nobody forks it.

| ID | Abstraction | Built by | Wave | Used by |
|---|---|---|---|---|
| [A1](#a1-name) | `Name` | <surface> | <wave> | <surfaces> |

## A1 Name

**What it is.** <one paragraph, plus a short signature sketch>
**Replaces:** <the concrete duplicates it removes, with counts>
**Used by:** <surfaces and what each uses it for>
**Built by:** <surface, wave; built as a new module first so it collides with nothing>

## Build order at a glance

<A short block showing which wave builds which abstraction. A surface may only use an abstraction whose builder has landed.>
```

Every surface file opens with a header line the checker reads:

```markdown
**Shared abstractions** ([02-SHARED-ABSTRACTIONS.md](02-SHARED-ABSTRACTIONS.md)). *Builds:* [A3](02-SHARED-ABSTRACTIONS.md#a3-name). *Uses:* [A1](…), [A2](…).
```

---

## 4. Coordination

```markdown
# Coordination

**Rules:** [00-RULES.md](00-RULES.md). Peer to peer: one agent per surface, each in its own worktree and thread, talking directly to the owner of anything it crosses. No coordinator agent: coordinators tend to impose holds nobody asked for.

## Agents and when they start

| Agent | Surface | Starts |
|---|---|---|

## Status

One line in `#refactor`, only when state changes (started, PR open, guards changed, merged, blocked). Deletions first, because deleting is the point:

    S12 · PR #231 open · guards 3/5 · lines −1,240 +310 · tests −48 +3 · next: <item>

## Cutover

Persisted formats change, so the new version is installed at a few cutover points, one per completed step:

1. Quiesce: nothing in flight; stop long-running processes.
2. Install the pinned stack, all components together.
3. Run each pending tool in `tools/cutover/` once.
4. Reset the runtime stores the step's merged surfaces declared.
5. Restart.
6. Delete the tools that ran, in the next PR.

## Decisions

| ID | Decision | Default |
|---|---|---|

Every question gets a default, so "accept defaults" is a complete answer.

## The prompt addendum

Append to the system prompt of every surface agent, with `{{SURFACE}}` filled in:

> **Your assignment: refactoring surface `{{SURFACE}}`.** Read, in order: `00-RULES.md`, `01-INDEX.md`, `02-SHARED-ABSTRACTIONS.md`, then your surface file. The rules override everything else, including your own instinct to be cautious.
>
> In your surface's files, refactoring is your task. **Delete** legacy code, compatibility code, converters, dead code and tests of deleted code. **Never** add a fallback, an alias, a converter or a "for now" path. **Finish completely:** your surface is done when its guards pass with zero exceptions.
>
> Re-verify your surface file against current `main` first; where it is wrong, correct it in your PR and say what changed.
>
> Tests protect behaviour, not structure. Delete tests of what you deleted; replace structural tests only where a real behaviour needs protecting, one test per family. No golden files for our own formats. Never weaken an assertion.
>
> Report lines and tests deleted and added. Post a status line only when your state changes.
```

Each surface file ends with its own dispatch message: a goal line ("Complete surface X per `docs/refactor/…`. Done when …") and a first message naming the rules file, what the agent builds and uses, the decisions that apply, and its crossings.
