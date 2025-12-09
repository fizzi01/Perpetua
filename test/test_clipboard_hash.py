"""
Quick test to verify clipboard hash function works correctly.
"""
import hashlib


def hash_content(content: str) -> str:
    """Test hash function."""
    if not content:
        return ""
    content_bytes = content.encode('utf-8', errors='ignore')
    return hashlib.md5(content_bytes).hexdigest()


# Test with different contents
test_cases = [
    "Hello World",
    "Hello World!",
    "Different content",
    "Hello World",  # Same as first
    "",
    "A" * 1000,  # Long content
    "Unicode: 你好世界 🌍",
    "https://example.com",
]

print("Testing hash function:")
print("=" * 60)

hashes = []
for i, content in enumerate(test_cases):
    h = hash_content(content)
    hashes.append(h)
    preview = content[:40] + "..." if len(content) > 40 else content
    print(f"{i+1}. '{preview}'")
    print(f"   Hash: {h}")

    # Check for duplicates
    if h in hashes[:-1]:
        idx = hashes.index(h)
        if content == test_cases[idx]:
            print(f"   ✓ Same as test {idx+1} (same content)")
        else:
            print(f"   ✗ COLLISION with test {idx+1}!")
    print()

print("=" * 60)
print(f"Total unique hashes: {len(set(hashes))}")
print(f"Total tests: {len(test_cases)}")

# Verify that same content produces same hash
assert hashes[0] == hashes[3], "Same content should produce same hash!"
print("✓ Same content produces same hash")

# Verify that different content produces different hash
assert hashes[0] != hashes[1], "Different content should produce different hash!"
print("✓ Different content produces different hash")

# Verify empty string
assert hashes[4] == "", "Empty string should produce empty hash"
print("✓ Empty string handled correctly")

print("\n✓ All hash tests passed!")

