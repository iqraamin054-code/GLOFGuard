"""Modular data-refresh and risk-indicator system for the GLOF prototype.

The package deliberately separates relatively static baseline susceptibility
from a dynamic environmental risk indicator.  Neither output is a guarantee
that a flood will occur.
"""

from .schemas import SAFETY_NOTICE, TIME_SERIES_COLUMNS, TimeSeriesRecord

__all__ = ["SAFETY_NOTICE", "TIME_SERIES_COLUMNS", "TimeSeriesRecord"]

__version__ = "0.1.0"
