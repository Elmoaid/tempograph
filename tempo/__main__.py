"""Allow `python -m tempo`."""
import sys
from .cli import main
sys.exit(main())
