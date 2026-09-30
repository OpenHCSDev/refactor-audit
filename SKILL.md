---
name: refactor-audit
description: Audit a codebase's structural debt by correct-maintenance (OpenHCS) standards and turn it into an aggressive, evidence-backed refactoring plan package that parallel agents can execute. Use this whenever the user wants to audit debt or slop, plan or coordinate a refactor, review a refactor's progress, compare a fork against its upstream, find legacy or compatibility code, measure whether new code is adding debt, write refactoring plans or per-surface documents for agents, or decide whether a codebase can be saved by refactoring, even if they never say "audit". Pairs with the nra-refactoring skill, which owns the per-decision ownership method; this skill owns measurement, surface mapping, plan packages and coordination.
---

# Refactor audit

Measure a codebase's structural debt, verify every finding in the code, map it into refactoring **surfaces**, and write a plan package that agents execute in parallel: binding rules, an index, shared abstractions, coordination, and one decision receipt per surface, written just in time.

The per-decision method (census, required relation, owners, new-case test) belongs to the **nra-refactoring** skill; read it when writing receipts. This skill covers everything around it.

**The pattern catalog (`references/patterns/`) is the heart of this skill.** It shows 43 antipatterns that agents produce by default, each as real slop beside its clean polymorphic form: what fact the code forces its reader to supply, which rung is broken (identity, membership, implementation), how families, capabilities composed by multiple inheritance, and derived single sources of truth collapse the duplication, and how many edits a new case needs before and after. Read its README before classifying findings, and cite pattern IDs in every receipt.

## Why the method looks like this

- **The codebase is the prompt.** Agents copy the code around them far more than they follow instructions. Two versions of one mechanism in the tree teach the next agent whichever has more call sites, usually the old one. So the plans delete aggressively and build exemplars; instructions alone never win against the majority of the tree.
- **Numbers lie in specific, repeatable ways.** A parser that can't read some files skips them silently. A file that "stopped churning" may have moved to another repo. A module named like a feature may be refactor output. Every surprising number is checked in the code before it is written down, and a wrong claim is corrected plainly the moment evidence contradicts it.
- **The owner's attention is the scarcest resource.** Plans batch every owner-level question with a default, run peer to peer with no coordinator agent, and report status in one line.

## Workflow

### 0. Orient

- Fetch and pin the heads you audit (`git fetch`, note the SHA and the PR number). Every document names its head, because `main` moves under plans quickly.
- List open PRs and the files they touch; they block or collide with surfaces.
- Check what CI exists. A repository with none gets a CI surface first.
- Find the project's Python (`requires-python`) and run every script with it: `uv python install 3.x`, then `uv run --python 3.x …` or the interpreter `uv python find 3.x` prints.
- For a fork, find its upstream and the merge-base; note whether upstream still moves.

### 1. Measure

