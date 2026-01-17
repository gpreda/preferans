"""Generate SVG card graphics for Preferans (32 cards)."""

SUITS = {
    'hearts': {'symbol': '♥', 'color': '#e74c3c'},
    'diamonds': {'symbol': '♦', 'color': '#e74c3c'},
    'clubs': {'symbol': '♣', 'color': '#2c3e50'},
    'spades': {'symbol': '♠', 'color': '#2c3e50'},
}

RANKS = ['7', '8', '9', '10', 'J', 'Q', 'K', 'A']


def generate_card_svg(rank: str, suit: str) -> str:
    """Generate SVG for a single card."""
    suit_info = SUITS[suit]
    symbol = suit_info['symbol']
    color = suit_info['color']

    # Adjust position for 10 (wider)
    rank_x = 12 if rank != '10' else 8

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 112">
  <!-- Card background -->
  <rect x="1" y="1" width="78" height="110" rx="6" ry="6" fill="white" stroke="#ccc" stroke-width="1"/>

  <!-- Top left rank and suit -->
  <text x="{rank_x}" y="22" font-family="Arial, sans-serif" font-size="16" font-weight="bold" fill="{color}">{rank}</text>
  <text x="10" y="38" font-family="Arial, sans-serif" font-size="14" fill="{color}">{symbol}</text>

  <!-- Bottom right rank and suit (inverted) -->
  <text x="{80 - rank_x - (8 if rank != '10' else 16)}" y="98" font-family="Arial, sans-serif" font-size="16" font-weight="bold" fill="{color}" transform="rotate(180, {80 - rank_x - (4 if rank != '10' else 8)}, 94)">{rank}</text>
  <text x="60" y="82" font-family="Arial, sans-serif" font-size="14" fill="{color}" transform="rotate(180, 64, 78)">{symbol}</text>

  <!-- Center suit symbol -->
  <text x="40" y="65" font-family="Arial, sans-serif" font-size="36" fill="{color}" text-anchor="middle">{symbol}</text>
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


def generate_all_cards() -> dict:
    """Generate all 32 cards plus card back."""
    cards = {}

    for suit in SUITS:
        for rank in RANKS:
            card_id = f"{rank}_{suit}"
            cards[card_id] = {
                'rank': rank,
                'suit': suit,
                'svg': generate_card_svg(rank, suit)
            }

    cards['back'] = {
        'rank': None,
        'suit': None,
        'svg': generate_card_back_svg()
    }

    return cards


if __name__ == '__main__':
    cards = generate_all_cards()
    print(f"Generated {len(cards)} cards")

    # Save sample card to verify
    with open('sample_card.svg', 'w') as f:
        f.write(cards['A_spades']['svg'])
    print("Sample card saved to sample_card.svg")
