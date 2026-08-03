"""
Batch write utilities for Neo4j operations.

Provides reusable batch writing patterns with result validation,
error handling, and progress logging.
"""

import logging
from typing import Any


class BatchWriter:
    """
    Reusable batch writer for Neo4j Cypher queries.

    Handles:
    - Chunking data into batches
    - Executing queries with UNWIND pattern
    - Validating results (explicit key checking)
    - Progress logging
    - Error handling and failure tracking
    """

    def __init__(
        self,
        session,
        batch_size: int = 1000,
        logger: logging.Logger | None = None,
    ):
        """
        Initialize batch writer.

        Args:
            session: Neo4j session for executing queries
            batch_size: Number of items per batch
            logger: Optional logger instance
        """
        self.session = session
        self.batch_size = batch_size
        self.logger = logger or logging.getLogger(__name__)

    def write_batches(
        self,
        data: list[dict[str, Any]],
        query: str,
        result_keys: list[str],
        progress_interval: int = 10,
        verify_symmetric: bool = False,
    ) -> dict[str, int]:
        """
        Write data in batches using provided Cypher query.

        Args:
            data: List of dicts to write (passed as $batch parameter)
            query: Cypher query with UNWIND $batch pattern
            result_keys: Keys to extract from result (e.g., ["created"] or ["r1_count", "r2_count"])
            progress_interval: Log progress every N batches
            verify_symmetric: If True, warn when counts are asymmetric (for bidirectional rels)

        Returns:
            Dict with:
                - total_written: Sum of all counts from successful batches
                - failed_batches: Number of batches that failed

        Example:
            writer = BatchWriter(session, batch_size=100, logger=logger)
            result = writer.write_batches(
                data=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
                query=\"\"\"
                    UNWIND $batch AS item
                    CREATE (n:Node {id: item.id, name: item.name})
                    RETURN count(n) AS created
                \"\"\",
                result_keys=["created"]
            )
            print(f"Wrote {result['total_written']} nodes")
        """
        if not data:
            return {"total_written": 0, "failed_batches": 0}

        total_written = 0
        failed_batches = 0

        num_batches = (len(data) + self.batch_size - 1) // self.batch_size

        for i in range(0, len(data), self.batch_size):
            batch_chunk = data[i : i + self.batch_size]
            batch_num = i // self.batch_size + 1

            try:
                result = self.session.run(query, batch=batch_chunk)

                # Validate result has expected keys
                record = result.single()

                if not record:
                    self.logger.warning(f"   ⚠ Batch {batch_num}/{num_batches} returned no result")
                    failed_batches += 1
                    continue

                # Check all expected keys present
                # Note: Neo4j Record objects require explicit .keys() check, not `in` operator
                available_keys = list(record.keys()) if hasattr(record, "keys") else []
                missing_keys = [key for key in result_keys if key not in available_keys]

                if missing_keys:
                    self.logger.warning(
                        f"   ⚠ Batch {batch_num}/{num_batches} write failed - missing result keys. "
                        f"Expected: {result_keys}, Found: {available_keys}, Missing: {missing_keys}"
                    )
                    failed_batches += 1
                    continue

                # Extract counts
                counts = [record[key] for key in result_keys]

                # Check for None values
                if any(count is None for count in counts):
                    self.logger.warning(
                        f"   ⚠ Batch {batch_num}/{num_batches} returned None for counts"
                    )
                    failed_batches += 1
                    continue

                # Sum counts
                batch_total = sum(counts)
                total_written += batch_total

                # Verify symmetry if requested (for bidirectional relationships)
                if verify_symmetric and len(counts) == 2:
                    if counts[0] != counts[1]:
                        self.logger.warning(
                            f"   ⚠ Batch {batch_num}/{num_batches}: Asymmetric relationships "
                            f"({result_keys[0]}={counts[0]}, {result_keys[1]}={counts[1]})"
                        )

            except Exception as e:
                self.logger.warning(f"   ⚠ Batch {batch_num}/{num_batches} write failed: {e}")
                failed_batches += 1

            # Log progress at intervals or on last batch
            if batch_num % progress_interval == 0 or i + self.batch_size >= len(data):
                progress = min(i + self.batch_size, len(data))
                self.logger.info(f"   Progress: {progress}/{len(data)} processed...")

        if failed_batches > 0:
            self.logger.warning(f"   ⚠ {failed_batches}/{num_batches} batches failed to write")

        return {
            "total_written": total_written,
            "failed_batches": failed_batches,
        }
