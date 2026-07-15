"""Natural-language target resolution: the last rung of the deterministic fallback ladder
(op-id -> prefix -> `file::symbol` -> feature label -> NL intent, plan U8/U10/U13). The LLM
here only ever names *which* ref to point a verb at; it never authors code and never touches
the ideal algebra.
"""
