# The pattern catalog

Every antipattern here was found in real agent-written code (agent-comms, and a fork of the Textual app Toad), with the clean form that replaced it. **Agents produce these by default.** Read the catalog before writing a surface receipt, and cite pattern IDs in receipts ("IMPL-2 in `coordination.py`") so the executing agent knows exactly which clean form applies.

## Contents

- [How to read an entry](#how-to-read-an-entry)
- [The rungs](#the-rungs)
- [Index of patterns](#index-of-patterns)
- Files: [implementation.md](implementation.md) · [membership.md](membership.md) · [identity.md](identity.md) · [boundaries.md](boundaries.md) · [over-time.md](over-time.md) · [agent-defaults.md](agent-defaults.md)

## How to read an entry

Each entry has the same parts:

- **The slop:** real code, trimmed. This is what an agent writes when nothing stops it.
- **What the code makes you know:** the fact a reader or maintainer must supply because no declaration states it. That missing declaration is the debt: it is a required answer maintained outside the mechanism.
- **The clean form:** the owned structure, using the shared abstractions (`DeclaredFamily`, `FieldCodec`, `LifecycleState`, `MroDispatch`, `Command`, `TypedTable`, capability mixins).
- **What collapses:** how many edits a new case needs before and after. That count is what separates factoring from relocation.
- **Detected by:** the overlay category, NRA detector, or grep that finds it.

## The rungs

A required answer can live outside its mechanism at three levels. Fix the lowest broken rung first: behaviour cannot be owned by a family whose members cannot be told apart.

| Rung | The question | Typical slop | Clean form |
|---|---|---|---|
| **Identity** | Which thing is this, and which fact is this field? | one concept encoded several ways; one name or field answering two questions; a fact split across stores | one value type per identity; distinct names; state carrying its own data |
| **Membership** | Which things belong to this set? | hand-written rosters, literal sets and name strings restating a family | capabilities on the classes; sets and names derived from the family |
| **Implementation** | What does each case do? | switches on strings, enums or types outside the family | each case owns its behaviour; shared behaviour on the parent; overlapping capabilities composed by multiple inheritance |

Two further groups cut across the rungs: **boundaries** (raw data handled everywhere instead of decoded once), and **duplication over time** (old versions, converters and dead code left beside the new). The last file, **agent defaults**, names the behaviours that produce all of the above.

## From overlay and census findings to patterns

| Finding | Usually | Check also |
|---|---|---|
| `string_dispatch` | IMPL-1, IMPL-6, IMPL-7 | MEMB-2 if the literals are a subset; IMPL-5 if the subject recurs in another function |
| `isinstance_switch` | IMPL-3, IMPL-4 | IMPL-9 when a `kind` field accompanies it |
| census `string_dispatch`, `type_switch` and their `_arms` measures (per function) | IMPL-1, IMPL-2, IMPL-3, IMPL-4 | subjects count candidates with at least three distinct arms; `_arms` also catches growth of existing candidates. Decide per site whether the taxonomy is external or a missing family. Counts do not catch subthreshold cases or additions offset by removals; admitted ownership needs site-specific guards |
| census `builtin_handler_type_switch` | IMPL-3, BOUND-1 | primitive MroDispatch `handles` arms count individually outside the canonical codec, including one per method. AST/domain handlers are not primitive cases; this is screening, not a dynamic import/execution proof |
| census `family_flattened` | BOUND-8 | codec and schema modules that generate wire or SQL forms from a family are the mechanism; everything else is a member leaving its owner as a string |
| `raw_shape` marked BYPASSES | BOUND-2 | MEMB-5 when the subject is a database row |
| `raw_shape` marked HAND-MAPPED | MEMB-5 | read what the target class is: a decoder, or a presentation type restating the wire |
| `raw_shape` unmodeled | BOUND-1 | BOUND-3 nearby; IDEN-5 if keys join two stores |
| `exact_key_set` | BOUND-3 | |
| `named_attribute`, `getattr_default` | BOUND-7 | TIME-3 when the default tolerates old shapes |
| `god_class`, `god_function` | IMPL-8, AGENT-4 | read for IDEN-1 and IMPL-12 inside it |
| `long_condition`, census `boolean_chain_terms` | syntactic **ownership leads**: IDEN-1 (same-value comparisons), IDEN-3 (absence tests on another object), IMPL-10 (own attributes), BOUND-1 (type tests), IMPL-1 (literal comparisons), IMPL-14 (other expressions) | inspect each lead before choosing a target. Predicate calls remain OPEN: they may already query the correct owner. A chain alone does not prove a missing structure |
| `literal_roster` | MEMB-1, MEMB-2, MEMB-3 | |
| `legacy_marker` | TIME-1 to TIME-4 | innocent uses of the words stay, renamed |
| `dead_module` | TIME-6 | |
| census `foreign_absence_probe` | IDEN-3 | the owner should report its state; the probes restate it |
| census `codec_subclass` | TIME-9 | two wire forms of the types the subclass special-cases |
| `positional_insert` | MEMB-5 | |
| `embedded_script` | BOUND-5 | TIME-7 for copied defaults inside it |
| `child_process_site` spread over many modules | IMPL-13 | IDEN-8 |
| census `type_identity_check`, `string_key_subscript` | BOUND-1 | |
| NRA `external_enum_case_recovery` | IMPL-2 | IMPL-11, IMPL-10 |

## Index of patterns

**Implementation** ([implementation.md](implementation.md))
- IMPL-1 String dispatch on a kind
- IMPL-2 Enum with no methods, switched on everywhere
- IMPL-3 A `match` over types that already exist
- IMPL-4 The half-finished family
- IMPL-5 One dispatch written twice
- IMPL-6 Effects decided centrally by key
- IMPL-7 A string action with a parameter bag
- IMPL-8 The undeclared class: closures sharing `nonlocal` state
- IMPL-9 The longhand tagged union
- IMPL-10 Legality by runtime rejection
- IMPL-11 A transition table beside its states
- IMPL-12 One procedure copied, with drift
- IMPL-13 One mechanism reimplemented at different levels of rigour
- IMPL-14 Validation as one anonymous boolean

**Membership** ([membership.md](membership.md))
- MEMB-1 A roster restating a family
- MEMB-2 A roster restating a capability
- MEMB-3 Literal sets and special names spelled in many places
- MEMB-4 Hand-written name strings
- MEMB-5 One record's shape written four times

**Identity** ([identity.md](identity.md))
- IDEN-1 One field answering two questions
- IDEN-2 One name with several meanings
- IDEN-3 One concept, several encodings
- IDEN-4 A name that disagrees with its value
- IDEN-5 One fact split across stores
- IDEN-6 Keyed by the wrong identity
- IDEN-7 A check wider than the question
- IDEN-8 A bare process ID as identity

**Boundaries** ([boundaries.md](boundaries.md))
- BOUND-1 Raw data read at every use, and re-validated
- BOUND-2 Bypassing the class that already models the data
- BOUND-3 Hand-written exact key sets
- BOUND-4 Structure flattened to text, then parsed back
- BOUND-5 Code embedded in strings
- BOUND-6 Configuration read by string path, typed at the call site
- BOUND-7 Access by attribute name
- BOUND-8 An owned fact flattened at its own boundary

**Duplication over time** ([over-time.md](over-time.md))
- TIME-1 A legacy path beside its replacement
- TIME-2 Converters and key renames on load
- TIME-3 Compatibility entry points, aliases and re-exports
- TIME-4 Negotiation between your own components
- TIME-5 A test switch in product code
- TIME-6 Dead modules
- TIME-7 Copies of another component's defaults
- TIME-8 Two declarations of one external format
- TIME-9 The adapter: the new type on top, the old shape underneath

**Agent defaults** ([agent-defaults.md](agent-defaults.md))
- AGENT-1 Keeping old code "for safety"
- AGENT-2 Stopping when the new path works
- AGENT-3 Porting every test, and pinning our own formats
- AGENT-4 Extending the biggest class
- AGENT-5 Copying the local idiom, including across repositories
- AGENT-6 Relocation reported as factoring
- AGENT-7 Holds, freezes and ceremony
