"""Graph Data Science helpers.

Only the projection lifecycle utilities the ownership graph needs. Every algorithm follows the
repo convention: create a named projection → use it → drop it (see :mod:`secgraph.gds.utils`).
"""

from secgraph.gds.utils import get_gds_client, safe_drop_graph

__all__ = ["get_gds_client", "safe_drop_graph"]
