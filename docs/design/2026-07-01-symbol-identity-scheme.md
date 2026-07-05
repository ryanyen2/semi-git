---
date: 2026-07-01
topic: symbol identity for sgt's join key — a canonical, rename-surviving id underneath the file::qualname surface, remapped by rename/move patches and detected through the drift gate
status: design / ADR — proposed; resolves RISK-C and the "Identity scheme" open question of 2026-07-01-intent-patch-algebra-and-recording-lens.md
builds-on:
  - docs/design/2026-07-01-intent-patch-algebra-and-recording-lens.md   # the algebra whose two laws depend on this id
  - docs/design/2026-06-30-contracts-over-git-substrate.md               # provides/requires derived interface
  - Mimram & Di Giusto, "A Categorical Theory of Patches" (Pijul)        # stable vertex identity ⇒ commutation
  - memory/refactor-rename-distill-limitation.md                         # the live failure this fixes: rename ⇒ delete+create ⇒ deps vanish
author-note: written by Claude. [CALL] marks a judgment; [RISK] marks where it can fail. This doc specifies the load-bearing prerequisite for every C-case (toggle / cherry-pick / reorder / version-select) in the intent-patch ADR.
---

# Symbol identity — the stable join key

## 0. Why this is load-bearing

The intent-patch algebra rests on exactly two deterministic laws:

```text
commute(p, q)  ⟺  footprint(p) ∩ footprint(q) = ∅
depends(q, p)  ⟺  q.requires ∩ p.provides ≠ ∅   ∨  same-symbol write-write
```

Both are **set operations over symbols**. Their honesty is therefore *entirely* a function of one
question: **when do two patches refer to the same symbol?** If the answer drifts — if renaming
`login` to `authenticate` makes `q.requires = {auth.login}` stop intersecting
`p.provides = {auth.authenticate}` — then the dependency edge silently vanishes, `commute` reports
a false disjointness, and every C-case built on the algebra (toggle a feature off, cherry-pick it
onto another base, select a version) composes against a lie.

This is not hypothetical. It is the failure already recorded in
`memory/refactor-rename-distill-limitation.md`: today's key is `file::qualname`, which is
**position- and name-derived**, so a rename reads as *delete the old symbol + create a new one*, and
the feature→code map degrades exactly where refactors happen. [CALL] Fixing this is the prerequisite
for the algebra, not an enhancement of it.

## 1. The three tiers (recap, then focus)

| tier | id | mutable? | role |
|---|---|---|---|
| **surface** | `file::qualname` (e.g. `auth/login.py::login`) | yes | human-readable label; display; initial lookup |
| **canonical** *(this doc)* | minted opaque `sym_<short>` | **no** | the actual join key for `commute`/`depends` |
| **content** | hash of the symbol's body | changes every edit | *not* an identity; used only to detect "did this body change" |

The mistake is using the surface tier as the join key. The surface name is a *lookup index into*
the canonical id, the way a filename is an index into an inode. `provides`/`requires` sets are
populated with **canonical ids**; the surface name rides along only for display and matching.

## 2. The canonical id

- **Minted, opaque, immutable.** `sym_<short>` (e.g. `sym_7f3a`). Not derived from position, name,
  or body — so nothing a refactor does can change it.
- **Born** when a symbol first appears in a patch's `provides` (the distiller sees a new top-level
  def/class/method with no predecessor). Minting is the only moment identity is *created*.
- **Bound to the surface name** by a mapping the graph owns: `canonical → current surface name`,
  plus the reverse index `surface name → canonical` used at distill time to resolve a `requires`
  reference to an id.
- **Stored** so it survives git operations: a `Symbol-Id:` entry in the commit trailer alongside
  `Patch-Id:` (the same rebase-safe channel the patch id uses), *and* mirrored in the graph sidecar
  so reads stay offline. [CALL] Trailer is the durable source; sidecar is the cache — same
  git-is-truth discipline as the rest of sgt.

The distiller's contract changes by one line: when it emits a `provides`/`requires` symbol, it
**resolves the surface name to a canonical id through the current mapping** rather than using the
surface string directly. Everything downstream (footprints, the two laws, the gates) operates on
canonical ids and never sees a name.

## 3. `rename` and `move` are the remap mechanism, not conveniences

A `rename`/`move` patch's entire job is to **carry the canonical id across a surface-name change** so
that references keep joining:

```text
rename auth.login -> auth.authenticate
  # effect: mapping[sym_7f3a] : "auth::login"  →  "auth::authenticate"
  # canonical id sym_7f3a is UNCHANGED; every requires/provides that pointed at it still joins.

move   auth/login.py::login -> auth/session.py::login
  # effect: mapping[sym_7f3a] : "auth/login.py::login" → "auth/session.py::login"
  # canonical unchanged; footprint's file dimension updates.
```

