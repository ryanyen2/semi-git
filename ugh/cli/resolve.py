"""`ugh resolve <address>`: the two versions of each pending two-version choice at an address.

A sync that found one definition changed differently on both sides kept ours in the file and left
a choice (`ugh.core.sync`). Without a flag this prints ours (the file now) beside theirs (the
merge's second parent). `--theirs` writes theirs, `--ours` keeps ours, `--apply` keeps whatever the
file now holds (an edit by hand); each then commits with one `Ugh-Resolves:` trailer per unit. The
project's check does not gate it.
"""

from __future__ import annotations

from ._common import _emit_json, _fail_json


def register(subs, parent) -> None:
    p = subs.add_parser("resolve", parents=[parent])
    p.add_argument("address")
    keep = p.add_mutually_exclusive_group()
    keep.add_argument("--theirs", dest="keep", action="store_const", const="theirs",
                      help="write theirs and record the choice")
    keep.add_argument("--ours", dest="keep", action="store_const", const="ours",
                      help="keep ours and record the choice")
    keep.add_argument("--apply", dest="keep", action="store_const", const="apply",
                      help="keep what the file now holds and record the choice")
    p.set_defaults(func=_cmd_resolve)


def _cmd_resolve(args) -> int:
    return _resolve(".", args.address, args.keep, args.as_json)


def _text(data: bytes | None) -> str | None:
    return None if data is None else data.decode("utf-8", "replace")


def _resolve(repo: str, address: str, keep: str | None, as_json: bool) -> int:
    from ugh.address import AddressError, resolve
    from ugh.core import sync
    from ugh.core.lens import DirtyWorkingTreeError, get
    from ugh.store.gitbind import GitError

    from .ideal_edit import address_refusal

    get(repo)
    pending = sync.choices(repo)
    try:
        units = {st.unit for st in resolve(repo, address)}
    except AddressError as e:
        return address_refusal("resolve", address, e, as_json)
    picked = [c for c in pending if c.unit in units]
    if not picked:
        listed = f" -- pending: {', '.join(c.address for c in pending)}" if pending else ""
        return _fail_json(f"no two-version choice at {address!r}{listed}", as_json)

    if keep is None:
        rows = [{"address": c.address, "merge": c.merge, "ours": _text(o), "theirs": _text(t)}
                for c in picked for o, t in [sync.versions(repo, c)]]
        if as_json:
            return _emit_json({"ok": True, "choices": rows})
        for row in rows:
            print(row["address"])
            for side, label in (("ours", "ours (the file now)"),
                                ("theirs", f"theirs (from {row['merge'][:7]}'s second parent)")):
                print(f"  {label}:")
                text = row[side]
                print("\n".join(f"    {line}" for line in text.rstrip("\n").splitlines())
                      if text is not None else "    (absent)")
        print(f"\n  ugh resolve {address} --theirs | --ours | --apply (after editing the file)")
        return 0

    try:
        sha = sync.resolve(repo, picked, keep, address)
    except (DirtyWorkingTreeError, GitError) as e:
        return _fail_json(str(e), as_json)
    resolved = [c.address for c in picked]
    if as_json:
        return _emit_json({"ok": True, "commit": sha, "kept": keep, "resolved": resolved})
    print(f"✓ resolved {', '.join(resolved)} ({keep}) — {sha[:12]}")
    return 0
