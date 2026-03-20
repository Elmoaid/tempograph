"""Language-specific handler mixins for FileParser."""

from .python_handler import PythonHandlerMixin
from .js_handler import JSHandlerMixin

__all__ = ["PythonHandlerMixin", "JSHandlerMixin"]
