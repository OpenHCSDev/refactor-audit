# Surface receipts

A surface receipt is the per-surface plan an agent executes. It follows the nra-refactoring skill's decision-receipt discipline (boundary, witnesses, required questions, owners, new-case test) and adds what parallel execution needs: guards, a test budget, a definition of done, and a dispatch message.

Write each receipt **at dispatch time, against the head of the day.** Re-measure first. If most of the surface already landed, write a closure note instead (section 3).

## Contents

1. Template
2. Guidance per section
3. Closure-note variant
4. Worked example (a terminal emulator)

---

## 1. Template

```markdown
# <ID>: <Surface>

**Head audited:** `<repo>` `main` at `<sha>` (#<pr>). **Rules:** [00-RULES.md](00-RULES.md). **Origin:** <upstream / fork / both>. **Step <n>.**
**Shared abstractions** (…). *Builds:* <…>. *Uses:* <…>.

## What is wrong
<One-sentence diagnosis in bold, then bullets with numbers, file paths, function names and line numbers.>

## Target
<Owned structure: families, capabilities, authorities. A short code sketch. Every deletion named explicitly.>

## Persisted state        (only if the surface changes a stored format)
<Classification of each store as runtime or durable, and what happens to each at cutover.>

## Guards
<AST or grep checks that make the old mechanism impossible to reintroduce.>

## Tests
<The few tests that protect behaviour; what gets deleted.>

## New-case experiments
<Today: the edits a new case needs, counted. After: the one declaration it needs.>

## Done when
<Observable, total conditions.>

## Dispatch
> **`<agent-name>`:** <goal and first message>
```

---

## 2. Guidance per section

**What is wrong.** Lead with the diagnosis in one sentence, then the evidence. Numbers beat adjectives: "a 223-line `match` over twelve command types" tells the agent what to find and how big it is. Separate what the code does from its origin (upstream or fork) only when it changes the plan.

**Target.** Name the owner of each fact. Use the shared abstractions the package defines instead of new mechanisms. Composition by multiple inheritance only where capabilities genuinely overlap and each capability replaces a hand-written roster; say what evidence justifies it. Names are derived from class names; an explicit override (`declared_name`) only for spellings an external standard dictates. Name every deletion: agents treat unnamed old code as something to keep.

**Persisted state.** Runtime state is reset. Durable state keeps its format when a derivation can reproduce it (for example, deriving stored keys from attribute paths so an existing preferences file loads unchanged); otherwise a one-shot tool outside `src/` carries it across and is then deleted.

**Guards.** Each guard is one mechanical check an agent can run. Guards scoped to the surface's files land with the surface; codebase-wide guards land when every adopter has merged.

**Tests.** One family-level test, one new-case test per abstraction, contract tests only for formats owned by others, and a performance gate on hot paths. List what gets deleted. Never ask for a golden file of our own format.

**New-case experiments.** Count today's edits for a realistic new case (a new kind, a new setting, a new command) and state the single declaration it needs after. This is the measure that separates factoring from relocation.

**Done when.** Total and observable. Never "most," never "follow-up."

**Dispatch.** A goal line and a first message: the rules file, what the agent builds and uses, the decisions that apply, its crossings, the one or two traps specific to this surface.

---

## 3. Closure-note variant

When re-measuring shows the surface largely landed:

```markdown
# <ID>: <Surface>

**Status:** mostly landed before this file was written. What is done, the small remainder, and where misattributed items belong.

## Already done
<Which PRs, what they built, verified at this head.>

## What remains
<Numbered items, each small and specific.>

## Reassigned
| Item | Evidence | Belongs to |
|---|---|---|
<Each item the index attributed here that is really another surface's. Record each one in the receiving surface's file too.>

## Done when / Dispatch
<Short; usually for the agent that built the landed part.>
```

---

## 4. Worked example

A terminal emulator where the families existed and their behaviour lived outside them. Condensed.

> **What is wrong.** This is a case of families that exist with their behaviour outside them.
> - Commands are twelve `NamedTuple` types joined only by a union alias; `TerminalState._handle_ansi_command` is a 223-line `match` applying each one from outside.
> - Reads are a polymorphic family; two of five already own `feed`, yet `StreamParser._feed` is an 85-line `isinstance` switch over all five.
> - `_parse_csi` compares mode strings seven times (`1000` … `1015`); `ANSIFeatures` is a bag of six optional booleans.
> The byte values and mode numbers are an external standard and stay exactly as they are; what moves is the behaviour attached to them.
>
> **Target.** Each command becomes a frozen, slotted dataclass under `ANSICommand` and owns `apply(state)`; the `match` and the union alias are deleted. Every read owns `feed`; the switch is deleted. Each terminal mode is a family member declaring its external number through `declared_name` and owning its effect.
>
> **Tests.** One table-driven contract test (escape sequences and the screen state each must produce), one new-case test, and a performance gate: the large-stream pilot must be no slower, because the emulator is on every agent's output path.
>
> **Done when.** The `match`, the switch and the mode comparisons are gone; every command, read and mode owns its behaviour; the contract test and the performance gate pass; the guards pass.
