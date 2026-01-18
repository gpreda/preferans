"""Generate SVG card graphics for Preferans (32 cards)."""

SUITS = {
    'hearts': {'symbol': '♥', 'color': '#e74c3c'},
    'diamonds': {'symbol': '♦', 'color': '#e74c3c'},
    'clubs': {'symbol': '♣', 'color': '#2c3e50'},
    'spades': {'symbol': '♠', 'color': '#2c3e50'},
}

RANKS = ['7', '8', '9', '10', 'J', 'Q', 'K', 'A']


def generate_card_svg(rank: str, suit: str) -> str:
    """Generate SVG for a single card (classic style)."""
    suit_info = SUITS[suit]
    symbol = suit_info['symbol']
    color = suit_info['color']

    # Adjust position for 10 (wider)
    rank_x = 10 if rank != '10' else 4

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 112">
  <!-- Card background -->
  <rect x="1" y="1" width="78" height="110" rx="6" ry="6" fill="white" stroke="#ccc" stroke-width="1"/>

  <!-- Top left rank and suit -->
  <text x="{rank_x}" y="26" font-family="Arial, sans-serif" font-size="24" font-weight="bold" fill="{color}">{rank}</text>
  <text x="8" y="48" font-family="Arial, sans-serif" font-size="21" fill="{color}">{symbol}</text>

  <!-- Bottom right rank and suit (inverted) -->
  <text x="{80 - rank_x - (12 if rank != '10' else 24)}" y="94" font-family="Arial, sans-serif" font-size="24" font-weight="bold" fill="{color}" transform="rotate(180, {80 - rank_x - (6 if rank != '10' else 12)}, 88)">{rank}</text>
  <text x="51" y="72" font-family="Arial, sans-serif" font-size="21" fill="{color}" transform="rotate(180, 59, 66)">{symbol}</text>

  <!-- Center suit symbol -->
  <text x="40" y="65" font-family="Arial, sans-serif" font-size="36" fill="{color}" text-anchor="middle">{symbol}</text>
</svg>'''

    return svg


def generate_compact_card_svg(rank: str, suit: str) -> str:
    """Generate SVG for a single card (compact style with L-shaped corner indicators).

    Visible area when overlapping:
    - Horizontal: 1/4 of width = 20px
    - Vertical: 1/5 of height = 22px

    Layout: Rank with suit to the right, and suit below - forming an L-shape.
    Labels are sized to fill the visible corner area.
    """
    suit_info = SUITS[suit]
    symbol = suit_info['symbol']
    color = suit_info['color']

    # Font sizes optimized for 20x22 visible area
    # Rank should be close to 20px wide (1/4 of card width)
    rank_size = 18 if rank != '10' else 14  # Smaller for "10" as it's 2 chars
    suit_size = 11

    # Positions for top-left corner (must fit within 20x22)
    rank_x = 1
    rank_y = 14
    suit_right_x = 13 if rank != '10' else 17  # After rank
    suit_below_y = 22

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 112">
  <!-- Card background -->
  <rect x="1" y="1" width="78" height="110" rx="6" ry="6" fill="white" stroke="#ccc" stroke-width="1"/>

  <!-- Top left L-shaped indicator (visible area: 20x22) -->
  <text x="{rank_x}" y="{rank_y}" font-family="Arial, sans-serif" font-size="{rank_size}" font-weight="bold" fill="{color}">{rank}</text>
  <text x="{suit_right_x}" y="{rank_y}" font-family="Arial, sans-serif" font-size="{suit_size}" fill="{color}">{symbol}</text>
  <text x="{rank_x}" y="{suit_below_y}" font-family="Arial, sans-serif" font-size="{suit_size}" fill="{color}">{symbol}</text>

  <!-- Bottom right L-shaped indicator (rotated 180°) -->
  <g transform="rotate(180, 40, 56)">
    <text x="{rank_x}" y="{rank_y}" font-family="Arial, sans-serif" font-size="{rank_size}" font-weight="bold" fill="{color}">{rank}</text>
    <text x="{suit_right_x}" y="{rank_y}" font-family="Arial, sans-serif" font-size="{suit_size}" fill="{color}">{symbol}</text>
    <text x="{rank_x}" y="{suit_below_y}" font-family="Arial, sans-serif" font-size="{suit_size}" fill="{color}">{symbol}</text>
  </g>

  <!-- Center suit symbol -->
  <text x="40" y="65" font-family="Arial, sans-serif" font-size="28" fill="{color}" text-anchor="middle">{symbol}</text>
</svg>'''

    return svg


