"""Shared pytest fixtures for Poster Helper Bot tests"""
import sys
from pathlib import Path

import pytest

# Add project root to path so tests can import project modules
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def db():
    """Get database instance"""
    from database import get_database
    return get_database()


@pytest.fixture
def simple_parser():
    """Get simple parser instance"""
    from simple_parser import get_simple_parser
    return get_simple_parser()


TEST_USER_ID = 999999
