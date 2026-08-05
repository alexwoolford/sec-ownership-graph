"""
secgraph — a graph-native SEC ownership-relationship graph in Neo4j.

Builds one graph from SEC ownership filings (Schedule 13D/G, Form 3/4/5, Form 13F), hard-keyed on
CIK, and answers the questions that need relationships followed to an unknown depth:

- **Activist convergence / campaign timing** — who moved first on an issuer, and who followed.
- **Activist coalitions** — the custodial-scrubbed co-targeting component around a filer.
- **Control chains** — transitive >=50% ownership pyramids.
- **Board interlocks** — the named director bridging two boards.

Everything served is deterministic Cypher with an evidence-or-abstain contract: each answer cites
its SEC accession number, or explicitly declines. See ``docs/demo_script_governance_desk.md``.
"""

import logging

# NullHandler prevents "no handler found" warnings when used as a library; applications configure
# their own handlers.
logging.getLogger(__name__).addHandler(logging.NullHandler())

__version__ = "0.1.0"

from secgraph.core.config.constants import (
    BATCH_SIZE_LARGE,
    BATCH_SIZE_SMALL,
)
from secgraph.core.config.settings import (
    get_neo4j_database,
    get_neo4j_uri,
)

__all__ = [
    "BATCH_SIZE_LARGE",
    "BATCH_SIZE_SMALL",
    "__version__",
    "get_neo4j_database",
    "get_neo4j_uri",
]
