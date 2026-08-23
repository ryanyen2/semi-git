#!/usr/bin/env bash
# Build the throwaway warm-up repository that the practice sheet works against.
#
#   scripts/make-practice-repo.sh <destination> <git|sgt> [bundle-root]
#
# It is deliberately not one of the two study projects. Ten minutes of practice
# on a study project would teach the participant part of the answer to request
# one, and we would never know how much.
#
# It is deliberately bigger than one file. The first version was a single
# `cart.py`, which clustered into exactly one feature -- so the practice sheet
# said "take one feature out without disturbing the others" against a repo that
# had no others, and the map the participant met had one row in it. Everything
# the sheet claims has to be visible here, which means several features, a
# commit that quietly does two things, a regression with its later fix, a
# feature that was added and then dropped, and a commit that touches no code at
# all.
#
# Every handle the practice sheet quotes is real and is checked at the bottom of
# this script. If you change the shapes here, run it and read what it prints.
set -euo pipefail

dest="${1:?usage: make-practice-repo.sh <destination> <git|sgt> [bundle-root]}"
condition="${2:?usage: make-practice-repo.sh <destination> <git|sgt> [bundle-root]}"
bundle_root="${3:-}"
SGT_SOURCE_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

rm -rf "$dest"
mkdir -p "$dest"
# Absolute from here on: everything below `cd`s into it and several later lines
# still pass `$dest` as a path, which would resolve to `$dest/$dest` otherwise.
dest="$(cd "$dest" && pwd)"
cd "$dest"

git init -q .
git config user.name "Practice"
git config user.email "practice@example.org"
git config commit.gpgsign false

# Ignore Python build artifacts, the way both study projects' own .gitignore
# does. Written to .git/info/exclude rather than to a committed .gitignore
# because every commit below has a pinned date and so a pinned sha, and the
# practice sheet quotes one of them (`44da4ad`) verbatim -- adding a file to the
# first commit renumbers every sha after it.
#
# Without this the sheet breaks in two places, and only for a participant who
# ran the tests before their first save, which the repo's own README invites.
# The stray `.pyc` files are untracked, so the next `sgt save` or `sgt revert`
# sweeps them into its commit; from then on every test run dirties the tree.
# Step 5's one-line README edit then warns "one save touched 9 features" in a
# four-feature repo, and step 6's `sgt undo` -- the back half of the sequence
# the sheet calls the most useful thing in these ten minutes -- refuses with
# "put() would overwrite uncommitted changes" over a list of .pyc files and
# offers `sgt advanced resync`, which is a remedy for something else.
printf '__pycache__/\n*.pyc\n.pytest_cache/\n' >> .git/info/exclude

commit() {
    git add -A
    GIT_AUTHOR_DATE="$1" GIT_COMMITTER_DATE="$1" git commit -q -m "$2"
}

# --- 1. the cart ----------------------------------------------------------

cat > README.md <<'EOF'
# cart

A shopping cart, for practising on. Nothing here counts.
EOF
commit "2026-06-01T09:00:00" "start the cart"

cat > cart.py <<'EOF'
"""A very small shopping cart."""


def add_item(cart, name, price, quantity=1):
    cart.append({"name": name, "price": price, "quantity": quantity})
    return cart


def total(cart):
    return sum(item["price"] * item["quantity"] for item in cart)
EOF
commit "2026-06-02T11:20:00" "a cart you can add things to"

cat > cart.py <<'EOF'
"""A very small shopping cart."""


def add_item(cart, name, price, quantity=1):
    cart.append({"name": name, "price": price, "quantity": quantity})
    return cart


def remove_item(cart, name):
    return [item for item in cart if item["name"] != name]


def total(cart):
    return sum(item["price"] * item["quantity"] for item in cart)
EOF
cat > test_cart.py <<'EOF'
from cart import add_item, remove_item, total


def test_total_adds_up():
    cart = add_item([], "apple", 1.25, 2)
    assert total(cart) == 2.50


def test_remove_item_takes_it_out():
    cart = add_item(add_item([], "apple", 1.00), "pear", 2.00)
    assert total(remove_item(cart, "apple")) == 2.00
EOF
commit "2026-06-03T10:15:00" "remove things from the cart"

# The fix to find: totals were coming out with fractions of a penny.
cat > cart.py <<'EOF'
"""A very small shopping cart."""


