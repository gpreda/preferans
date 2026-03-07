"""Enumerate all distinct 10-card hand encodings in preferans.

Encoding rules:
- 7,8,9,10 → 'x'
- J, D (Queen), K, A keep identity
- Each suit listed in descending order: A K D J x x x x
- Suits sorted by length desc, then by pattern string for ties
- Separator '-' between suits (no trailing separator for empty suits)
"""

from itertools import combinations_with_replacement
from functools import cmp_to_key

# Card order: A > K > D > J > x
CARD_ORDER = {'A': 0, 'K': 1, 'D': 2, 'J': 3, 'x': 4}

# Generate all possible suit patterns
# Each suit has up to 4 high cards {A, K, D, J} and up to 4 x's
HIGH_CARDS = ['A', 'K', 'D', 'J']


def _cmp_pattern(a, b):
    """Compare two suit patterns: A > K > D > J > x, char by char."""
    for ca, cb in zip(a, b):
        if CARD_ORDER[ca] != CARD_ORDER[cb]:
            return CARD_ORDER[ca] - CARD_ORDER[cb]
    return len(a) - len(b)


def _sort_key(pat):
    """Sort key for patterns: by length desc, then by card order for ties."""
    return (-len(pat), [CARD_ORDER[c] for c in pat])

def generate_suit_patterns():
    """Generate all (pattern_string, length) for a single suit."""
    patterns = []
    # Enumerate all subsets of high cards
    for mask in range(16):  # 2^4 subsets of {A, K, D, J}
        highs = [HIGH_CARDS[i] for i in range(4) if mask & (1 << (3 - i))]
        for x_count in range(5):  # 0-4 x's
            length = len(highs) + x_count
            if length > 8:
                continue
            pat = ''.join(highs) + 'x' * x_count
            patterns.append((pat, length))
    return patterns


def enumerate_hands():
    patterns = generate_suit_patterns()
    # Index patterns: (pattern_string, length)
    # We need to pick 4 suits (order doesn't matter initially) summing to 10

    # Group patterns by length for efficiency
    by_length = {}
    for pat, length in patterns:
        by_length.setdefault(length, []).append(pat)

    # Find all 4-suit combinations summing to 10
    # Lengths range 0-8, we need l1+l2+l3+l4=10 with l1>=l2>=l3>=l4>=0
    results = set()

    # Generate length partitions of 10 into 4 parts (descending)
    def length_partitions(total, parts, max_val=8):
        if parts == 1:
            if 0 <= total <= max_val:
                yield (total,)
            return
        for v in range(min(total, max_val), -1, -1):
            for rest in length_partitions(total - v, parts - 1, min(v, max_val)):
                yield (v,) + rest

    for lens in length_partitions(10, 4):
        # lens is sorted descending (e.g., (4, 3, 2, 1))
        # Get all patterns for each length
        pat_lists = [by_length.get(l, []) for l in lens]
        if any(len(p) == 0 for p in pat_lists):
            continue

        # Generate all combinations of patterns for these lengths
        # Need to handle duplicate lengths carefully
        # Group consecutive equal lengths
        from itertools import product
        groups = []
        i = 0
        while i < 4:
            j = i
            while j < 4 and lens[j] == lens[i]:
                j += 1
            groups.append((lens[i], j - i))  # (length, count)
            i = j

        # For each group of equal-length suits, use combinations_with_replacement
        group_options = []
        for length, count in groups:
            pats = sorted(by_length.get(length, []), key=_sort_key)
            # combinations_with_replacement gives sorted tuples (no duplicates)
            group_combos = list(combinations_with_replacement(pats, count))
            group_options.append(group_combos)

        # Cartesian product across groups
        for combo in product(*group_options):
            # Flatten: combo is tuple of tuples
            suit_pats = []
            for group_tuple in combo:
                suit_pats.extend(group_tuple)
            # Build encoding string
            # Suits are already sorted: by length desc (from partition),
            # within same length by pattern string (from combinations_with_replacement)
            encoding = '-'.join(p for p in suit_pats if p)  # skip empty strings
            results.add(encoding)

    def _hand_sort_key(enc):
        parts = enc.split('-')
        return [_sort_key(p) for p in parts]
    return sorted(results, key=_hand_sort_key)


if __name__ == '__main__':
    hands = enumerate_hands()
    print(f"Total distinct hand encodings: {len(hands)}")
    print()
    # Show first and last 20
    for h in hands[:20]:
        print(f"  {h}")
    if len(hands) > 40:
        print(f"  ... ({len(hands) - 40} more) ...")
    for h in hands[-20:]:
        print(f"  {h}")
