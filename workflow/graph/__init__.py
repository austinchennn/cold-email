# graph package — LangGraph orchestration for the cold-email pipeline
import warnings

# See skills/__init__.py — silence the langchain-core pydantic-v1 warning on
# Python 3.14+ before any graph module imports langchain / langgraph.
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible",
    category=UserWarning,
)

from graph.pipeline import build_pipeline, run_pipeline  # noqa: E402
from graph.state import PipelineState  # noqa: E402

__all__ = ["build_pipeline", "run_pipeline", "PipelineState"]
