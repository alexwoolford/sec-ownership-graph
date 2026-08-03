"""
Constants for secgraph package.

Centralizes magic numbers and configuration defaults.
"""

# Batch sizes for Neo4j operations
BATCH_SIZE_SMALL = 1000  # For node creation
BATCH_SIZE_LARGE = 5000  # For relationship creation
BATCH_SIZE_DELETE = 10000  # For relationship deletion

# GDS algorithm defaults
DEFAULT_TOP_K = 50
DEFAULT_SIMILARITY_CUTOFF = 0.1
DEFAULT_SIMILARITY_THRESHOLD = 0.6
DEFAULT_JACCARD_THRESHOLD = 0.3

# Minimum description length for similarity computation
# Filters out meaningless short descriptions (e.g., "N/A", very short text)
# that cause false exact matches (1.0 similarity)
MIN_DESCRIPTION_LENGTH_FOR_SIMILARITY = 200  # characters

# PageRank defaults
DEFAULT_MAX_ITERATIONS = 20
DEFAULT_DAMPING_FACTOR = 0.85

# Embedding defaults
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536

# Rate limiting
MIN_REQUEST_INTERVAL = 0.1  # seconds between API calls (general)
# OpenAI embeddings allow higher rates (100 req/sec)
EMBEDDING_REQUEST_INTERVAL = 0.01  # seconds between embedding API calls

# API rate limits (requests per second)
SEC_EDGAR_RATE_LIMIT = 10.0  # SEC EDGAR official limit: 10 req/sec
SEC_EDGAR_LONG_DURATION_LIMIT = 5.0  # SEC EDGAR long-duration limit: 5 req/sec average
YFINANCE_RATE_LIMIT = 0.0  # yfinance: No explicit limit, library handles throttling

# Cache TTL (Time To Live) in days
CACHE_TTL_COMPANY_DOMAINS = 30  # Company domain data cache TTL
CACHE_TTL_COMPANY_PROPERTIES = 30  # Company properties cache TTL
CACHE_TTL_10K_EXTRACTED = 365  # 10-K extracted data cache TTL (long-lived)
CACHE_TTL_NEGATIVE_RESULT = 7  # Negative results (not found) cache TTL (shorter)

# Parallel processing defaults
DEFAULT_WORKERS = 8  # Default number of parallel workers
DEFAULT_WORKERS_WITH_API = 16  # Default workers when API key is available (faster)

# OpenAI async concurrency defaults
# Max concurrent API requests for embedding creation
# Conservative default (30) balances throughput with memory/rate limits
# Can be increased up to 50-80 for higher tiers, or reduced to 5-10 for lower tiers
OPENAI_MAX_CONCURRENT = 30

# Embedding processing defaults
EMBEDDING_PAGE_SIZE = 50_000  # Fetch 50K keys per page (cursor-based pagination)
EMBEDDING_NEO4J_BATCH_SIZE_LARGE = 50_000  # Batch size for Neo4j writes (short texts)
EMBEDDING_NEO4J_BATCH_SIZE_SMALL = 1_000  # Batch size for Neo4j writes (long texts)

# GraphRAG/Vector index defaults
VECTOR_INDEX_MAX_WAIT_SECONDS = 30  # Maximum time to wait for vector index to come online
VECTOR_INDEX_CHECK_INTERVAL = 2  # Seconds between vector index status checks