Run from the target repository (scripts are in this skill's `scripts/`):

```
debt_census.py --repo . --root src/pkg                                  # snapshot
debt_census.py --repo . --root src/pkg --base <old-sha>                 # what changed since
debt_census.py --repo . --root src/pkg --upstream upstream/main --exclude tools/   # fork versus upstream
overlay.py     --repo . --root src/pkg [--upstream upstream/main]       # what NRA cannot see
attribute_origin.py --repo . --root src/pkg --since <old-sha> --foundation src/pkg/<abstraction>.py
merge_review.py --repo . --root src/pkg --since <old-sha>                # the debt each merged PR added
```

- **`debt_census.py`** reports densities per 1,000 code lines. The headline is `type(x) is` checks + boolean chains of four or more + string-keyed subscripts. `boolean_chain_terms` weighs each chain by its terms, so one eleven-term validity check counts eleven, not one. The overlay classifies the syntax of every term of every long chain and reports the dominant kind as an ownership lead, not a diagnosis. Predicate calls may already query the correct owner; inspect their declarations before choosing a target. Use `--exclude` for debugging scripts: they are not product code.
- **`overlay.py`** finds string dispatch, `isinstance` switches, raw record shapes (and whether an existing class already models them), hand-written exact key-set checks, attribute access by name, god classes and functions, repeated literal sets, legacy markers, dead modules, positional inserts, JavaScript embedded in strings, and child-process sites. With `--upstream`, each finding is tagged with its origin.
- **`merge_review.py`** measures every merged pull request since a revision, after minus before over the files it changed, and lists the merges that added the most debt and every merge that added dispatch. Use it for progress reviews and to find cleanup targets: a merge that added candidate debt is a cleanup plan's first witness. Parse failures name the merge and file, withhold rankings, and exit nonzero; never treat an incomplete review as zero debt.
- **`attribute_origin.py`** classifies new modules by the pull request that created them, which is the only reliable way to say who introduced debt and whether it predates the abstractions it should use.
- **Every script warns when files fail to parse.** Treat that warning as a stop: re-run with the right Python.
- The scripts are themselves written to the catalog's standard (measures, findings, checks and modes are families in `scripts/audit/`). When you extend them, add a class; never add a branch. `references/patterns/agent-defaults.md` (AGENT-8) shows what their first version looked like, and why tooling is not exempt.

Then run NRA's complete scan with the same Python (`python -m nominal_refactor_advisor src/pkg --json`) and confirm `complete: true` and zero omitted detectors. NRA keys on declared families, so a codebase that keeps its states in strings, booleans and `None` shows few findings; that is a blind spot, not health. The overlay covers it.

### 2. Verify before writing anything down

Read the code behind every number you intend to state. Real corrections this method has produced:

| The number said | The code showed |
|---|---|
| A god file dropped to 0% churn after a refactor | It moved to another repository through five paths; its churn followed it |
| A child is killed with SIGKILL only, so descendants leak | It runs as PID 1 in a PID namespace; the whole tree dies |
| Two compaction pipelines, so one is a copy | Different performers, authorities and records: two operations |
| Modules named per phase, so a lifecycle split by phase | Pipeline roles in sound layers; the lifecycle was already a state family |
| New modules are dense, so agents ignore the rules | 37 of 39 were written before the abstractions existed on `main` |
| A snippet looked unmerged work | It was in no branch or PR, so it was in an agent's unpushed worktree |

When a measurement surprises you, the next step is reading code, and when evidence contradicts something you already wrote, correct it in the document and say so.

### 3. Classify

Map every finding to a pattern in the catalog (the catalog's README maps overlay categories to patterns), and give it these labels before it becomes work:

- **External contract or ours.** Formats owned by others (a protocol spec, a library's API, a terminal standard, SQLite itself) are honored exactly. Formats both of whose ends you control change freely, in lockstep.
- **Live or dead.** Dead code is deleted, never refactored.
- **Legacy or innocent.** "Fallback" choosing the previous tab is innocent; a reader of an old format is legacy.
- **Durable or runtime state,** which decides whether a store is reset at cutover or carried across once.
- **Origin:** upstream or fork; refactor-created or feature-created.

### 4. Map surfaces

- **Organize by owned concept, not by origin or file,** and at boundaries by boundary: decode-once happens where data enters.
- **Run a crossing analysis:** which surfaces share files. Disjoint surfaces run in parallel; shared ones are sequenced, or the shared file gets one owner.
- **Extract a shared abstraction only when several surfaces need the same mechanism** (roughly three or more instances). Reject candidates the evidence does not support, and say why. Each shared abstraction has exactly one builder, a wave, and a list of users; users request extensions from the builder and never fork it.
- **Enforce polymorphism codebase-wide, not only per surface.** A surface's guards protect that refactor's result and nothing else. Use the census's `string_dispatch` and `type_switch` to count candidate subjects, and `string_dispatch_arms` and `type_switch_arms` to catch growth within an existing candidate. All four use a three-distinct-arm threshold per function. Verify ownership at each site: syntax cannot distinguish an external taxonomy from a missing family. These counts are screening ratchets, not complete no-new-case guards; subthreshold cases and additions offset by removals can escape them. Enforce admitted ownership decisions with site-specific guards. Seal mechanisms that must not be adapted (TIME-9).
- **Do not hide primitive switches in decorated methods.** `builtin_handler_type_switch` counts each dict/list/str/int/float/bool/tuple arm declared through the imported MroDispatch `handles` decorator, including a single arm on a method. It uses the same collector in the audit and receiving Core command. Only the canonical `field_codec.py` boundary is admitted; naming another module `*_codec.py` is not admission. Domain and Python AST classes are not primitive arms. Import aliases are read from the module AST; dynamic Python rebinding is outside this screening count's proof scope. PR421's real ACP failure source grows from zero to six such arms even though the older per-function type-switch counts do not catch that relocation.
- **Order:** CI and the ratchet first when missing; then abstraction builders; then surfaces in parallel waves; god-class decomposition last, since other surfaces shrink those classes first.

### 5. Write the package

Read `references/package-templates.md` and `references/rules-template.md`. A package holds:

- `README.md`: contents and reading order;
- `00-RULES.md`: the binding rules (from the template, with project specifics);
- `01-INDEX.md`: evidence, surfaces, order, crossings, decisions with defaults;
- `02-SHARED-ABSTRACTIONS.md` when two or more surfaces share mechanisms;
- `03-COORDINATION.md`: agents, status line, cutover, decisions, the prompt addendum;
- surface receipts, one per prompt, just in time.

### 6. Write surface receipts just in time

Read `references/surface-receipt.md`. For each surface, at dispatch time:

- **Re-measure at today's head.** If most of the surface already landed, write a short closure note instead of a full receipt, so no agent redoes finished work.
- Fill the receipt: boundary, witnesses with file:line and numbers, findings (each citing its pattern ID), required questions, ownership, target sketch (the pattern's clean form, adapted), new-case experiments, migration, guards, tests, done-when, dispatch.
- **Record every reassignment in the receiving surface's file,** not only in the one giving it away.
- Update the index, run `check_package.py DIR --zip OUT.zip`, and fix every broken link and every disagreement between the abstraction table and surface headers before presenting.

### 7. Review progress, and close the loop

Re-run the census and overlay at the new head against the last audited head, attribute changes by originating PR, and separate "the refactor worked where applied" from "new debt arrived elsewhere." Report both plainly, with production-code numbers.

Then close the loop. **Any violation class seen twice becomes a mechanical check in the same review:** a ratchet measure, a sealed mechanism, or a guard. Agents follow the structure around them and route around prose, so whatever stays prose keeps eroding. Before shipping a new measure, **prove it tracks the violation on real history**: run it at the heads where the violation grew, and confirm it grew too. Plausible measures often don't (see principle 14).

## The rules every plan carries

Summarized; the full text is in `references/rules-template.md`.

1. **No backwards compatibility.** One format per thing; no converters, aliases, re-exports, fallbacks or "for now" paths. External contracts are honored exactly, which is correctness, not compatibility.
2. **Hard cutover.** Runtime state is reset; durable state is carried across once by a tool outside `src/` that is deleted before the surface is done.
3. **Delete aggressively,** including dead code on sight; report lines deleted first.
4. **Finish the job.** Done means the guards pass with zero exceptions. Dual paths end with one path.
5. **Tests protect behaviour, not structure.** Delete tests of deleted code; one test per family; golden tests only for external contracts; never weaken an assertion. Busy work is fake work.
6. **Guards are the definition of done,** run by a fast required CI check.

## Writing the documents

- Plain and specific: numbers, file paths, line numbers, the head audited. State results at their actual strength; do not hedge real findings or inflate weak ones.
- Explain the reason for each rule and target; agents apply reasons to cases the text does not name.
- Imperative voice for instructions. No em dashes, no marketing cadence.
- Keep genuinely open questions only when the domain or the owner must answer; give every owner question a default so a one-word reply suffices.

## Tool regression checks

Run `python -m unittest discover -s <skill-directory>/tests -v` with the project's Python before changing the audit scripts. The tests cover dispatch growth and removal, valid owner predicates, and fail-closed merge reviews. Rebuild the `.skill` archive from the same source after they pass.

## Reference files

- `references/patterns/README.md`, then the file for each rung you meet: the catalog of antipatterns and their clean forms, with real code. **Read before classifying findings and before every receipt.**
- `references/rules-template.md`: the binding rules, ready to adapt. Read when writing `00-RULES.md`.
- `references/package-templates.md`: README, index, shared abstractions and coordination templates, including the status line, cutover procedure, decision batch and prompt addendum. Read when writing the package.
- `references/surface-receipt.md`: the receipt template, the closure-note variant, and a worked example. Read before every surface.
- `references/principles.md`: the lessons behind the method, with the evidence for each. Read when a situation is not covered above, or when a finding does not fit a category.
