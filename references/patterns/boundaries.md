# Boundaries: raw data handled everywhere instead of decoded once

Data crossing a boundary (a socket, a file, a database row, a subprocess's output, a user's settings) should be decoded once, where it enters, into a type; everything after trusts the type. Every pattern here handles the raw form at each use instead.

## Contents

BOUND-1 Raw data read at every use, and re-validated · BOUND-2 Bypassing the class that already models the data · BOUND-3 Hand-written exact key sets · BOUND-4 Structure flattened to text, then parsed back · BOUND-5 Code embedded in strings · BOUND-6 Configuration read by string path, typed at the call site · BOUND-7 Access by attribute name

---

## BOUND-1 Raw data read at every use, and re-validated

**Seen in:** agent-comms' feature modules: 79 record shapes read by three or more string keys; 249 `type()` checks, 168 of them on a value just taken out of a dict.

```python
data = json.loads(stdout)
session_id = data["sessionId"]
if type(session_id) is not str or not session_id:
    raise HelperError("sessionId")
session_file = data["sessionFile"]
if type(session_file) is not str:
    raise HelperError("sessionFile")
```

**What the code makes you know:** the record's shape, restated at every site that reads it, with its validation repeated each time.

```python
@dataclass(frozen=True)
class ReopenResult:
    session_id: str = field(metadata={"wire_name": "sessionId"})
    session_file: Path = field(metadata={"wire_name": "sessionFile"})

result = FieldCodec.decode(ReopenResult, json.loads(stdout))   # strict: unknown or missing keys fail here
```

**What collapses:** every `type()` check and string-keyed read after the boundary.
**Detected by:** the ratchet's `type_is` and `str_key`; overlay `raw_shapes`.

---

## BOUND-2 Bypassing the class that already models the data

**Seen in:** agent-comms' private N/K stores; Toad's ACP agent.

```python
row = conn.execute("SELECT * FROM prompt_bindings WHERE ...").fetchone()
binding_id = row["binding_id"]                     # 13 columns read by hand, while
input_id = row["input_id"]                         # a PromptBinding class declares 12 of them
...

coordination = update["coordination"]              # Toad: 12 keys read raw, while
title = coordination.get("autoTitle")              # acp/messages.py already has CoordinationUpdate
```

**What the code makes you know:** that a type exists for this data, which nothing at the reading site mentions.

```python
binding = PROMPT_BINDINGS.select_one(conn, "binding_id = ?", (binding_id,))   # a PromptBinding
update = FieldCodec.decode(CoordinationUpdate, payload["coordination"])
```

**What collapses:** the hand mapping; the class becomes the only statement of the shape.
**Detected by:** overlay `raw_shape` marked **BYPASSES**. A shape marked **HAND-MAPPED** is different: the function builds the matching class from the keys it reads. Check what that class is. In the Toad fork, `CoordinationUpdate` looked like a model being bypassed and was a Textual UI message whose fields copy the wire's: the wire had no type at all, and the fix is MEMB-5's (one declaration, carried by the message).

---

## BOUND-3 Hand-written exact key sets

**Seen in:** 31 checks in 21 agent-comms modules.

```python
if set(data) != {"sessionId", "sessionFile"}:
    raise HelperError("unexpected keys")
```

**What the code makes you know:** the record's fields, written as a set literal beside the code that then reads them one by one.

**The clean form:** a strict codec rejects unknown and missing keys by construction (BOUND-1).
**Detected by:** overlay `exact_key_sets`.

---

## BOUND-4 Structure flattened to text, then parsed back

**Seen in:** Toad's status line.

```python
parts.append(presentation.summary.partition("\n")[0] or "Ready")
```

**What the code makes you know:** that the first line of a prose summary is its headline. The structure existed upstream and was flattened into text; the view reconstructs it with string surgery.

```python
@dataclass(frozen=True)
class Presentation:
    headline: str
    details: str
```

**What collapses:** the parsing, and its silent failure when the prose changes shape.
**Detected by:** `split`, `partition` or regular expressions applied to values your own code produced.

---

## BOUND-5 Code embedded in strings

**Seen in:** agent-comms' compaction: about 10,000 characters of JavaScript in Python strings, each helper's output parsed by hand.

```python
_SNAPSHOT_JS = """
const { SessionManager } = await import(pkg);
const settings = { ...DEFAULT_COMPACTION_SETTINGS, keepRecentTokens: Number(recent) };
if (!Number.isSafeInteger(settings.keepRecentTokens)) throw new Error("bounds");
process.stdout.write(JSON.stringify({ status, sessionId, leafId, ... }));
"""
out = subprocess.run(["node", "--input-type=module", "-e", _SNAPSHOT_JS], ...)
```

**What the code makes you know:** everything about the helper, since no tool checks, lints or measures code held in a string; and its settings validation duplicates the Python side's.

```python
class PiHelper(ABC, Generic[Request, Result]):
    script: ClassVar[str]                 # a real .mjs file shipped as package data
    result: ClassVar[type[Result]]
    def run(self, request: Request, *, deadline: float) -> Result:
        ...                               # A12 BoundedRun, A2 encode in, strict A2 decode out

class SourceSnapshotHelper(PiHelper[SnapshotRequest, SnapshotResult]):
    script = "helpers/source_snapshot.mjs"
    result = SnapshotResult
```

**What collapses:** hidden code, per-helper process handling and parsing, and duplicated validation.
**Detected by:** overlay `embedded_js`.

---

## BOUND-6 Configuration read by string path, typed at the call site

**Seen in:** Toad: 45 reads of 31 settings across 13 modules.

```python
expand = self.app.settings.get("tools.expand", str, expand=False)
if self.app.settings.get("sidebar.hide", bool): ...
```

**What the code makes you know:** each setting's path and type, restated at every read; a typo is a silent default. The schema declares the type as a string, and each caller declares it again as a Python type.

```python
class ToolSettings(SettingsGroup):
    expand = ChoiceSetting(ExpansionPolicy, default=FailExpansion)

if app.settings.tools.expand.should_expand(status): ...     # typed; a typo fails the type checker
```

**What collapses:** the string paths and the type arguments at every read.
**Detected by:** grep `settings.get(` with a string literal.

---

## BOUND-7 Access by attribute name

**Seen in:** the Toad fork: 69 `getattr`/`hasattr` calls with literal names, 27 `getattr(obj, name, default)` calls in `Conversation` alone. And in this skill's own first scripts (AGENT-8).

```python
root = getattr(self.app, "_coordination_root", None)      # someone else's private attribute
if hasattr(widget, "session_id"):                         # duck typing across a boundary
    ...
```

**What the code makes you know:** which objects have which attributes, and what the default means when one is missing. A `getattr` with a default quietly tolerates objects of other shapes, which is compatibility by another name.

```python
class HasSession(Protocol):
    session_id: SessionId

def open_thread(target: HasSession) -> None: ...          # the requirement is declared, and checked
```

For external structures such as Python's own AST, destructure with `match` on the real classes (`case ast.Call(func=ast.Name(id="type"))`) instead of probing attributes.
**What collapses:** the probing, the defaults, and the mystery of which shapes flow where.
**Detected by:** the census's `attr_by_name` and `getattr_default`.