def add_item(cart, name, price, quantity=1):
    cart.append({"name": name, "price": price, "quantity": quantity})
    return cart


def remove_item(cart, name):
    return [item for item in cart if item["name"] != name]


def total(cart):
    return round(sum(item["price"] * item["quantity"] for item in cart), 2)
EOF
cat >> test_cart.py <<'EOF'


def test_total_rounds_to_whole_pennies():
    cart = add_item([], "apple", 0.333, 3)
    assert total(cart) == 1.00
EOF
commit "2026-06-05T09:40:00" "round the total to whole pennies"

# --- 2. discounts ---------------------------------------------------------

cat > discount.py <<'EOF'
"""Discounts you can apply to a cart."""


def percent_off(cart, percent):
    for item in cart:
        item["price"] = round(item["price"] * (100 - percent) / 100, 2)
    return cart
EOF
cat > test_discount.py <<'EOF'
from cart import add_item, total
from discount import percent_off


def test_percent_off_reduces_the_total():
    cart = add_item([], "apple", 2.00)
    percent_off(cart, 50)
    assert total(cart) == 1.00
EOF
commit "2026-06-08T14:05:00" "percentage discounts"

cat > discount.py <<'EOF'
"""Discounts you can apply to a cart."""

COUPONS = {"WELCOME10": 10, "SUMMER25": 25}


def percent_off(cart, percent):
    for item in cart:
        item["price"] = round(item["price"] * (100 - percent) / 100, 2)
    return cart


def coupon(cart, code):
    percent = COUPONS.get(code.upper())
    if percent is None:
        raise ValueError(f"no such coupon: {code}")
    return percent_off(cart, percent)
EOF
cat >> test_discount.py <<'EOF'


def test_coupon_applies_its_percent():
    from discount import coupon

    cart = add_item([], "apple", 10.00)
    coupon(cart, "welcome10")
    assert total(cart) == 9.00


def test_unknown_coupon_is_an_error():
    import pytest
    from discount import coupon

    with pytest.raises(ValueError):
        coupon(add_item([], "apple", 1.00), "NOPE")
EOF
commit "2026-06-11T16:30:00" "coupon codes"

# --- 3. the tangled one ---------------------------------------------------
#
# The receipt, and the first place money is formatted at all.
#
# This used to be the repo's tangled save -- in the one-file version the same
# commit added a `receipt()` to `cart.py` AND a `CURRENCY` constant nobody asked
# for. It is not tangled any more: it writes one new file. The tangle the sheet
# actually points at is "charge shipping per item", below, which silently drops
# the free-over-fifty rule while its message talks only about per-item pricing.

cat > receipt.py <<'EOF'
"""Turning a cart into something you can print."""

from cart import total

CURRENCY = "GBP"
SYMBOL = "£"


def format_money(amount):
    return SYMBOL + "%.2f" % amount


def format_receipt(cart):
    lines = [
        "%d x %s  %s" % (i["quantity"], i["name"], format_money(i["price"])) for i in cart
    ]
    lines.append("total  " + format_money(total(cart)))
    return "\n".join(lines)
EOF
commit "2026-06-15T11:45:00" "print a receipt"

cat > test_receipt.py <<'EOF'
from cart import add_item
from receipt import format_money, format_receipt


def test_receipt_ends_with_the_total():
    cart = add_item([], "apple", 1.00, 3)
    assert format_receipt(cart).splitlines()[-1].startswith("total")


def test_money_has_two_decimals():
    assert format_money(3) == "£3.00"
EOF
commit "2026-06-16T09:20:00" "receipt tests"

# --- 4. shipping, its regression, and its fix ------------------------------

cat > shipping.py <<'EOF'
"""What it costs to post an order."""

FLAT_RATE = 4.99


def shipping_cost(cart):
    return FLAT_RATE
EOF
cat > test_shipping.py <<'EOF'
from cart import add_item
from shipping import shipping_cost


def test_flat_rate_for_a_small_order():
    assert shipping_cost(add_item([], "apple", 1.00)) == 4.99
EOF
commit "2026-06-19T13:10:00" "flat rate shipping"

cat > shipping.py <<'EOF'
"""What it costs to post an order."""

from cart import total

FLAT_RATE = 4.99
FREE_OVER = 50.00


def shipping_cost(cart):
    if total(cart) >= FREE_OVER:
        return 0.0
    return FLAT_RATE
EOF
cat >> test_shipping.py <<'EOF'


