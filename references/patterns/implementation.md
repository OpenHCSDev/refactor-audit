# Implementation rung: what does each case do?

The answer to "what does this case do?" belongs to the case. Every pattern here puts it somewhere else: a switch in a consumer, a table beside the family, a copy of a procedure.

## Contents

IMPL-1 String dispatch on a kind · IMPL-2 Enum with no methods · IMPL-3 A `match` over types that already exist · IMPL-4 The half-finished family · IMPL-5 One dispatch written twice · IMPL-6 Effects decided centrally by key · IMPL-7 A string action with a parameter bag · IMPL-8 The undeclared class · IMPL-9 The longhand tagged union · IMPL-10 Legality by runtime rejection · IMPL-11 A transition table beside its states · IMPL-12 One procedure copied, with drift · IMPL-13 One mechanism at different levels of rigour · IMPL-14 Validation as one anonymous boolean

---

## IMPL-1 String dispatch on a kind

**Seen in:** agent-comms' backend-to-ACP events: 15 kinds spelled in the producer and again in two consumers.

```python
# backend.py, the producer
yield {"type": "tool_start", "id": call_id, "name": name, "title": title}
yield {"type": "done", "ok": False, "text": text, "reason_code": code}   # reason_code at this one site only

# acp.py, inside a 1,058-line function
kind = event.get("type")
if kind == "tool_start":
    self.update_turn_activity(ActivityState.WORKING, event.get("title", ""))
if kind in ("input_started", "done", "settled"):
    self._sync_goal_execution()

# agent_loop.py, a second consumer
elif kind == "tool_start":
    set_activity(name, ActivityState.WORKING, event.get("title", ""))
```

**What the code makes you know:** which kinds exist; which fields each carries (and that `reason_code` exists on only some `done`s); that three files' strings must agree; which kinds trigger goal sync. A misspelled kind is an event silently ignored.

```python
class AgentEvent(ABC):
    """What the backend reports during a turn. Closed family."""

class TurnLifecycleEvent(AgentEvent):
    """Moves the turn's lifecycle; goal sync reacts to every member."""

@dataclass(frozen=True)
class ToolEvent(AgentEvent):
    id: str
    name: str

@dataclass(frozen=True)
class ToolStart(ToolEvent):
    title: str

@dataclass(frozen=True)
class ToolEnd(ToolEvent, TurnLifecycleEvent):     # a tool event AND a lifecycle event
    ok: bool

class AgentEventConsumer(MroDispatch):
    @handles(ToolStart)
    def show_working(self, event: ToolStart) -> None:
        self.set_activity(Working(event.title))    # written once; both consumers inherit it

    @handles(TurnLifecycleEvent)
    def sync_goal(self, event: TurnLifecycleEvent) -> None:
        ...                                        # fires for every lifecycle event through the MRO
```

**What collapses:** a new event needed a producer dict, an arm in each consumer, and its fields re-read with `.get` in each, across three files with nothing checking them; now it is one class, plus a handler only where someone reacts. The goal-sync roster, the duplicated reaction and every payload `.get` disappear.
**Detected by:** overlay `string_dispatch`; grep `event.get("type")`.

---

## IMPL-2 Enum with no methods, switched on everywhere

**Seen in:** agent-comms' coordination: 14 enums, none with a single method; `ExecutionStatus` switched on in 14 functions.

```python
class ExecutionStatus(StrEnum):
    QUEUED = "queued"
    PENDING = "pending"
    ACTIVE = "active"
    DEFERRED = "deferred"
    COMPLETED = "completed"
    FAILED = "failed"

# one of fourteen places that behave differently per status
if record.status is ExecutionStatus.ACTIVE and record.current_attempt_ordinal is None:
    raise ValueError("active execution needs an attempt")
if before.status in {ExecutionStatus.PENDING, ExecutionStatus.DEFERRED}:
    ...
```

**What the code makes you know:** all fourteen places a status changes behaviour, and which statuses carry which data.

```python
class ExecutionState(LifecycleState): ...

class Queued(ExecutionState): ...
class Pending(ExecutionState): ...

@dataclass(frozen=True)
class Active(ExecutionState):
    attempt_ordinal: int                 # only an active execution has an attempt

class Deferred(ExecutionState): ...
```

