# Membership rung: which things belong to this set?

A set of family members, or of members sharing a property, is a fact the family already knows. Every pattern here writes it down again by hand, where it drifts the first time the family changes.

## Contents

MEMB-1 A roster restating a family · MEMB-2 A roster restating a capability · MEMB-3 Literal sets and special names spelled in many places · MEMB-4 Hand-written name strings · MEMB-5 One record's shape written four times

---

## MEMB-1 A roster restating a family

**Seen in:** agent-comms' recovery gateway client; Toad's settings.

```python
# recovery_gateway_client.py: each set restates an enum declared elsewhere
_STATUSES = frozenset({"queued", "pending", "active", "deferred", "completed", "failed"})
_PHASES = frozenset({"prompt_starting", "prompt_accepted", "model_running", ...})   # all twelve

if payload["status"] not in _STATUSES:
    raise GatewayError("unknown status")

# toad/settings.py
INPUT_TYPES = {"boolean", "integer", "number", "string", "choices", "text"}
```

**What the code makes you know:** that a new member must be added here too, in a different module.

```python
status = ExecutionState.decode(payload.status)   # the family is the only list; unknown names fail here
# INPUT_TYPES: SettingKind's members, if anything needs the list at all
```

**What collapses:** every copy of the list.
**Detected by:** module-level `frozenset`s or sets of string literals whose values match a family's names.

---

## MEMB-2 A roster restating a capability

**Seen in:** agent-comms' pi backend, ACP consumer and goal tool.

```python
_SESSION_MUTATING_COMMANDS = {"set_model", "set_thinking_level", "compact", ...}
if kind in ("input_started", "done", "settled"):           # which events move the turn
    self._sync_goal_execution()
choices = ("active", "standby", "completed", "blocked")    # which goal actions a model may take
if phase in {"compaction", "summarization_retry", "provider_retry"}:   # where no output is expected
    ...
```

**What the code makes you know:** which members share a property, restated wherever the property matters.

```python
class MutatesSession: ...            # the property is a class the members inherit
class SetModel(PiCommand, MutatesSession): ...

class StallExempt: ...
class Compaction(Excursion, StallExempt): ...

PiCommand.members_with(MutatesSession)          # derived wherever a set is needed
GoalAction.members_with(ModelInvocable)
```

**What collapses:** each roster; a new member declares the capability once and every set includes it. This is where multiple inheritance earns its place: capabilities overlap without nesting.
**Detected by:** a tuple or set literal of member names inside a condition; overlay `literal_rosters`.

---

## MEMB-3 Literal sets and special names spelled in many places

**Seen in:** agent-comms' built-in channels; Toad's sidebars; a store's file name.

```python
if channel == "#any": raise ValueError("aggregate channel")        # exporting.py
target = "#all"                                                     # claim_admission.py
GLOBAL_TARGET = "#all"                                              # acp.py
if target == "broadcast": target = "#all"                           # channels.py: an alias
if exact_target in {"#any", "broadcast"}: ...                       # audience_manifest.py, twice
path = root / "read_markers.json"                                   # nine times, two modules
```

**What the code makes you know:** that `broadcast` means `#all`, that only `#any` is an aggregate, and every place those facts are spelled.

```python
class BuiltinChannel(StrEnum):
    ANY = "#any"
    ALL = "#all"
    NONE = "#none"
    @property
    def aggregate(self) -> bool: return self is BuiltinChannel.ANY

# the file name belongs to the store that owns the file:
READ_LEDGER = LockedStore(root / "read_markers.json", ReadLedgerRecord)
```

(Under a no-compatibility rule, the `broadcast` alias is deleted outright rather than declared.)
**What collapses:** every spelling but one.
**Detected by:** grep for each special value; overlay `literal_rosters`.

---

## MEMB-4 Hand-written name strings

**Seen in:** a first draft of state classes; export kinds; stored names.

```python
class Queued(ExecutionState):
    value = "queued"                 # the class name, typed again
class Active(ExecutionState):
    value = "active"
```

**What the code makes you know:** that the string and the class name must agree, in every class.

```python
class DeclaredFamily:
    def __init_subclass__(cls, affix: str | None = None, declared_name: str | None = None, **kw):
        ...                          # derives snake_case(class name, minus affix); registers; rejects collisions

class Queued(ExecutionState): ...                              # "queued", derived
class MouseAnyEventMode(TerminalMode, declared_name="1003"): ... # an external standard's spelling
```

`declared_name` exists only for spellings someone else owns. Pin a family's names with a golden test only when they are an external contract.
**What collapses:** one string per class.
**Detected by:** a class attribute whose string equals the class name in another case.

---

## MEMB-5 One record's shape written four times

**Seen in:** agent-comms' SQLite stores: 57 tables, 321 reads by column name, 34 positional inserts.

```python
@dataclass(frozen=True)
class OrdinaryDeliveryCandidate:                # 1. the class
    candidate_id: str
    thread: str
    ...

"""CREATE TABLE ordinary_delivery_candidates (   -- 2. the DDL
       candidate_id TEXT PRIMARY KEY, thread TEXT NOT NULL, ...)"""

conn.execute("INSERT INTO routes VALUES (?, ?, ?, ?)", (a, b, c, d))   # 3. the column order, implicitly

row = conn.execute("SELECT * FROM ordinary_delivery_candidates WHERE ...").fetchone()
return OrdinaryDeliveryCandidate(candidate_id=row["candidate_id"],     # 4. every reader's mapping
                                 thread=row["thread"], ...)
```

**What the code makes you know:** that four copies agree, including a column order no line of code states. Reorder two same-typed columns and a positional insert writes wrong data without an error.

```python
@dataclass(frozen=True)
class OrdinaryDeliveryCandidate:
    candidate_id: str = field(metadata={"primary_key": True})
    thread: str
    ...

CANDIDATES = TypedTable("ordinary_delivery_candidates", OrdinaryDeliveryCandidate)
# DDL, inserts with column lists, and strict decoding of rows: all derived from the class
```

**What collapses:** three of four copies; a new column is one field (and a migration).
**Detected by:** overlay `positional_inserts`, and `raw_shapes` whose subject is a row.
