"""Automated hollow fulfillment for rewrite drafts (plan: semantic repair loop).

`sgt/core/rewrite.py`'s verbs (chiefly `revert_keep_dependents`) draft hollow ops naming exactly
the symbols a human must rewrite by hand today. This package automates that fulfillment: a
pluggable `RepairBackend` (`backends.py`) proposes new bytes for a hollow, a free static check
(`verify.py`) rejects an obviously-broken proposal before spending an oracle run, and `loop.py`
drives the whole thing through the existing `stage -> oracle -> land` gate, unchanged.
"""
