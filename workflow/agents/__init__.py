# agents package
import warnings

# See skills/__init__.py — silence the langchain-core pydantic-v1 warning on
# Python 3.14+ before any agent module imports langchain.
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible",
    category=UserWarning,
)
