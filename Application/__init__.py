"""The Enjaz chat application — everything a user eventually touches.

Deliberately separate from `pipeline/` (which builds the knowledge base)
and `evaluation/` (which measures it). They run as different processes
under different database roles: the pipeline owns the tables and reaches
across every project, while this package connects as a SELECT-only role
constrained to one project per transaction.

See `Application/README.md` for the layout and the trust boundary.
"""
