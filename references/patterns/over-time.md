# Duplication over time: the old version left beside the new

These patterns duplicate across time instead of across files: the replacement arrives, and the thing it replaced stays. Each leaves two answers to one question in the tree, and agents copy whichever has more call sites.

## Contents

TIME-1 A legacy path beside its replacement · TIME-2 Converters and key renames on load · TIME-3 Compatibility entry points, aliases and re-exports · TIME-4 Negotiation between your own components · TIME-5 A test switch in product code · TIME-6 Dead modules · TIME-7 Copies of another component's defaults · TIME-8 Two declarations of one external format · TIME-9 The adapter: the new type on top, the old shape underneath

---

## TIME-1 A legacy path beside its replacement

**Seen in:** agent-comms' publisher, input drain and cutover machinery.

```python
async def publish(self, message):
    if self._fresh_root_enabled():
        return await self._publish_fresh_root(message)
    # Legacy publish rechecks its barrier under its own lock: if a fresh-root
    # cutover raced the dispatch, it refuses ...
    return await self._publish_legacy(message)
```

**What the code makes you know:** which path serves production today, and whether the other is still reachable.

**The clean form:** establish from evidence which path serves production. If it is the new one, delete the old. If it is the old one, bring the new one to parity, switch, and delete the old in the same change. Then delete the cutover machinery itself (here a 641-line module), since nothing remains to cut over.
**What collapses:** one of two paths, and the machinery switching between them.
**Detected by:** overlay `legacy`; a flag selecting between two implementations of one operation.

---

## TIME-2 Converters and key renames on load

**Seen in:** agent-comms' registry and goals.

```python
raw = json.loads(path.read_text())
if "owner_epochs" in raw:
    raw["owner_generations"] = raw.pop("owner_epochs")   # read the old format, forever
status = raw.pop("status", None)                        # rebuild the new record from old keys
```

**What the code makes you know:** every past format, since the loader must keep accepting them all.

**The clean form:** the code reads one format. Runtime state is reset at cutover; durable state is converted once by a tool outside `src/` that runs and is then deleted. Where a derivation can reproduce today's stored names exactly (for example, deriving keys from attribute paths), no conversion is needed at all.
**What collapses:** the converters, and every future one.
**Detected by:** `.pop("…")` in a loader; overlay `legacy`.

---

## TIME-3 Compatibility entry points, aliases and re-exports

**Seen in:** Toad; a round-1 carve plan; named constructors kept for another repository.

```python
def routed_kinds(self) -> tuple[str, str]:
    """Compatibility for callers selecting the original two routed kinds."""
    return self._kinds[:2]

from .model.threads import *     # an aggregator kept so 73 importers need not change
```

**What the code makes you know:** that callers may use either the old or the new entry point, and which is meant.

**The clean form:** migrate the callers in the same change and delete the old entry point. If the callers are in another repository you control, change it in a lockstep PR, pinned and installed together.
**What collapses:** the alias, and the question every future reader asks about it.
**Detected by:** docstrings and comments mentioning compatibility; star re-exports.

---

## TIME-4 Negotiation between your own components

**Seen in:** Toad and its MCP package.

```python
if POSITIVE_DECISIONS_CAPABILITY not in inventory.compatibility:
    raise McpError("package lacks the compatibility capability")
```

**What the code makes you know:** that the two components might run at different versions. Capability negotiation is compatibility machinery: it exists to cope with version skew.

**The clean form:** pin both components into one stack, installed together, so skew is impossible; delete the negotiation. (Contrast a persistent worker that *refuses* a mismatched build: rejecting is correct; adapting is compatibility.)
**What collapses:** the negotiation and the capability vocabulary.
**Detected by:** "capability" or "compatibility" checks between components in repositories you own.

---

## TIME-5 A test switch in product code

**Seen in:** Toad's comms sidebar.

```python
target = os.environ.pop("TOAD_COMMS_TEST_TARGET", None)
if target:
    self._select(target)          # behaviour that exists only for tests
```

**What the code makes you know:** that production code behaves differently when an environment variable is set.

**The clean form:** tests drive the widget through its real interface. Delete the switch.
**Detected by:** environment variables named `*TEST*` read in `src/`.

---

## TIME-6 Dead modules

**Seen in:** Toad: eight modules nothing imports, including an empty `toad/os.py` shadowing the standard library's `os`.

**What the code makes you know:** whether each is used, which the reader cannot tell without searching.

**The clean form:** confirm it is not launched by name (`python -m …`, an entry point), then delete it with any tests that exist only for it. Git keeps it.
**Detected by:** overlay `dead_modules`.

---

## TIME-7 Copies of another component's defaults

**Seen in:** agent-comms' manual compaction.

```python
SETTINGS = b'{"compaction":{"enabled":false,"reserveTokens":16384,"keepRecentTokens":20000}}'
```

`16384` and `20000` are pi's own defaults, copied. They drift the first time pi changes them.

