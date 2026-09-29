# Identity rung: which thing is this, and which fact is this field?

Identity is the lowest rung: a family cannot own behaviour for members it cannot tell apart, and a field cannot be trusted when it answers two questions. These patterns produce the subtlest bugs, because every individual line looks right.

## Contents

IDEN-1 One field answering two questions · IDEN-2 One name with several meanings · IDEN-3 One concept, several encodings · IDEN-4 A name that disagrees with its value · IDEN-5 One fact split across stores · IDEN-6 Keyed by the wrong identity · IDEN-7 A check wider than the question · IDEN-8 A bare process ID as identity

---

## IDEN-1 One field answering two questions

**Seen in:** agent-comms' thread registry: `owner_epoch` is bumped when ownership changes **and** on every turn claim.

```python
def _claim_turn_unlocked(self, name: str) -> tuple[Thread, int]:
    current = replace(current, turn_generation=current.turn_generation + 1)
    self._bump_owner_epoch_unlocked(name)              # a turn claim also moves the "owner" counter
    self._turn_epochs[name] = self._owner_epochs[name]
```

**What the code makes you know:** that "has the owner changed?" cannot be answered from `owner_epoch`, since every turn also changes it. A consumer wanting only ownership sees a false change each turn. (The same defect had already been split out once, as a separate admission counter; this was its second form.)

```python
@dataclass(frozen=True)
class ThreadIncarnation:
    name: str
    created_at: float            # a recreated thread is a new incarnation
    owner_generation: int        # bumped only when ownership changes

@dataclass(frozen=True)
class TurnIdentity:
    incarnation: ThreadIncarnation
    turn_generation: int         # bumped on every turn claim, and nothing else
```

**What collapses:** the double-duty counter and its derived snapshot (`_turn_epochs`); each question has its own field.
**Detected by:** one counter bumped from functions about different events; a field whose consumers each "filter out" some of its changes.

---

## IDEN-2 One name with several meanings

**Seen in:** agent-comms: "claim" meant wake attention, resource ownership, and turn claiming; "settled" named two different events in two protocols.

```python
yield {"type": "settled"}                                              # backend: the model stream finished
await self._emit_event(sid, {"type": "settled", "turn_id": turn_id})   # ACP: this turn is finalized
```

**What the code makes you know:** which meaning each use has, from context.

```python
class StreamSettled(TurnLifecycleEvent): ...    # the backend's event

@dataclass(frozen=True)
class TurnSettled(OutboundUpdate):              # the subscriber-facing event
    turn: TurnIdentity
# and "claim" keeps one meaning; the others become WakeAssignment and TurnLease
```

**What collapses:** the ambiguity, and the class of bug where code for one meaning handles the other.
**Detected by:** one identifier used with different payload shapes; a word appearing in unrelated modules' class names.

---

## IDEN-3 One concept, several encodings

**Seen in:** a Toad status line: "this source could not be read" is a boolean for one source, `None` for another, and a status string for a third.

```python
attention = activity.unavailable
if activity.unavailable:                                      # a boolean
    parts.append("Status unavailable")
elif presentation is not None:                                # None means absent
    parts.append(presentation.summary.partition("\n")[0] or "Ready")
if self.history is not None and self.history.status == "unavailable":   # None, or a status string
    parts.append("History unavailable")
```

**What the code makes you know:** three representations of one concept, and which source uses which.

```python
class SourceState(ABC, Generic[T]):
    needs_attention: ClassVar[bool] = False
    def status_part(self, label: str) -> str | None: return None

@dataclass(frozen=True)
class Available(SourceState[T]):
    value: T

@dataclass(frozen=True)
class Unavailable(SourceState[T]):
    needs_attention = True
    def status_part(self, label: str) -> str: return f"{label} unavailable"

sources = {"Status": activity, "History": history}
parts = [p for label, s in sources.items() if (p := s.status_part(label))]
attention = any(s.needs_attention for s in sources.values())
```