Each state owns its behaviour and its data; consumers call methods on the state they hold.
**What collapses:** a new status needed the enum member, fourteen switch sites, validation, a serializer and a client roster; now it is one class.
**Detected by:** NRA `external_enum_case_recovery`; overlay `string_dispatch`.

---

## IMPL-3 A `match` over types that already exist

**Seen in:** Toad's terminal emulator: twelve command types, applied by a 223-line `match` in `TerminalState`.

```python
class ANSICursor(NamedTuple):             # NamedTuples cannot share a real base class,
    delta_x: int | None = None            # so they are joined by a union alias instead
    ...
ANSICommand = ANSIContent | ANSICursor | ANSIStyle | ANSIClear | ...

def _handle_ansi_command(self, command: ANSICommand) -> None:   # 223 lines
    match command:
        case ANSICursor(delta_x, delta_y, absolute_x, absolute_y, erase, clear_range):
            ...
        case ANSIClear(clear):
            ...
        case ANSIScroll(direction, lines):
            ...
```

**What the code makes you know:** that each command's effect lives in an arm of one function in another class, not with the command.

```python
class ANSICommand(ABC):
    @abstractmethod
    def apply(self, state: TerminalState) -> None: ...

@dataclass(frozen=True, slots=True)
class ANSICursor(ANSICommand):
    delta_x: int | None = None
    ...
    def apply(self, state: TerminalState) -> None:
        ...                               # the body of the old arm, now owned by the command

# TerminalState: command.apply(self)
```

**What collapses:** the `match`, the union alias, and a new command's edit to a function in another class.
**Detected by:** a `match` over class patterns, or an `isinstance` chain over one family.

---

## IMPL-4 The half-finished family

**Seen in:** Toad's stream parser: five read types, two of which own `feed`; the parser handles all five with an `isinstance` switch.

```python
class ReadPattern(StreamRead):            # owns feed, is_exhausted, unconsumed_text
    def feed(self, text): ...
class ReadRegex(StreamRead):              # owns only __init__
    ...

def _feed(self, text):                    # 85 lines
    if isinstance(self._reading, (ReadPattern, ReadPatterns)):
        ...
    elif isinstance(self._reading, Read):
        ...
    elif isinstance(self._reading, ReadUntil):
        ...
```

**What the code makes you know:** that two members are finished and three are handled from outside, and which is which.

```python
class StreamRead(ABC):
    @abstractmethod
    def feed(self, buffer: Buffer) -> Result | None: ...   # every read owns its consumption

# StreamParser: result = self._reading.feed(buffer)
```

**What collapses:** the switch; a new read type is one class.
**Detected by:** overlay `isinstance_switches` where some members already own the method.

---

## IMPL-5 One dispatch written twice

**Seen in:** Toad's settings screen: the same seven-way dispatch on a setting's type in `compose` and in `schema_to_widget` (140 lines), with the integer and number branches each dispatching again on validation strings.

```python
if setting.type == "boolean":
    yield Switch(...)
elif setting.type == "integer":
    for rule in setting.validate or []:
        if rule["type"] == "minimum": ...
        elif rule["type"] == "maximum": ...
    yield Input(..., type="integer")
elif setting.type == "number":
    for rule in setting.validate or []:
        if rule["type"] == "minimum": ...          # the same code again
        elif rule["type"] == "maximum": ...
    yield Input(..., type="number")
```

**What the code makes you know:** that two functions must stay in step, and that integer and number validation are the same code written twice.

```python
class Bounded:                                     # capability: shared bounds and their validation
    minimum: float | None = None
    maximum: float | None = None
    def validators(self) -> list[Validator]: ...

class SettingKind(ABC, Generic[T]):
    @abstractmethod
    def widget(self, key: str, value: T) -> Widget: ...

class IntegerSetting(SettingKind[int], Bounded): ...
class NumberSetting(SettingKind[float], Bounded): ...
```

**What collapses:** both dispatches and the duplicated validation; a new kind of setting is one class.
**Detected by:** the same string-dispatch subject appearing in two functions in the overlay.

---

## IMPL-6 Effects decided centrally by key

**Seen in:** Toad's `app.py::setting_updated`, an 11-way `elif` on setting keys.

```python
def setting_updated(self, key: str, value: object) -> None:
    if key == "ui.column":
        if isinstance(value, bool):
            ...
    elif key == "ui.column-width":
        if isinstance(value, int):
            ...
    elif key == "ui.theme":
        ...
```