**The clean form:** the owner of the defaults provides them. Write only what you override (`"enabled": false`), or read the defaults from their owner.
**What collapses:** a silent divergence waiting to happen.
**Detected by:** numeric literals equal to another package's documented defaults; configuration written as a byte or string literal.

---

## TIME-8 Two declarations of one external format

**Seen in:** agent-comms: pi's compaction settings declared by two classes, built by a dict literal, and rebuilt in two JavaScript helpers, six places in all.

```python
@dataclass(frozen=True)
class PiCompactionDecision:
    enabled: bool
    reserve_tokens: int = field(metadata={"wire_name": "reserveTokens"})
    ...

@dataclass(frozen=True)
class CompactionSettings:                      # the same fields, in another module
    enabled: bool
    reserve_tokens: int = field(metadata={"wire_name": "reserveTokens"})
    ...
```

**What the code makes you know:** that six places describe one format, and must agree.

**The clean form:** one record for the external format; anything that adds to it (a decision's trigger) composes it.

```python
@dataclass(frozen=True)
class PiCompactionDecision:
    settings: PiCompactionSettings
    trigger: CompactionTrigger
```

**What collapses:** five of six declarations.
**Detected by:** classes with the same field names and wire names in different modules.

---

## TIME-9 The adapter: the new type on top, the old shape underneath

**Seen in:** agent-comms. Asked to type a reserved input source, an agent produced typed records and then a codec subclass to keep writing the old flat dict:

```python
class SelectedSourceCodec(FieldCodec):
    """Keep the current flat journal contract; internal owners are typed values."""

    @classmethod
    def encode(cls, value):
        if isinstance(value, TextDigest):
            return value.value
        result = super().encode(value)
        if isinstance(value, SelectedSource):
            result.pop("owner")                              # flatten the typed owner back out
            result.update(value.owner.source_fields())       # "ownerName", "ownerCreatedAt"
        return result

    @classmethod
    def project(cls, value, view):                           # the same body again
        ...

    @classmethod
    def _decode(cls, target, data):
        if isinstance(target, type) and issubclass(target, SelectedSource):
            owner = ThreadIncarnation.from_source_fields(data)
            data = {key: value for key, value in data.items() if key not in owner.source_fields()}
            data["owner"] = FieldCodec.encode(owner)          # re-encode, so the parent can decode it
        return super()._decode(target, data)
```

The store it preserved was a journal of in-flight work, which the rules reset at cutover, and "existing journals must read identically" had been revoked in writing. The agent was copying precedent: `main` already had `MessageWireCodec(FieldCodec)` special-casing one type in `_decode`, and `TranscriptCodec` special-casing another. Measured, those precedents had produced two wire forms of one type: `ClaimTransition` encoded with `"admission": null` through the plain codec, and without it through `Message.to_wire()`, and the canonical parser rejected the first.

**What the code makes you know:** that the typed model is not what is stored; that a second representation lives underneath it; that the same type encodes differently depending on which codec touches it. An adapter is a converter that runs forever, and two versions of one thing.

**Why agents write it:** in library code, preserving a contract is diligence, so "keep the current contract" reads as care. Word-based guards do not catch it: that docstring contains none of the banned words.

**The clean form, in three parts:**

1. **Decide the store before touching code.** Runtime state is reset at cutover, so no adapter. Durable state keeps its names by derivation, or moves once through a tool in `tools/cutover/`. An external format gets one declared wire record at that boundary.
2. **A type that owns its wire form declares it,** and the mechanism consults the capability instead of being subclassed:

   ```python
   class WireValue(ABC):
       """A type that owns its wire form; FieldCodec uses it wherever the type appears."""
       __slots__ = ()
       @abstractmethod
       def to_wire(self) -> Any: ...
       @classmethod
       @abstractmethod
       def from_wire(cls, data: Any) -> Self: ...
   ```

3. **Mechanisms are sealed,** so an adapter fails at import rather than in review:

   ```python
   class Sealed:
       """Subclasses may exist only in the defining module."""
       __slots__ = ()
       def __init_subclass__(cls, **kwargs):
           super().__init_subclass__(**kwargs)
           sealed = [b for b in cls.__bases__ if b is not Sealed and issubclass(b, Sealed)]
           if not sealed:
               cls._sealed_home = cls.__module__
           elif cls.__module__ != cls._sealed_home:
               raise TypeError(f"{cls.__qualname__} adapts the sealed mechanism {sealed[0].__qualname__}")
   ```

   Seal mechanisms (codecs, stores' locking, process supervision, request tracking), never extension points (families, tables, lifecycle states): count each shared abstraction's subclasses outside its module, and the split shows itself.

**What collapses:** every per-type codec subclass, and the second wire form each one sustained. In agent-comms, `ClaimTransition` became a `WireValue` and `MessageWireCodec` was deleted; the durable wire's bytes were unchanged, and plain encodings of admission-less transitions became the parseable form.
**Detected by:** the census's `codec_subclass`; the ratchet's `CodecSubclass` measure; import-time failure once the mechanism is sealed. A PR template that requires each changed store to be declared runtime or durable leaves an adapter nowhere to hide.
