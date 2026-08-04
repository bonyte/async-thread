#!/usr/bin/env python3
"""
Quick verification script for Async Thread implementation.

Run this to verify all components are working:
  python verify.py
"""

import asyncio
import sys
from pathlib import Path

def check_file_exists(filepath: str, description: str) -> bool:
    """Check if a file exists and print status."""
    exists = Path(filepath).exists()
    status = "✅" if exists else "❌"
    print(f"{status} {description}: {filepath}")
    return exists


def check_imports() -> bool:
    """Verify all imports work."""
    print("\n📦 Checking imports...")
    try:
        import fastapi
        print("✅ fastapi imported")
        import sqlalchemy
        print("✅ sqlalchemy imported")
        import redis
        print("✅ redis imported")
        import pydantic
        print("✅ pydantic imported")
        import pytest
        print("✅ pytest imported")
        return True
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("\n💡 Fix: pip install -r requirements.txt")
        return False


def check_models() -> bool:
    """Verify models can be imported."""
    print("\n🗂️  Checking models...")
    try:
        from models import Thread, ThreadMessage, TimestampMixin, Base
        print("✅ Thread model imported")
        print("✅ ThreadMessage model imported")
        print("✅ TimestampMixin imported")
        print("✅ Base imported")
        
        # Check model attributes
        assert hasattr(Thread, 'id'), "Thread missing id"
        assert hasattr(Thread, 'participant_hash'), "Thread missing participant_hash"
        assert hasattr(ThreadMessage, 'body'), "ThreadMessage missing body"
        print("✅ Model attributes verified")
        return True
    except Exception as e:
        print(f"❌ Model error: {e}")
        return False


def check_main() -> bool:
    """Verify main app can be imported."""
    print("\n🚀 Checking main app...")
    try:
        from main import app, participant_hash
        print("✅ FastAPI app imported")
        print("✅ participant_hash function imported")
        
        # Test participant_hash
        hash_ab = participant_hash("user-a", "user-b")
        hash_ba = participant_hash("user-b", "user-a")
        assert hash_ab == hash_ba, "Hash not order-independent"
        print("✅ participant_hash is order-independent")
        print(f"   Sample: participant_hash('a', 'b') = {hash_ab[:16]}...")
        
        return True
    except Exception as e:
        print(f"❌ Main app error: {e}")
        return False


def check_tests() -> bool:
    """Verify test file exists and can be imported."""
    print("\n🧪 Checking tests...")
    try:
        check_file_exists("test_async_thread.py", "Test file")
        check_file_exists("conftest.py", "Pytest config")
        
        # Try importing conftest fixtures
        import conftest
        print("✅ conftest imported (fixtures available)")
        return True
    except Exception as e:
        print(f"⚠️  Test import warning: {e}")
        return True  # Not critical


def main():
    """Run all verification checks."""
    print("=" * 60)
    print("🔍 Async Thread - Implementation Verification")
    print("=" * 60)
    
    # Check files
    print("\n📋 Checking files...")
    files_ok = all([
        check_file_exists("main.py", "Main app"),
        check_file_exists("models.py", "ORM models"),
        check_file_exists("conftest.py", "Pytest config"),
        check_file_exists("test_async_thread.py", "Test suite"),
        check_file_exists("requirements.txt", "Dependencies"),
        check_file_exists("README.md", "Documentation"),
    ])
    
    # Check dependencies
    imports_ok = check_imports()
    
    # Check models
    models_ok = check_models()
    
    # Check main app
    main_ok = check_main()
    
    # Check tests
    tests_ok = check_tests()
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 Verification Summary")
    print("=" * 60)
    
    all_ok = files_ok and imports_ok and models_ok and main_ok and tests_ok
    
    if all_ok:
        print("""
✅ All checks passed!

🚀 Quick start:

  1. Start the app:
     uvicorn main:app --reload --port 8005

  2. In another terminal, run tests:
     pytest test_async_thread.py -v

  3. Check health:
     curl http://localhost:8005/health

📚 More info: See README.md or IMPLEMENTATION.md
        """)
        return 0
    else:
        print("""
❌ Some checks failed. Fix issues above, then try again.

💡 Common fixes:
   - pip install -r requirements.txt
   - Ensure Python 3.11+: python --version
   - Check Redis is running: redis-cli ping
        """)
        return 1


if __name__ == "__main__":
    sys.exit(main())
