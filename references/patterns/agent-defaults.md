# Agent defaults: the behaviours that produce everything else

The patterns in the other files are what the code looks like. These are why agents write it. Each is a sensible-sounding default that, on a codebase like this, manufactures debt.

## Contents

AGENT-1 Keeping old code "for safety" · AGENT-2 Stopping when the new path works · AGENT-3 Porting every test, and pinning our own formats · AGENT-4 Extending the biggest class · AGENT-5 Copying the local idiom, including across repositories · AGENT-6 Relocation reported as factoring · AGENT-7 Holds, freezes and ceremony · AGENT-8 Treating tooling as exempt

---

## AGENT-1 Keeping old code "for safety"

**The default:** leave the old function, add an alias, keep a fallback branch, in case something still needs it.
**Why agents do it:** public code is full of libraries that genuinely need deprecation cycles, so "keep a deprecated alias" is a learned pattern; and keeping code can never break a test, while deleting it might.
**Why it is wrong here:** deleting is not irreversible; git keeps everything. The old code left in the tree is the unsafe choice: it can still be called, it drifts from its replacement, and the next agent copies it (TIME-1, TIME-3).
**Instead:** find the callers, migrate them, delete. Old code belongs in history. The most common disguise is an adapter that keeps an old shape alive under a new type (TIME-9); it avoids every banned word.

## AGENT-2 Stopping when the new path works

**The default:** once the new mechanism passes its tests, open the PR; migrating the remaining callers and deleting the old mechanism becomes "a follow-up."
**Why it is wrong:** the half-finished migration is the worst state the code can be in: two mechanisms, and nothing saying which is real (IMPL-4, TIME-1). The follow-up rarely comes, because the next task is always more urgent.
**Instead:** done means the guards pass with zero exceptions. There is no "most."

## AGENT-3 Porting every test, and pinning our own formats

**The default:** when code changes, update every test that touched it; add golden files "to be safe."
**Why it is wrong:** test suites here outweighed the code (70,000 lines against 58,000; 32,000 against 16,000). Porting tests of deleted code is fake work, and a golden file of your own format is compatibility by another name: it freezes what you are allowed to change.
**Instead:** delete tests of deleted code; one family-level test per family; golden files only for formats someone else owns; never weaken an assertion to pass.

## AGENT-4 Extending the biggest class

**The default:** add the new feature where related code already lives, which is usually the largest class.
**Evidence:** in the Toad fork, `Conversation` grew from 1,686 to 3,042 lines, `ToadApp` from 632 to 1,889, `Agent` from 773 to 1,886.
**Why it is wrong:** the class's size is already the problem; each addition makes the next agent more likely to add there too.
**Instead:** give the feature its own owner, and let the big class delegate to it.

## AGENT-5 Copying the local idiom, including across repositories

**The default:** match the style of the surrounding code.
**Evidence:** in agent-comms, feature code written before shared abstractions existed ran at 90+ smells per 1,000 lines; after they landed, new modules came in clean. In the Toad fork, agents introduced `type()` re-validation (absent upstream) and long boolean chains (32 times upstream's rate), agent-comms' own habits.
**Why it is wrong:** the surrounding code is not a standard; it is whatever was written last.
**Instead:** match the canonical examples the rules name, not the nearest file.

## AGENT-6 Relocation reported as factoring

**The default:** move code into a new module, service or mixin and call the problem solved.
**Evidence:** a god file's churn "dropped to zero" because it moved to another repository, where it kept churning; mixins carved out of a god class still shared one `self`; a `*Service` that forwards every call keeps the same dispatch.
**Why it is wrong:** the new-case edit count is unchanged; only the file names moved.
**Instead:** measure the new-case edit count before and after. Label pure moves as relocation, and never claim them as factoring.

## AGENT-7 Holds, freezes and ceremony

**The default:** pause work until someone confirms, freeze a PR "until a distinct instruction," re-hash files to prove they did not change, appoint a fresh owner for a small fix.
**Evidence:** agents' own words: "I was wrong to keep imposing it after you received fresh authorization"; "I mistakenly paused it."
**Why it is wrong:** it looks like care and costs the owner's attention, which is the scarcest resource in the system.
**Instead:** keep moving unless told to stop or blocked on something you can name; decide reversible things yourself and state the choice.

## AGENT-8 Treating tooling as exempt

**The default:** write helper scripts quickly, in whatever style is fastest, because "it's just a tool."
**Evidence:** this skill's own first scripts. They measured the patterns in this catalog while committing most of them:

```python
# the first _measures.py: IMPL-3, MEMB-4, BOUND-1, BOUND-7 in twenty lines
c = collections.Counter()
for node in ast.walk(tree_):
    if isinstance(node, ast.Compare):
        if isinstance(node.left, ast.Call) and getattr(node.left.func, "id", None) == "type":
            c["type_is"] += 1                      # measures keyed by hand-written strings
    elif isinstance(node, ast.BoolOp) and len(node.values) >= 4:
        c["chain4"] += 1
    ...

# the first overlay.py: findings as positional tuples in a string-keyed dict
R["raw_shapes"].append((len(ks), key, sb, sorted(ks)[:6], match))
...
for r in R["raw_shapes"]:
    print(r[1], r[2], "BYPASSES " + r[4] if r[4] else "unmodeled")
```

**Why it is wrong:** agents read tools as examples like any other code, and a skill's scripts are read by every agent that uses the skill.
**Instead:** tools meet the same bar:

```python
class Measure(ABC):
    """One kind of debt, owning how it is recognized."""
    headline: ClassVar[bool] = False
    @classmethod
    @abstractmethod
    def matches(cls, node: ast.AST) -> bool: ...

class TypeIdentityCheck(Measure):
    headline = True
    @classmethod
    def matches(cls, node: ast.AST) -> bool:
        match node:
            case ast.Compare(left=ast.Call(func=ast.Name(id="type"))):
                return True
        return False

@dataclass(frozen=True)
class BypassedShape(Finding):          # a finding is a typed record that renders itself
    function: str
    keys: tuple[str, ...]
    model: str
```

A family of measures replaces the `isinstance` chain; structural `match` on Python's own AST classes replaces `getattr` probing; typed findings replace tuples read by index.