**What the code makes you know:** that each setting's effect lives in a branch of the application class, keyed by a string that must match the schema, re-checking a type the schema already declared.

```python
class UiSettings(SettingsGroup):
    column = BooleanSetting(title="Column layout", default=False, effect=App.apply_column)
    theme = ChoiceSetting(Theme, default=DefaultTheme, effect=App.apply_theme)

# on change: setting.effect(app, value)
```

**What collapses:** the switch and its type checks; a setting with an effect is one declaration.
**Detected by:** overlay `string_dispatch` on a key subject, with an `isinstance` switch beside it.

---

## IMPL-7 A string action with a parameter bag

**Seen in:** agent-comms' `update_goal`: 296 lines, 15 parameters, dispatching on a string `action`; also `runtime.handle` (12 actions) and `cli.main` (27 commands).

```python
def update_goal(self, name, action, text=None, progress=None, block_reason=None,
                goal_id=None, expected_status=None, expected_goal=None, model_report=None,
                owner_action=None, owner_store=None, expected_owner_pid=None,
                wait_for=None, reviewed_inputs=None):
    if action == "set": ...          # uses text
    elif action == "blocked": ...    # uses block_reason
    elif action == "standby": ...    # uses wait_for
```

**What the code makes you know:** which of fifteen parameters each action reads, and which actions a model may take (hand-listed elsewhere, in a tool's `choices`).

```python
@dataclass(frozen=True)
class GoalAction(Command):
    expect: GoalPrecondition                     # shared compare-and-set, once
    def apply(self, goal: Goal, ctx) -> Goal: ...

class ModelInvocable: ...                        # capabilities: who may take the action
class OwnerInvocable: ...

@dataclass(frozen=True)
class Block(GoalAction, ModelInvocable):
    reason: str                                  # only Block carries a reason

@dataclass(frozen=True)
class Pause(GoalAction, OwnerInvocable): ...     # the model cannot pause

# the tool's choices: GoalAction.members_with(ModelInvocable)
```

**What collapses:** the signature, the dispatch, and the hand-listed tool choices; a new action is one class.
**Detected by:** a function with many optional parameters and string dispatch on one of them.

---

## IMPL-8 The undeclared class: closures sharing `nonlocal` state

**Seen in:** agent-comms' `_stream_agent_events`: 1,560 lines, 19 parameters, 11 closures, about 45 local variables reassigned three or more times, nesting 23 deep.

```python
async def _stream_agent_events(agent_bin, agent_args, task, cwd, env_extra, session_file,
                               steering_queue, finish_event, ...):          # 19 parameters
    stats_requested = False
    fail_reason = None                                                      # assigned at 23 sites
    phase = "prompt_acceptance"                                             # assigned at 14 sites
    async def forward_steering():
        nonlocal fail_reason, input_uncertain, authority_revoked, ...       # rewrites six outer variables
        ...
```

**What the code makes you know:** which locals are really fields, which belong together, and which closure may change which.

```python
class TurnSession:
    """Was _stream_agent_events. Components own the fact families it interleaved."""
    phase: TurnPhase
    failure: TurnFailure | None
    stats: StatsRequest
    inputs: InputForwarding
    watchdog: ProgressWatchdog
    usage: UsageAccount
```

**What collapses:** the parameter list becomes configuration, the closures become methods of the components that own their state, and nesting unwinds. Extracting the class first is relocation; the factoring is the components owning their facts.
**Detected by:** `nonlocal`; many parameters; overlay `god_functions`.

---

## IMPL-9 The longhand tagged union

**Seen in:** agent-comms' export limits and scopes; Toad's `Setting`.

```python
@dataclass(frozen=True)
class WireExportLimit:
    kind: WireExportLimitKind
    value: int | float | None          # a byte count for MAX_BYTES, a timestamp for RECENT

    def __post_init__(self):
        object.__setattr__(self, "kind", WireExportLimitKind(self.kind))
        if self.kind is WireExportLimitKind.FULL and self.value is not None: raise ValueError
        if self.kind is WireExportLimitKind.MAX_BYTES and (type(self.value) is not int or self.value <= 0): raise ValueError
        ...

    @classmethod
    def max_bytes(cls, n: int) -> "WireExportLimit": ...   # one named constructor per case:
    @classmethod
    def recent(cls, t: float) -> "WireExportLimit": ...    # the subclasses, waiting to be declared
```

**What the code makes you know:** which fields go with which kind, and what `value` means (two different units).

```python
class WireExportLimit(DeclaredFamily, affix="Limit"):
    def byte_ceiling(self) -> int | None: return None
    def time_cutoff(self) -> float | None: return None

@dataclass(frozen=True)
class FullLimit(WireExportLimit): ...

@dataclass(frozen=True)
class MaxBytesLimit(WireExportLimit):
    value: int                          # a byte count, and only that
    def byte_ceiling(self) -> int: return self.value
```

**What collapses:** the kind enum, the validation, the named constructors, and the switches consuming them.
**Detected by:** a `kind` field next to optional fields; one named constructor per case.

---

## IMPL-10 Legality by runtime rejection

**Seen in:** agent-comms' coordination: about 47 `raise`s across five `__post_init__` methods, 25 of them in `RecoverySnapshot`.

```python
def __post_init__(self):
    if self.disposition is ClaimDisposition.ENGAGED and self.verdict is not TriageVerdict.ENGAGE:
        raise ValueError("engaged claim needs an engage verdict")
    if self.disposition is ClaimDisposition.PASSIVE and self.verdict is not None:
        raise ValueError("passive claim has no verdict")
    ...
```

**What the code makes you know:** the legal combinations, encoded as a list of the illegal ones.

```python
@dataclass(frozen=True)
class Engaged(ClaimState):
    verdict: EngageVerdict          # only this state has a verdict; nothing to check

class Passive(ClaimState): ...      # no verdict field exists to be wrong
```

**What collapses:** the checks themselves: an illegal state can no longer be constructed. Keep only invariants that genuinely span several objects, each as a named rule.
**Detected by:** many `raise`s in `__post_init__` comparing enum members.

---

## IMPL-11 A transition table beside its states

**Seen in:** agent-comms' four `*_TRANSITIONS` dicts (139 lines) with inline rosters restating parts of them; Toad's goal toggle.

```python
EXECUTION_STATUS_TRANSITIONS = {
    ExecutionStatus.QUEUED: {ExecutionStatus.PENDING, ExecutionStatus.FAILED},
    ...
}
if after.status is ExecutionStatus.ACTIVE and before.status not in {ExecutionStatus.PENDING, ExecutionStatus.DEFERRED}:
    ...                              # a second, partial copy of the table

toggle = {"active": "paused", "paused": "active", "blocked": "retry", "completed": ""}[self.status]
```

**What the code makes you know:** that the table, the inline rosters and every predecessor's row must be edited together.

```python
class Queued(ExecutionState):
    @classmethod
    def successors(cls): return (Pending, Failed)      # declared on the state

# anything needing a table derives it: ExecutionState.transition_table()
```

**What collapses:** the tables and the inline rosters; a transition is declared once, on its state.
**Detected by:** module-level dicts keyed by enum members; overlay `literal_rosters`.

---

## IMPL-12 One procedure copied, with drift

**Seen in:** agent-comms' turn settlement, written three times; the third copy skips a step.

```python
# acp.py, copy one
self._active_turns.pop(session_id, None)
fence = self._comms.finish_turn(thread, turn_id, expected=claim)
try:
    await self._emit_event(session_id, {"type": "settled", "turn_id": turn_id})
finally:
    self._comms.pause_waits_after_terminal_turn(fence)

# manual_compaction_bridge.py, copy three, reaching into another object's privates
agent._active_turns.pop(session_id, None)
agent._comms.finish_turn(thread, turn_id, expected=claim)      # the fence is discarded
await agent._emit_event(session_id, {"type": "settled", "turn_id": turn_id})
                                                               # pause_waits_after_terminal_turn: never called
```

**What the code makes you know:** that three places must perform one procedure identically, and which one is right.

```python
class CommsAgent:
    async def settle_turn(self, turn: TurnIdentity, claim: TurnLease) -> None:
        """The one way a turn settles, in one order, with every step."""
```

**What collapses:** two copies and a real bug.
**Detected by:** near-identical blocks; private attributes of another object reached from outside (`agent._comms`).

---

## IMPL-13 One mechanism at different levels of rigour

**Seen in:** agent-comms' child processes: eleven modules, from race-free `pidfd` and PID-namespace containment down to bare-PID signalling of long-lived owners.

```python
# owner_lifecycle.py: the weakest version guards the riskiest case
def _signal_local_owner(pid: int, signum: int) -> None:
    if os.name == "posix" and os.getpgid(pid) == pid:
        os.killpg(pid, signum)            # a reused PID can be an unrelated process group
    else:
        os.kill(pid, signum)
```

**What the code makes you know:** which of eleven implementations is correct, and that the best one already exists elsewhere in the codebase.

```python
class ChildProcess(ABC):
    def stop(self) -> ChildOutcome:
        """Template: graceful signal to the group, one grace period, forced kill, reap."""

class ProcessGroups: ...                   # platform capabilities, composed per platform
class PidfdHandles: ...
class LinuxPlatform(Platform, ProcessGroups, PidfdHandles, NamespaceContainment): ...
class MacPlatform(Platform, ProcessGroups): ...

@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    started_at: str                        # a reused PID has a different start time
```

**What collapses:** eleven implementations into one, lifted from the best existing one rather than rewritten.
**Detected by:** overlay `child_process` sites across many modules.

---

## IMPL-14 Validation as one anonymous boolean

**Seen in:** agent-comms: 109 conditions of six or more terms on `main`, the largest 23 terms. This one decides whether a reserved input source is still valid:

```python
if (
    source.owner_name != owner.name
    or source.owner_created_at != float(owner.created_at).hex()     # identity, encoded inline
    or source.turn_id == owner.active_turn.id
    or source.reserved_revision != _session_revision(witness.session_file)
    or row is None
    or row.owner != owner.name                                       # the same identity, weaker
    or row.admission != source.admission_generation
    or row.native_id is not None or row.turn_id is not None or row.sent_text is not None
    or hashlib.sha256(row.source_text.encode()).hexdigest() != source.original_sha256
):
```

**What the code makes you know:** eleven rules, fused so that when the check fails nobody can tell which rule broke: the error, the log line and whoever debugs it all get "invalid". Inside the chain, most terms restate something owned elsewhere: the owner's identity compared field by field with its encoding written inline (6 sites across 6 files on `main`), then compared again by name alone; "the row is still only reserved" as three `None` checks (IDEN-3, IMPL-10); the hashing scheme at the use site (11 digest comparisons on `main`).

```python
@dataclass(frozen=True)
class ReservedSource:
    owner: ThreadIncarnation          # compared as one value; the encoding lives in its codec
    turn: TurnId
    revision: SessionRevision
    admission: Generation
    digest: TextDigest                # TextDigest.of(text) is the only place the hash scheme exists

class InputRow(ABC):
    accepts_reservation: ClassVar[bool] = False
class ReservedRow(InputRow):
    accepts_reservation = True        # owner, admission, digest
class SentRow(InputRow): ...          # native_id, turn, sent_text exist only here
class MissingRow(InputRow): ...

class ReservationRule(DeclaredFamily, affix="Rule"):
    @abstractmethod
    def violated(self, check: ReservationCheck) -> bool: ...

class OwnerChangedRule(ReservationRule):
    def violated(self, check): return check.source.owner != check.owner.incarnation

class AlreadySentRule(ReservationRule):
    def violated(self, check): return not check.row.accepts_reservation

class ContentChangedRule(ReservationRule):
    def violated(self, check): return check.row.digest != check.source.digest

violation = next((rule for rule in ReservationRule.members() if rule().violated(check)), None)
```

**What collapses:** the chain becomes a lookup that returns *which* rule failed, and its name is the error. The identity encoding goes from six sites to one codec, "still reserved" becomes a state whose sent fields cannot exist on a reserved row, and the hash scheme lives in one class. A new rule is one class, and every other place validating a source reuses the same rules instead of writing its own chain.
**Detected by:** the census's `boolean_chain_terms` (a chain weighs its term count, so an eleven-term chain counts eleven); overlay `long_condition`, listed by term count, with each chain's terms classified (`scripts/audit/chain_terms.py`). The dominant kind suggests what to inspect: same-value comparisons may need an identity or snapshot value (IDEN-1); absence tests may indicate an owner whose state is being reconstructed (IDEN-3); own attributes may indicate lifecycle flags (IMPL-10); type tests may indicate decoding at the use site (BOUND-1); literal comparisons may indicate a missing family (IMPL-1). Predicate calls remain OPEN, because they may already query the correct owner. Only source-backed unrelated rules justify this pattern's rule family; preserve evaluation order, short-circuit guards and effects. Guard: the ratchet's chain-term measure, so no touched file adds terms.
