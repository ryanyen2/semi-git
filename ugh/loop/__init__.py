"""The agentic loop: plan intake, checkpoint matching, drift review (plan U14, R18/R21).

A plan (an agent's or human's stated intent before doing the work) decomposes into predicted
hollow ops (`plan.intake`); real commits are matched against those predictions by footprint
overlap (`match.compute_checkpoint`), and anything unpredicted surfaces as drift -- a queryable
fact instead of a manual diff-read.
"""
