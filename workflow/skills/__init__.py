# skills package
import warnings

# langchain-core still imports pydantic.v1 shims, which warn loudly on
# Python 3.14+. We don't touch that code path — silence just this warning.
# Kept here (package import) so it runs before any submodule pulls in langchain.
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible",
    category=UserWarning,
)
