# Principles, with the evidence behind them

Each principle came from a measurement or a correction in a real audit of an agent-built codebase (agent-comms) and a fork of a Textual app (Toad). Read this when a situation is not covered by the workflow, or when a finding does not fit a category.

## Contents

1. The codebase is the prompt
2. Two versions are worse than one bad one
3. Detection is local; "locally reasonable" means nothing
4. Numbers need the code read behind them
5. Attribute by origin, never by name or by guess
6. Organize by owned concept, and at boundaries by boundary
7. Abstractions need several instances; reject weak candidates
8. External contracts versus ours
9. Tests protect behaviour; busy work is fake work
10. The owner's attention is the scarcest resource
11. Plans go stale; write them just in time
12. Two copies of a fact drift, in plans too
13. A ratchet must allow the refactor it protects
14. Mechanize every recurring violation, and prove the measure first
15. Enforce polymorphism by ratcheting its alternatives

---

**1. The codebase is the prompt.** Agents copy the code around them far more than they follow instructions. In agent-comms, feature code written before shared abstractions existed ran at over 90 smells per 1,000 lines; after the abstractions landed, new modules came in clean, and agents built new state families on the lifecycle base without being told. In the Toad fork, agents doubled upstream's largest classes (`Conversation` 1,686 to 3,042 lines), extending whatever was already biggest, and imported agent-comms' habits (`type()` re-validation, long chains) into a codebase that had none. Consequence: delete old versions, build exemplars, and name canonical examples; instructions alone lose to the majority of the tree.

**2. Two versions are worse than one bad one.** A reader, person or agent, has to work out which one is real, and nothing in the code says. Agents resolve it by copying the version with more call sites, usually the old one, so an unfinished migration teaches the pattern it was meant to retire. Old code belongs in git history: recoverable, and impossible to copy by mistake. This is why the rules forbid every compatibility path.

**3. Detection is local; "locally reasonable" means nothing.** Nine lines of UI code held four problems (one concept encoded three ways, a variable named for one question holding another's answer, a view reconstructing states, a headline cut from prose), all visible without the rest of the codebase. Reasonableness is relative to requirements; judging "locally" strips the requirements and leaves only familiarity, which is the copy-the-context effect again. Global context matters for designing the fix, never for seeing the problem.

**4. Numbers need the code read behind them.** A file's churn "dropping to zero" was a move to another repository. A parser for the wrong Python version silently skipped 9% of a fork and subtracted upstream's counts from modified files. A SIGKILL-only child looked leaky and was contained by a PID namespace. Each would have become a wrong instruction to agents. The scripts warn on parse failures; everything else needs a human-style read.

**5. Attribute by origin, never by name or by guess.** Classifying modules by name called refactor output (`turn_runner.py`, `read_basis.py`) "features." A claim that agents were ignoring the rules was wrong: 37 of 39 dense modules merged before the abstractions existed on `main`. Classify by the pull request that created each module, and compare merge times on the first-parent line of `main`, which is when code actually became available to copy.

**6. Organize by owned concept, and at boundaries by boundary.** Surfaces defined by origin or by file split one concept across agents. Decode-once happens where data enters, so the boundary is the natural unit there: raw record reads in agent-comms clustered at SQLite tables, helper-program outputs and protocol payloads, and each became one surface.

**7. Abstractions need several instances; reject weak candidates.** Child-process supervision implemented eleven ways earned a shared abstraction; a suspected append-only journal pattern had one real instance (the wire itself) and was rejected in writing. A helper-program runner used in six places earned a name as a composition of two existing abstractions. Say why a candidate was rejected, so nobody reinvents it.

**8. External contracts versus ours.** Formats owned by others (a protocol specification, a terminal standard, a library's parameter names, SQLite) are honored exactly; that is correctness. Formats whose ends you both control change in lockstep, with no compatibility. The same word can mean either: "capability negotiation" between two of your own components is compatibility machinery, since pinning them together makes skew impossible; a persistent worker refusing a mismatched build is correct, because it rejects rather than adapts.

**9. Tests protect behaviour; busy work is fake work.** Test suites outweighed their code in both audits (70,000 lines against 58,000; 32,000 against 16,000). Porting them faithfully would cost more than the refactor. In the Toad fork, 228 "tests" were hand-run scripts; one collector made them a suite without editing any of them. Golden files belong only to external contracts; pinning your own format is compatibility by another name.

**10. The owner's attention is the scarcest resource.** Agents running around the clock with every hold and unrecorded decision waiting on the owner pulled the owner into working until 4 or 5 a.m. Plans therefore batch every owner question with a default, keep durable decisions in one file quoting the owner's words, run peer to peer with no coordinator (coordinators kept imposing holds), and report status as one line per state change.

**11. Plans go stale; write them just in time.** In one round, three of five surfaces were partly or mostly done before their files were written, and a head moved forty PRs in a day. Receipts written at dispatch time, re-verified by the executing agent, avoid sending agents to redo finished work.

**12. Two copies of a fact drift, in plans too.** The shared-abstractions table and the surface headers both said who uses what; they disagreed in four places. Reassignments recorded only in the giving file got lost. Record each fact once, link to it, and let `check_package.py` catch the rest.

**13. A ratchet must allow the refactor it protects.** agent-comms' class-size ratchet rejected any growth of any class, so it rejected a change that moved two functions onto the class that owned their data (+25 lines to `ClaimTransition`), which is the move the catalog prescribes everywhere. The intent was to stop god classes growing (AGENT-4), so the measure became lines beyond the god-class threshold: small owners absorb behaviour freely, no class crosses 500 lines, no class past it grows. Test every ratchet against a known-good refactor as well as a known-bad change; a ratchet that only has failing examples will be tuned into blocking the cure.

**14. Mechanize every recurring violation, and prove the measure first.** In one day across two agent-built repositories, agents respected the style wherever structure enforced it (debt density fell 19% and 38%, legacy code was deleted rather than renamed) and broke it wherever the rule was only written down (a revived compatibility adapter, an eleven-term validity chain, two codec forks, 75 new `None` checks). Each violation class stopped once it had a mechanical check. But measures need proving: to catch the `None` growth, a count of optional fields and then a count of optional attributes both fell while the checks rose. Only counting probes of *another object's* absent state tracked it (+81 against +75). Run a candidate measure at the heads where the violation grew before trusting it.

**15. Enforce polymorphism by ratcheting its alternatives.** The plans for two agent-built repositories prescribed polymorphic targets surface by surface, each with guards for its own result, and enforced several antipatterns codebase-wide. They never ratcheted string dispatch or type switches, the direct alternatives to polymorphism. Across two days and 210 merges, agents mostly chose families anyway, but six merges added dispatch, and one feature merge added two string dispatches with nineteen string-keyed reads. Count candidate subjects per function (a subject compared against three or more literals; three or more type checks on one subject), and their distinct arms, so growth within an existing candidate is visible too. Prove both measures on known-bad changes and known-good refactors. Syntax does not establish ownership: inspect whether each taxonomy is external or a missing family. Counts are screening ratchets, not complete enforcement; subthreshold cases and additions offset by removals escape them. Once ownership is admitted, site-specific guards make the prohibited dispatch impossible to reintroduce.