def test_free_over_fifty():
    assert shipping_cost(add_item([], "chair", 60.00)) == 0.0
EOF
commit "2026-06-22T10:00:00" "free shipping over fifty"

# Added...
cat > cart.py <<'EOF'
"""A very small shopping cart."""

GIFT_WRAP_PRICE = 2.50


def add_item(cart, name, price, quantity=1):
    cart.append({"name": name, "price": price, "quantity": quantity})
    return cart


def remove_item(cart, name):
    return [item for item in cart if item["name"] != name]


def gift_wrap(cart):
    return add_item(cart, "gift wrapping", GIFT_WRAP_PRICE)


def total(cart):
    return round(sum(item["price"] * item["quantity"] for item in cart), 2)
EOF
cat >> test_cart.py <<'EOF'


def test_gift_wrapping_costs_extra():
    from cart import gift_wrap

    assert total(gift_wrap(add_item([], "apple", 1.00))) == 3.50
EOF
commit "2026-06-25T15:35:00" "gift wrapping"

# The regression. The message says one thing about per-item pricing and says
# nothing about having dropped the free-over-fifty rule on the way past.
cat > shipping.py <<'EOF'
"""What it costs to post an order."""

FLAT_RATE = 4.99
PER_ITEM = 1.50


def shipping_cost(cart):
    return round(FLAT_RATE + PER_ITEM * (len(cart) - 1), 2)
EOF
cat > test_shipping.py <<'EOF'
from cart import add_item
from shipping import shipping_cost


def test_flat_rate_for_a_small_order():
    assert shipping_cost(add_item([], "apple", 1.00)) == 4.99


def test_extra_items_cost_more():
    cart = add_item(add_item([], "apple", 1.00), "pear", 1.00)
    assert shipping_cost(cart) == 6.49
EOF
commit "2026-06-29T11:05:00" "charge shipping per item"

# ...and dropped again.
cat > cart.py <<'EOF'
"""A very small shopping cart."""


def add_item(cart, name, price, quantity=1):
    cart.append({"name": name, "price": price, "quantity": quantity})
    return cart


def remove_item(cart, name):
    return [item for item in cart if item["name"] != name]


def total(cart):
    return round(sum(item["price"] * item["quantity"] for item in cart), 2)
EOF
cat > test_cart.py <<'EOF'
from cart import add_item, remove_item, total


def test_total_adds_up():
    cart = add_item([], "apple", 1.25, 2)
    assert total(cart) == 2.50


def test_remove_item_takes_it_out():
    cart = add_item(add_item([], "apple", 1.00), "pear", 2.00)
    assert total(remove_item(cart, "apple")) == 2.00


def test_total_rounds_to_whole_pennies():
    cart = add_item([], "apple", 0.333, 3)
    assert total(cart) == 1.00
EOF
commit "2026-07-02T09:50:00" "drop gift wrapping, nobody used it"

# The correction, keeping what the regression was actually for.
cat > shipping.py <<'EOF'
"""What it costs to post an order."""

from cart import total

FLAT_RATE = 4.99
PER_ITEM = 1.50
FREE_OVER = 50.00


def shipping_cost(cart):
    if total(cart) >= FREE_OVER:
        return 0.0
    return round(FLAT_RATE + PER_ITEM * (len(cart) - 1), 2)
EOF
cat >> test_shipping.py <<'EOF'


def test_free_over_fifty_still_applies():
    assert shipping_cost(add_item([], "chair", 60.00)) == 0.0
EOF
commit "2026-07-06T14:25:00" "free shipping over fifty again"

# --- 5. a commit that touches no code at all ------------------------------

cat > README.md <<'EOF'
# cart

A shopping cart, for practising on. Nothing here counts.

## What is in here

- `cart.py` — adding things, removing things, and the total.
- `discount.py` — a percentage off, or a coupon code.
- `receipt.py` — turning a cart into something you can print.
- `shipping.py` — what it costs to post an order.

## Tests

```
python -m pytest -q
```
EOF
commit "2026-07-09T10:30:00" "explain what is in here"

cat > receipt.py <<'EOF'
"""Turning a cart into something you can print."""

from cart import total

CURRENCY = "GBP"
SYMBOL = "£"


def format_money(amount):
    return "%s%.2f" % (SYMBOL, amount)


