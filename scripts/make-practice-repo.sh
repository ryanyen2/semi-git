#!/usr/bin/env bash
# Build the throwaway warm-up repository that the practice sheet works against.
#
#   scripts/make-practice-repo.sh <destination> <git|sgt> [bundle-root]
#
# It is deliberately not one of the two study projects. Ten minutes of practice
# on a study project would teach the participant part of the answer to request
# one, and we would never know how much.
#
# The shapes here match the practice sheet: a function called `total` in
# `cart.py` to revert, a fix to find, and one save that quietly does two things,
# so the participant has met a tangle before it matters.
set -euo pipefail

dest="${1:?usage: make-practice-repo.sh <destination> <git|sgt> [bundle-root]}"
condition="${2:?usage: make-practice-repo.sh <destination> <git|sgt> [bundle-root]}"
bundle_root="${3:-}"

rm -rf "$dest"
mkdir -p "$dest"
cd "$dest"

git init -q .
git config user.name "Practice"
git config user.email "practice@example.org"
git config commit.gpgsign false

commit() {
    git add -A
    GIT_AUTHOR_DATE="$1" GIT_COMMITTER_DATE="$1" git commit -q -m "$2"
}

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
commit "2026-06-02T11:20:00" "add items and a total"

cat > cart.py <<'EOF'
"""A very small shopping cart."""


def add_item(cart, name, price, quantity=1):
    cart.append({"name": name, "price": price, "quantity": quantity})
    return cart


def total(cart):
    return sum(item["price"] * item["quantity"] for item in cart)


def apply_discount(cart, percent):
    for item in cart:
        item["price"] = item["price"] * (100 - percent) / 100
    return cart
EOF
commit "2026-06-04T15:05:00" "discounts"

# The fix to find: totals were coming out with fractions of a penny.
cat > cart.py <<'EOF'
"""A very small shopping cart."""


def add_item(cart, name, price, quantity=1):
    cart.append({"name": name, "price": price, "quantity": quantity})
    return cart


def total(cart):
    return round(sum(item["price"] * item["quantity"] for item in cart), 2)


def apply_discount(cart, percent):
    for item in cart:
        item["price"] = item["price"] * (100 - percent) / 100
    return cart
EOF
commit "2026-06-06T09:40:00" "round the total to whole pennies"

# One save that quietly does two things: the receipt, and a currency change
# nobody mentioned.
cat > cart.py <<'EOF'
"""A very small shopping cart."""

CURRENCY = "GBP"


def add_item(cart, name, price, quantity=1):
    cart.append({"name": name, "price": price, "quantity": quantity})
    return cart


def total(cart):
    return round(sum(item["price"] * item["quantity"] for item in cart), 2)


def apply_discount(cart, percent):
    for item in cart:
        item["price"] = item["price"] * (100 - percent) / 100
    return cart


def receipt(cart):
    lines = [f"{i['quantity']} x {i['name']}  {i['price']:.2f}" for i in cart]
    lines.append(f"total  {total(cart):.2f} {CURRENCY}")
    return "\n".join(lines)
EOF
commit "2026-06-09T14:10:00" "print a receipt"

cat > test_cart.py <<'EOF'
from cart import add_item, apply_discount, receipt, total


def test_total_adds_up():
    cart = add_item([], "apple", 1.25, 2)
    assert total(cart) == 2.50


def test_discount_reduces_the_total():
    cart = add_item([], "apple", 2.00, 1)
    apply_discount(cart, 50)
    assert total(cart) == 1.00


def test_receipt_ends_with_the_total():
    cart = add_item([], "apple", 1.00, 3)
    assert receipt(cart).splitlines()[-1].startswith("total  3.00")
EOF
commit "2026-06-10T10:00:00" "tests"

if [ "$condition" = sgt ] && [ -n "$bundle_root" ] && [ -x "$bundle_root/bin/sgt" ]; then
    # Give the practice copy a real graph, so `sgt now` and `sgt log` have
    # something to show on the very first command. Building it here means the
    # participant's first command in the session is fast.
    "$bundle_root/bin/sgt" init >/dev/null 2>&1 || true
    "$bundle_root/bin/sgt" log --refresh >/dev/null 2>&1 || true
fi

echo "Practice repo at $dest"