**What collapses:** the three encodings, the branching, and the per-source text.
**Detected by:** the census's `foreign_absence_probe`: `other.attr is None` and `not other.attr`, code outside an object probing its absent state. Measure the reader, not the declaration. In the Toad fork, the refactor cut optional fields and attributes (−34 and −19 in a day) while `None` checks rose by 75; probes of other objects rose by 81, in the new modules reading another object's state from outside (one checked 18 of a conversation's fields, private ones included). Guard: the ratchet's probe count never increases in a touched file.

---

## IDEN-4 A name that disagrees with its value

**Seen in:** the same Toad snippet.

```python
attention = activity.unavailable      # named for one question, holding the answer to another
```

**What the code makes you know:** whether "attention" is meant to cover only activity, or whether an unavailable history was forgotten. Either a bug or a rule, and nothing says which.

**The clean form:** a declared property on the state (`needs_attention`), aggregated over every source, as in IDEN-3.
**Detected by:** reading. Names and values that disagree are a fast tell of an unowned fact.

---

## IDEN-5 One fact split across stores

**Seen in:** agent-comms' goals: whether a goal is paused lives on the goal; who paused it lives in another file, joined by revision.

```python
paused = goal.status == "paused"
event = pause_events.get(f"{goal.goal_id}:{goal.revision}")   # found only while the revision matches
source = event.source if event else None
```

Any path that rewrites a paused goal bumps its revision and severs the join: the goal stays paused but forgets the owner paused it, and the model may then resume it. A shipped fix repaired one such path; the representation invited the next.

```python
@dataclass(frozen=True)
class PausedGoal(GoalState):
    source: PauseSource              # who paused it lives in the state; no revision can lose it
```

**What collapses:** the join, and the whole class of lost-source bugs.
**Detected by:** a key built from an identifier plus a version; two stores read together to answer one question.

---

## IDEN-6 Keyed by the wrong identity

**Seen in:** agent-comms' read markers: watermarks keyed by *view* (viewer, channel, mode, participant basis).

```python
def _view_marker_key(viewer, channel, mode, participants) -> str:
    return json.dumps(["view2", viewer, channel, mode, participants])
```

"Has this viewer seen message *m*?" is a fact about a viewer and a message. As a watermark on a view, two situations share one representation: *m* was displayed and read, or *m* was hidden in that mode and merely sits below the watermark. Change the mode or rebind a DM, and the watermark asserts reads of messages never shown.

```python
class ReadLedger:
    def seen_through(self, viewer: str, conversation: Conversation) -> int: ...
    def mark_displayed(self, viewer: str, displayed: DisplayBasis) -> None: ...   # only what was shown
    def unread(self, viewer: str, view: View) -> int: ...                         # views own no markers
```

**What collapses:** two key formats, fallbacks between them, and the false reads.
**Detected by:** a key that includes presentation choices (mode, filter, basis) for a fact about content.

---

## IDEN-7 A check wider than the question

**Seen in:** agent-comms' DM read acknowledgement.

```python
if file_revision(self.registry._path) != proof.registry_revision:
    raise StaleDisplay()              # any registry write, including any turn claim, rejects a genuine read
```

The proof exists to answer "is this still a DM between the same two thread incarnations?" It checks two much wider facts, so it rejects situations that do not differ: the opposite failure to IDEN-6.

```python
if (proof.viewer, proof.peer) != (current.viewer, current.peer):   # ThreadIncarnation values
    raise StaleDisplay()
```

**What collapses:** the false rejections, while a genuine delete-and-rebind is still caught.
**Detected by:** a staleness check comparing a whole file's revision or a global counter.

---

## IDEN-8 A bare process ID as identity

**Seen in:** agent-comms' owner processes: launched, recorded as `pid=process.pid`, and later probed and signalled by that number alone.

A PID is reused after a process dies. Long-lived detached processes, signalled long after launch, are exactly where reuse happens; the codebase had already solved it with `pidfd` in two newer modules.

```python
@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    started_at: str        # read per platform; checked before every liveness answer and signal
```

**What collapses:** the risk of signalling an unrelated process, or its whole group.
**Detected by:** `os.kill` or `killpg` on a stored integer.