Without such a patch, the distiller sees `auth::login` disappear and `auth::authenticate` appear,
mints a *new* canonical id for the latter, and the old dependency edges orphan. **So a rename that
is not recorded as a `rename` patch is precisely the RISK-C failure.** The DSL op is the fix; the
open question is only how the op gets *created*.

## 4. Detection — who authors the rename patch

Two honest paths; [CALL] I recommend (B), because it needs no agent cooperation and reuses the drift
gate the algebra already defines.

**(A) Agent-declared.** The coding agent, having just done the rename, emits
`rename auth.login -> auth.authenticate` alongside its commit. Cleanest signal, zero heuristics —
but only as reliable as the agent's discipline, and non-negotiable for agents that don't cooperate.

**(B) Heuristic proposes, drift gate confirms.** On distill, when a symbol in `provides` *vanished*
and a new one *appeared* in the same commit, run a cheap match:

```text
candidate rename  ⟸  git rename detection (for move across files)
                  ∧  signature / arity match          (structural)
                  ∧  body similarity ≥ θ               (content-hash proximity, not equality)
                  ∧  the vanished symbol had in-force dependents (else nobody cares)
```

A candidate does **not** silently remap. It surfaces through the **drift gate** (the intent-patch
ADR §5): "commit footprint shows `login` removed + `authenticate` added; this looks like a rename of
`sym_7f3a` — accept as `rename`, or record as delete+create?" The user (or an auto-policy) confirms,
and confirmation *is* the `rename` patch. This turns identity maintenance into the same
verify-and-adjust loop as everything else, instead of a separate trusted heuristic that can be
wrong invisibly.

[RISK] Threshold θ and the signature-match rule are where false positives/negatives live. A false
*positive* (two unrelated symbols merged) is worse than a false *negative* (a real rename read as
delete+create) — the former corrupts the join silently, the latter merely degrades to today's
behavior and is visible. So the gate must **bias toward asking** and default to delete+create when
uncertain.

## 5. Worked example — a dependency surviving a rename

```text
p1  provides {sym_7f3a}     surface "auth::login"          "add login endpoint"
p2  requires {sym_7f3a}     "add rate-limiting to login"   → depends(p2, p1)   ✓ (7f3a ∩ 7f3a)

r   rename auth.login -> auth.authenticate                 # mapping[sym_7f3a] → "auth::authenticate"
    # p1.provides still {sym_7f3a}; p2.requires still {sym_7f3a}

p2  requires {sym_7f3a}     → depends(p2, p1)   STILL ✓    # the edge did NOT vanish
branch minimal = all - p2                                  # toggle rate-limit off — still well-defined
```

Contrast without `r`: distill would have rewritten `p1` to `provides {sym_NEW}` and left `p2`
requiring the now-orphaned `sym_7f3a` → `depends(p2,p1)` evaporates → `all - p2` composes as if p2
never needed p1 → possible silent break. The single remap line is what keeps the whole C-case honest.

## 6. Interaction with the rest of the system

- **Footprint / distill:** unchanged except the name→id resolution step (§2). Footprint sets become
  sets of canonical ids.
- **Blame / attribution:** line→symbol blame now resolves to a canonical id, so blame is *stable
  across renames for free* — a latent win over `file::qualname` blame.
- **`decompose` / `merge` patches:** splitting a patch doesn't touch symbol ids (a symbol keeps its
  id regardless of which patch provides it); merging two patches unions their provide-sets of ids.
- **Contract identity** is the same pattern one level up (minted `contract` id, mutable `on <name>`
  surface) and is out of scope here — noted so the two aren't conflated.

## 7. Failure modes / open

- **[RISK] Split/merge of a symbol itself** (extract half of `login` into a new function) is *not* a
  rename — it's one id spawning two. Needs a `split-symbol` provenance edge or it looks like
  rename+create. Deferred; flag it rather than let the heuristic guess.
- **[RISK] Cross-language / non-Python** symbols have no `ast` qualname; the surface tier needs a
  language-agnostic locator (path + tree-sitter node path) before this generalizes. Canonical tier
  is language-agnostic already (it's just a minted id).
- **Open — where minting lives.** Distiller vs. a dedicated identity pass. Leaning distiller, since
  it already walks the diff and is the only place a "new symbol" is first observed.
- **Open — trailer vs. anchor comment.** Trailer survives rebase but not `git blame` of the source
  line; an in-source anchor comment is invasive but travels with the code. Trailer + sidecar is the
  §2 default; revisit if blame-locality matters.

## 8. Decision

**Proposed.** Introduce a minted canonical symbol id as the join key, demote `file::qualname` to a
surface lookup, and make `rename`/`move` patches the remap mechanism — created via the drift gate
(path B) so identity maintenance is a confirm-loop, not a trusted heuristic. This resolves RISK-C of
the intent-patch ADR and is the prerequisite for shipping any C-case. Additive over the current
store: the mapping and trailer are new; nothing is deleted until the canonical id is the join key
everywhere.
