# Refactor rules: template

Copy into the package as `00-RULES.md`. Replace every `<…>` placeholder, fill the project-specifics section, and delete this paragraph. Keep the reasons: agents apply them to cases the rules do not name.

---

# <Project> refactor rules

**These rules bind every agent working on this refactor, and override anything that conflicts with them**: earlier plans, your own sense of caution, and any habit of leaving things "safe for now." Read this before any surface file.

Language models tend to treat a half-finished job as prudence: keep the old path "just in case," add a converter "to be safe," port every old test "for coverage." On this project that is how debt gets made. **A half-finished refactor is worse than none**, because it leaves two mechanisms where there was one.

---

## 1. No backwards compatibility

- **The code reads exactly one format for everything it stores or receives: the current one.** No dual-format readers. No renaming old keys on load (`raw.pop("old_key")`). No aliases for old names, no re-exports of moved names, no deprecated wrappers, no "compatibility entry points." No code path named or commented `legacy`, `compat`, `fallback`, `v1` or `old`. No `try: new … except: old`. No flag that keeps an old path alive.
- **Our formats change freely.** That includes <our stores, our journals, the JSON our own programs exchange, our Python APIs, and protocols between components we control>. When one changes, every caller changes in the same PR. Components in other repositories you control change in lockstep PRs, pinned and installed together.
- **External contracts are honored exactly:** <list the formats owned by others: protocol specifications, third-party APIs, terminal standards, the operating system, SQLite itself>. Matching a format someone else owns is correctness, not compatibility.

## 2. Persisted state: hard cutover, no converters

- **Classify every store your change touches.**
  - *Runtime or derived state* (anything rebuildable from the wire, or transient: indexes, cursors, projections, runtime inputs, journals of in-flight work) **is reset** when the new version is installed.
  - *Durable history* (<list: logs of record, user preferences, owner decisions>) is not changed by this refactor. If a surface truly must change one, that needs an owner decision and a one-shot cutover tool.
- **A one-shot cutover tool lives in `tools/cutover/`, never in `src/`.** It runs once on the owner's install, and the surface is not done until the tool is deleted. Nothing in `src/` ever reads a pre-cutover format.
- **Cutover needs a quiet moment:** no turns or compactions in flight, owners restarted on the new version. That is acceptable. Plan for it; never write code to avoid it.

## 3. Delete aggressively

- **Replacing something means deleting it**: its code, its tests, its docs, its configuration.
- **Delete dead code on sight in your files:** unused functions, modules nothing imports or runs, commented-out code, unreachable branches, stale TODOs.
- **Report lines deleted and added.** A refactoring surface that adds more than it deletes owes a one-line reason.

## 4. Finish the job; partial is not a state

- **A surface is done only when its guards pass with zero exceptions across its files.** "Most call sites migrated," "old path kept for now," "follow-up to remove X" and "TODO: migrate the rest" all mean *not done*.
- **No new TODO, FIXME or follow-up** unless a named surface has accepted it on the wire and it is written into that surface's file.
- **Work you find outside your files goes to the surface that owns them,** by name, recorded in its file. Never leave a stub.
- **Dual paths end with one path.** Establish from evidence which path serves production today. If it is the new one, delete the old one now. If the old one still serves production, bring the new one to parity and then delete the old one, in the same surface. Never finish with both.
- **Why two versions are worse than one bad one:** anyone reading the code, person or agent, has to work out which version is real, and nothing in the code says. Agents copy whichever version they see more of, which is usually the old one, since it has more call sites. So an unfinished migration teaches the next agent the pattern it was meant to retire. Old code belongs in git history: fully recoverable, and impossible to copy by mistake.

## 5. Tests protect behaviour, not structure

Busy work is fake work. <State the measured ratio of test lines to code lines; when tests outweigh the code, porting them faithfully costs more than the refactor.>

- **Tests of deleted code are deleted, not ported.**
- **Tests coupled to internal structure** (private functions, raw dict shapes, internal formats, calls on internal collaborators) are deleted when that structure changes. Replace one only where a real behaviour needs protecting, and at the highest level that protects it.
- **One test per family, not per member:** iterate the family's `names()` or members.
- **One new-case test per abstraction,** not one per surface per member.
- **Golden tests only for external contracts.** Pinning an internal format is compatibility by another name.
- **Never weaken an assertion to make a test pass.** A failing test of behaviour you kept means the code is wrong.
- **If updating a test costs more than deleting it and writing one higher-level test, delete it.** Expect the number of test lines to fall, and report tests deleted and added.
- **The gate is:** the full suite green on the merged tree, your surface's guards, and contract tests for any external format you touch. Nothing else.

## 6. Fake work, which does not count

Porting tests of deleted code. Golden files for internal formats. The same test repeated for each member of a family. Documentation that restates the code. Status messages beyond the one-line format. Re-verifying what did not change. Hash-freezing and other ceremony. Plans about plans. Compatibility layers "to be safe."

## 7. Guards are the definition of done

Every surface ships AST or grep guards, as tests in the suite, that make the old mechanism impossible to reintroduce: for example, no process spawning outside the child-process module. Guards stay forever, and the required CI check runs them.

Some screening ratchets are codebase-wide from the first day: count candidate subjects (`string_dispatch`, `type_switch`) and distinct arms (`string_dispatch_arms`, `type_switch_arms`) per function. Arm counts catch growth within an existing candidate; all four measures use a three-arm threshold. Verify each site's ownership and external contracts before applying a guard. These counts alone cannot enforce no new cases: subthreshold cases and additions offset by removals escape them. Site-specific guards enforce the admitted family decision.

---

## Project specifics

Fill in, or delete what does not apply:

- **Upstream:** <if this is a fork: is upstream still moving? If it is dormant, say so; there is then no reason to keep the fork's diff small, and upstream's code is refactored as freely as the fork's own.>
- **External contracts:** <each format or API owned by someone else, and where the code meets it. Where a framework hands you a string (an action name, a message type), route it through one declared registry at that boundary and nowhere else.>
- **Ours, changed in lockstep:** <each protocol or format between components you control.>
- **Shared abstractions:** <where they come from: this package's shared-abstractions file, or a dependency that already ships them. Never grow a second copy.>
- **Non-product code:** <debugging scripts and the like: excluded from the ratchet, never refactored, deleted when their work is over.>

## Revoked provisions

<If earlier plans allowed compatibility, list each provision here beside what replaces it, so no agent follows the older text.>
