#!/usr/bin/env python3
"""
PyContinuity Test Runner
Runs all tests in the test suite including unit tests, integration tests, and benchmarks.
"""

import os
import sys
import time
import subprocess
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def run_test_file(test_file: Path) -> tuple[bool, str, float]:
    """Run a single test file and return results."""
    print(f"\n{'='*20} Running {test_file.name} {'='*20}")
    
    start_time = time.time()
    try:
        # Run the test file
        result = subprocess.run(
            [sys.executable, str(test_file)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120  # 2 minute timeout
        )
        
        duration = time.time() - start_time
        
        # Print output
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
            
        success = result.returncode == 0
        status = "PASSED" if success else "FAILED"
        
        print(f"\n{test_file.name}: {status} (took {duration:.2f}s)")
        return success, status, duration
        
    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        print(f"\n{test_file.name}: TIMEOUT (after {duration:.2f}s)")
        return False, "TIMEOUT", duration
        
    except Exception as e:
        duration = time.time() - start_time
        print(f"\n{test_file.name}: ERROR - {e}")
        return False, "ERROR", duration


def main():
    """Run all tests in the test suite."""
    print("PyContinuity Test Suite Runner")
    print("=" * 50)
    
    test_dir = Path(__file__).parent
    
    # Find all test files
    test_files = [
        test_dir / "test_protocol.py",
        test_dir / "test_new_chunking.py", 
        test_dir / "test_chunk_manager.py",
        test_dir / "test_integration_simple.py",
        test_dir / "test_performance.py",
        test_dir / "test_clipboard_reconstruction.py"
    ]
    
    # Filter existing files
    existing_tests = [f for f in test_files if f.exists()]
    
    if not existing_tests:
        print("No test files found!")
        return 1
        
    print(f"Found {len(existing_tests)} test files:")
    for test_file in existing_tests:
        print(f"  - {test_file.name}")
    
    # Run tests
    results = []
    start_time = time.time()
    
    for test_file in existing_tests:
        success, status, duration = run_test_file(test_file)
        results.append((test_file.name, success, status, duration))
    
    total_duration = time.time() - start_time
    
    # Summary
    print("\n" + "=" * 50)
    print("TEST SUITE SUMMARY")
    print("=" * 50)
    
    passed = sum(1 for _, success, _, _ in results if success)
    total = len(results)
    
    for test_name, success, status, duration in results:
        icon = "✓" if success else "✗"
        print(f"{icon} {test_name}: {status} ({duration:.2f}s)")
    
    print(f"\nOverall Results: {passed}/{total} tests passed")
    print(f"Total time: {total_duration:.2f}s")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED!")
        print("The PyContinuity system is working correctly.")
        return 0
    else:
        print(f"\n❌ {total - passed} TESTS FAILED!")
        print("Please review the failed tests above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())