def format_receipt(cart):
    lines = [
        "%d x %s  %s" % (i["quantity"], i["name"], format_money(i["price"])) for i in cart
    ]
    lines.append("total  " + format_money(total(cart)))
    return "\n".join(lines)
EOF
commit "2026-07-13T16:00:00" "tidy up the money formatting"

# --- 6. the history view --------------------------------------------------

if [ "$condition" = sgt ] && [ -n "$bundle_root" ] && [ -x "$bundle_root/bin/sgt" ]; then
    sgt_bin="$bundle_root/bin/sgt"
    python_bin="$bundle_root/toolenv/bin/python"

    # Give the practice copy a real graph, so `sgt now` and `sgt log` have
    # something to show on the very first command, and so the participant's
    # first command in the session is fast rather than a thirty second wait.
    "$sgt_bin" init >/dev/null 2>&1 || true
    "$sgt_bin" log --refresh >/dev/null 2>&1 || true

    # Pin the feature names the practice sheet quotes.
    #
    # Without this the names come from an LLM call, which means they are neither
    # stable between builds nor present at all when the key is missing -- and a
    # missing key is not hypothetical: it is what shipped, and it is why the
    # practice repo showed one feature called `add_item apply_discount…` instead
    # of anything a person would recognise. A sheet that says `sgt show
    # "Shipping"` has to be right on every machine, so the names are pinned here
    # rather than hoped for. A pin is durable (`.sgt/pins/pins.json`) and
    # survives the participant's own re-clustering.
    pin_status=0
    if [ -x "$python_bin" ]; then
        "$sgt_bin" log --map --json > .sgt-map.json 2>/dev/null || true
        # The binary is passed in, not guessed from the layout. Guessing it found
        # whatever `sgt` happened to be on PATH, which renamed a different repo's
        # graph and left this one untouched while still printing success.
        "$python_bin" - "$dest" "$sgt_bin" > .sgt-names.txt <<'PY' || pin_status=$?
import json, pathlib, subprocess, sys

repo = pathlib.Path(sys.argv[1])
sgt = sys.argv[2]

# A feature is identified by a commit subject only it can own. Matching on the
# label would defeat the point, since the label is the thing that is
# unreliable. Matching on a symbol was the obvious idea and is wrong: a feature
# can end up holding only bookkeeping sentinels and report zero symbols while
# still owning four saves, so a symbol match silently skips it.
# Several candidates per label, newest-first, because `sgt show` returns only
# the most recent handful of saves and a feature's oldest save may not be in
# the payload at all.
WANTED = [
    ("Shipping", ["free shipping over fifty again", "charge shipping per item",
                  "free shipping over fifty", "flat rate shipping"]),
    ("Discounts", ["coupon codes", "percentage discounts"]),
    ("Receipts", ["tidy up the money formatting", "receipt tests", "print a receipt"]),
    ("The Cart", ["drop gift wrapping, nobody used it", "gift wrapping",
                  "round the total to whole pennies", "remove things from the cart",
                  "a cart you can add things to"]),
]

def run(*argv):
    return subprocess.run([sgt, *argv], cwd=repo, capture_output=True, text=True, timeout=300)


try:
    m = json.loads((repo / ".sgt-map.json").read_text())
except Exception:
    sys.stderr.write("no map to pin names against\n")
    sys.exit(1)

subjects = {}
for fid in m.get("features", {}):
    try:
        subjects[fid] = {s.get("subject", "")
                         for s in json.loads(run("show", fid, "--json").stdout).get("saves", [])}
    except Exception:
        subjects[fid] = set()

taken = set()
for label, candidates in WANTED:
    for fid, subs in subjects.items():
        if fid in taken or not subs.intersection(candidates):
            continue
        if run("feature", "rename", fid, label).returncode == 0:
            taken.add(fid)
            print("%s\t%s" % (label, fid))
        break

# Read it back. A rename that silently did nothing is the failure this whole
# block exists to prevent, and it looks exactly like success from here.
tree = run("log", "--tree", "--no-color").stdout
missing = [label for label, _ in WANTED if label not in tree]
if missing:
    sys.stderr.write("names that did not stick: %s\n" % ", ".join(missing))
    sys.exit(1)
PY
        rm -f .sgt-map.json
    fi

    # The search index, so `sgt find "…"` in the practice repo answers on
    # meaning rather than on word overlap. Built here for the same reason it is
    # built for the study project: on first use it costs the participant thirty
    # seconds of their ten minutes.
    if [ -x "$python_bin" ]; then
        "$python_bin" -c "
