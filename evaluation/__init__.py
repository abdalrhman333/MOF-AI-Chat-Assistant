"""Retrieval evaluation — measuring whether a change made search better.

Separate from `pipeline/` on purpose. The pipeline *produces* the vector
store; this package *judges* it, and its real consumer is the chat backend,
which has to trade retrieval quality against a five-second response budget
and currently has no way to tell an improvement from a good feeling.
"""