def generate_large_card_svg(rank: str, suit: str) -> str:
    """Generate SVG for a single card (large style with big corner indicators).

    Same L-shaped layout as compact, but with labels twice as large.
    Rank and suit symbols are the same size.
    """
    suit_info = SUITS[suit]
    symbol = suit_info['symbol']
    color = suit_info['color']

    # Large font sizes - twice the compact size
    # Rank and suit are the same size
    label_size = 36 if rank != '10' else 28  # Smaller for "10" as it's 2 chars

    # Positions for top-left corner
    rank_x = 1
    rank_y = 28
    suit_right_x = 26 if rank != '10' else 34  # After rank
    suit_below_y = 44

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 112">
  <!-- Card background -->
  <rect x="1" y="1" width="78" height="110" rx="6" ry="6" fill="white" stroke="#ccc" stroke-width="1"/>

  <!-- Top left L-shaped indicator -->
  <text x="{rank_x}" y="{rank_y}" font-family="Arial, sans-serif" font-size="{label_size}" font-weight="bold" fill="{color}">{rank}</text>
  <text x="{suit_right_x}" y="{rank_y}" font-family="Arial, sans-serif" font-size="{label_size}" fill="{color}">{symbol}</text>
  <text x="{rank_x}" y="{suit_below_y}" font-family="Arial, sans-serif" font-size="{label_size}" fill="{color}">{symbol}</text>

  <!-- Bottom right L-shaped indicator (rotated 180°) -->
  <g transform="rotate(180, 40, 56)">
    <text x="{rank_x}" y="{rank_y}" font-family="Arial, sans-serif" font-size="{label_size}" font-weight="bold" fill="{color}">{rank}</text>
    <text x="{suit_right_x}" y="{rank_y}" font-family="Arial, sans-serif" font-size="{label_size}" fill="{color}">{symbol}</text>
    <text x="{rank_x}" y="{suit_below_y}" font-family="Arial, sans-serif" font-size="{label_size}" fill="{color}">{symbol}</text>
  </g>

  <!-- Center suit symbol (smaller since corners are large) -->
  <text x="40" y="65" font-family="Arial, sans-serif" font-size="20" fill="{color}" text-anchor="middle">{symbol}</text>
</svg>'''

    return svg


def generate_centered_card_svg(rank: str, suit: str) -> str:
    """Generate SVG for a single card (centered style).

    Large corner ranks in top-left and bottom-right, suit symbols in bottom-left and top-right corners.
    """
    suit_info = SUITS[suit]
    symbol = suit_info['symbol']
    color = suit_info['color']

    # Rank size (one unit smaller than large style)
    rank_size = 35 if rank != '10' else 27

    # Suit size for corner symbols (doubled)
    suit_size = 52

    # Rank position for top-left (2 pixels farther from margins)
    rank_x = 3
    rank_y = 30

    # Suit position - 2px from margins
    suit_margin = 2

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 112">
  <!-- Card background -->
  <rect x="1" y="1" width="78" height="110" rx="6" ry="6" fill="white" stroke="#ccc" stroke-width="1"/>

  <!-- Top left rank -->
  <text x="{rank_x}" y="{rank_y}" font-family="Arial, sans-serif" font-size="{rank_size}" font-weight="bold" fill="{color}">{rank}</text>

  <!-- Top right suit (2px from top and right edges) -->
  <text x="{80 - suit_margin}" y="{suit_margin}" font-family="Arial, sans-serif" font-size="{suit_size}" fill="{color}" text-anchor="end" dominant-baseline="hanging">{symbol}</text>

  <!-- Bottom left suit (2px from bottom and left edges) -->
  <text x="{suit_margin}" y="{112 - suit_margin}" font-family="Arial, sans-serif" font-size="{suit_size}" fill="{color}">{symbol}</text>

  <!-- Bottom right rank (rotated 180° around card center) -->
  <g transform="rotate(180, 40, 56)">
    <text x="{rank_x}" y="{rank_y}" font-family="Arial, sans-serif" font-size="{rank_size}" font-weight="bold" fill="{color}">{rank}</text>
  </g>
</svg>'''

    return svg


def generate_card_back_svg() -> str:
    """Generate SVG for card back."""
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 112">
  <!-- Card background -->
  <rect x="1" y="1" width="78" height="110" rx="6" ry="6" fill="#1a5490" stroke="#0d3a6e" stroke-width="1"/>

  <!-- Pattern -->
  <defs>
    <pattern id="backPattern" patternUnits="userSpaceOnUse" width="16" height="16">
      <path d="M0,8 L8,0 L16,8 L8,16 Z" fill="none" stroke="#2980b9" stroke-width="1"/>
    </pattern>
  </defs>
  <rect x="6" y="6" width="68" height="100" rx="4" ry="4" fill="url(#backPattern)"/>

  <!-- Inner border -->
  <rect x="6" y="6" width="68" height="100" rx="4" ry="4" fill="none" stroke="#3498db" stroke-width="2"/>
</svg>'''
    return svg


def generate_all_cards(style: str = 'classic') -> dict:
    """Generate all 32 cards plus card back.

    Args:
        style: 'classic', 'compact', 'large', or 'centered'
    """
    cards = {}

    if style == 'centered':
        generator = generate_centered_card_svg
    elif style == 'large':
        generator = generate_large_card_svg
    elif style == 'compact':
        generator = generate_compact_card_svg
    else:
        generator = generate_card_svg

    for suit in SUITS:
        for rank in RANKS:
            card_id = f"{rank}_{suit}"
            cards[card_id] = {
                'rank': rank,
                'suit': suit,
                'svg': generator(rank, suit)
            }

    cards['back'] = {
        'rank': None,
        'suit': None,
        'svg': generate_card_back_svg()
    }

    return cards


if __name__ == '__main__':
    # Generate classic style
    cards = generate_all_cards(style='classic')
    print(f"Generated {len(cards)} classic cards")

    # Save sample classic card
    with open('sample_card_classic.svg', 'w') as f:
        f.write(cards['A_spades']['svg'])
    print("Sample classic card saved to sample_card_classic.svg")

    # Generate compact style
    cards_compact = generate_all_cards(style='compact')
    print(f"Generated {len(cards_compact)} compact cards")

    # Save sample compact card
    with open('sample_card_compact.svg', 'w') as f:
        f.write(cards_compact['A_spades']['svg'])
    print("Sample compact card saved to sample_card_compact.svg")