from sgt.lens.search import build_index
build_index('$dest')
" >/dev/null 2>&1 || true
    fi

    if [ "$pin_status" -eq 0 ]; then
        echo "  Pinned feature names:"
        sed 's/^/    /' .sgt-names.txt
    else
        echo >&2
        echo "  WARNING: the practice feature names did not stick. The practice sheet" >&2
        echo "  quotes them literally (\`sgt show \"Shipping\"\`), so it is now wrong." >&2
        echo "  Check what \`sgt log --tree\` prints in $dest before running a session." >&2
        echo >&2
    fi
    rm -f .sgt-names.txt

    # These three checks FAIL the build rather than warn.
    #
    # They used to echo to stderr and exit 0, so a bundle whose warm-up repo had
    # a degenerate graph, unpinned names and seven dead handles shipped silently
    # -- on the repository the participant meets first, and in a build log the
    # facilitator has no reason to re-read. The work repo has been hard-gated
    # since the beginning; there was no argument for the practice repo being
    # softer, only an accident of the order they were written in.
    fail=0

    # The graph itself, before any of its handles. A build can produce an ideal
    # missing most of its symbols, and every handle check below would then fail
    # for a reason that reads like a naming problem.
    if [ ! -x "$python_bin" ]; then
        echo "  no interpreter at $python_bin, so the graph could not be checked." >&2
        fail=1
    elif ! "$python_bin" "$SGT_SOURCE_SCRIPTS/check_graph_integrity.py" "$dest"; then
        fail=1
    fi
    [ "$pin_status" -eq 0 ] || fail=1

    # Every handle the practice sheet quotes, checked here rather than found
    # wrong by a participant with a facilitator watching. Add a line whenever
    # the sheet gains an example.
    #
    # `The Cart@2` and `44da4ad` are here because the sheet types them verbatim
    # and neither is stable by construction. `@n` is a positional counter over a
    # feature's chapters, and the chapter cut is the one part of this build an
    # LLM can move: with a key, `.sgt/intent/segments.json` is written from the
    # model rather than the deterministic fallback, so a build machine can cut
    # The Cart into two chapters where this one cuts three -- and the sheet's
    # last revert example would then resolve to nothing, at the end of the
    # exercise the practice sheet calls the most useful ten minutes in it.
    for handle in "The Cart" "Discounts" "Receipts" "Shipping" \
                  "cart.py::total" "shipping.py::shipping_cost" "receipt.py::format_money" \
                  "The Cart@2" "44da4ad"; do
        if ! "$sgt_bin" show "$handle" --json 2>/dev/null | grep -q '"ok": true'; then
            echo "  the practice sheet quotes \`$handle\` and it does not resolve." >&2
            fail=1
        fi
    done

    if [ "$fail" -ne 0 ]; then
        echo >&2
        echo "  Refusing to ship this practice copy. It is the first ten minutes of the" >&2
        echo "  session, and the sheet quotes these by name." >&2
        exit 1
    fi
    echo "  Every handle the practice sheet quotes resolves."
elif [ "$condition" = sgt ]; then
    # Asked for the sgt condition and there is no sgt to build it with.
    #
    # This used to fall through to the success line, so `make-practice-repo.sh
    # <dest> sgt` with no bundle root printed "Practice repo at <dest>" over a
    # plain git repository with no `.sgt` in it and no feature names -- the one
    # thing the sgt practice sheet is entirely about. Both real callers pass the
    # bundle root; the case that hit this is a facilitator or a maintainer running
    # the script by hand, which is exactly when a silent wrong answer costs most.
    #
    # The binary is not guessed from PATH on purpose. See the note above the pin
    # step: guessing found whatever `sgt` happened to be installed and renamed a
    # different repository's graph while reporting success here.
    echo >&2
    if [ -z "$bundle_root" ]; then
        echo "  No bundle root given, so there is no sgt to build the graph with." >&2
    else
        echo "  No executable at $bundle_root/bin/sgt, so there is no sgt to build" >&2
        echo "  the graph with." >&2
    fi
    echo "  Pass the bundle root as the third argument. Without it this is a plain git" >&2
    echo "  repository, and the sgt practice sheet quotes feature names that only exist" >&2
    echo "  once the graph is built and pinned." >&2
    exit 1
fi

echo "Practice repo at $dest"
