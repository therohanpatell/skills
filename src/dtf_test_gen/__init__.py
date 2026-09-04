"""DTF Test Data Generator -- minimal, transformation-aware BigQuery test data."""

__version__ = "0.1.0"

from dtf_test_gen.config import AppConfig
from dtf_test_gen.engine import Engine

__all__ = ["AppConfig", "Engine", "__version__"]
