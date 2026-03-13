# TrojkaD Alignment Patterns

Accumulated patterns and fixes from iterative alignment with Sim50T.
Each entry documents a discovered misalignment and the fix applied.
CRITICAL: New fixes must NOT regress on any of these patterns.

## Iteration 1 (score: 58)

**Pattern:** **Connected non-master non-trump cards overvalued as `safe` for declarer leads** (Diffs 2 & 3, the `one_on_one_first_player` rule). This is the most impactful pattern because: (a) it affects 2 of 5 diffs, (b) the 1v1 opening lead fires very frequently (every 1v1 game, trick 1), and (c) the fix is logically sound — cards that can be beaten by higher unaccounted cards are NOT safe leads.

**Fix:** In `_classify_declarer_leading`, modify the non-trump connected-high classification: before classifying connected cards as `safe`, check whether the top card of the connected group is master in its suit (i.e., `_ctx_higher_unaccounted(top_card, ctx) == 0`). If the top card is NOT master (there are higher unaccounted cards), classify those connected cards as `risky` instead of `safe`. This way, guaranteed winners (singleton aces, master-connected cards) remain `safe` and are preferred by TrojkaD's deterministic tiebreaker, while non-master connected groups are deprioritized. Specifically, change the block at lines ~8558-8562 from unconditionally marking connected cards as `safe` to checking `if _ctx_higher_unaccounted(connected[0], ctx) == 0` first — if true, mark `safe`; otherwise mark `risky`.

**Summary:** Downgrade non-master connected non-trump cards from `safe` to `risky` in declarer leading classification, so aces and true masters are preferred as opening leads.

---

## Iteration 2 (score: 69)

**Pattern:** **When declarer follows trump suit in 1v1 and cannot win the trick, TrojkaD forces the lowest trump as `must` (weight 999). But Sim50T preserves the lowest trump for future ruffing by dumping a middle trump instead.** This is the most impactful pattern because: (a) it's a `must` classification with no randomness — TrojkaD gets it wrong 100% of the time in this scenario, unlike diffs 1/2 where weighted random gives a small chance of matching Sim50T; (b) the strategic logic is sound and generalizable — lowest trumps are valuable for cheap ruffing, while middle trumps are expendable.

**Fix:** In `_rule_one_on_one_second_player` (line 8700), modify the "can't win, following suit" branch. Currently it unconditionally returns `lowest_card(suit_cards)` as `must`. Change this to: **when the player is declarer AND the followed suit is the trump suit AND there are 2+ trump cards to choose from**, play the second-lowest trump instead of the absolute lowest. Specifically, sort suit_cards by rank ascending, and return `suit_cards[1]` (second-lowest) as `must` instead of `suit_cards[0]` (lowest). When not declarer, or when not in the trump suit, or when only 1 suit card is available, keep the existing behavior (play lowest). This preserves the lowest trump in the declarer's hand for future ruffing of side-suit leads.

**Summary:** When declarer can't win a trump trick in 1v1, dump the second-lowest trump instead of the lowest to preserve cheap ruffing capability.

---

## Iteration 3 (score: 44)

**Pattern:** **Post-pass 2 unconditionally demotes declarer's safe trump cards to risky, preventing declarer from ever prioritizing trump-drawing leads when any side-suit card is classified.** This fires in every declarer leading scenario where both trump and non-trump classified cards exist — a very common situation. Sim50T consistently shows declarers should draw trumps (lead connected trump sequences) before cashing side-suit aces. Affects Diffs 3 and 6 deterministically (100% wrong).

**Fix:** In `_rule_one_on_one_first_player`, restrict post-pass 2 (lines 8499-8510) to **followers only**. Change the condition at line 8502 from `if trump:` to `if trump and not is_declarer:`. This prevents declarer's connected trump cards from being demoted, so they remain `safe`(100) and compete equally with side-suit aces. TrojkaD's deterministic tiebreaker (`min(rank)`) then naturally picks Q♠/Q♦ (rank 6) over A♣/A♥ (rank 8), matching Sim50T exactly. For followers, the existing behavior is preserved — followers should probe side suits rather than lead trumps, which aligns with the original intent of the post-pass.

**Summary:** Stop demoting declarer's safe trump cards to risky so declarer prioritizes drawing trumps over cashing side-suit aces.

---

## Iteration 4 (score: 57)

**Pattern:** **Declarer ruffing with the lowest trump in `trump_last` instead of preserving it for future cheap ruffing** (Diff 6). This is a direct extension of the iteration 2 pattern ("preserve lowest trump for ruffing") that was fixed in `_rule_one_on_one_second_player` but was never applied to the `trump_last` rule. The mismatch is deterministic (must=999, 0% match rate) — TrojkaD will ALWAYS get this wrong. The principle is proven and well-understood.

Additionally, **diffs 4 and 5 (each ~1% match rate)** represent strategic hand-development leads that TrojkaD's classification fundamentally cannot capture without major restructuring. These are diverse (side-suit establishment vs trump probing) and no single classification tweak covers both.

**Fix:** In the `trump_last` rule (which handles ruffing when the player has no cards in the led suit): when the **declarer** is ruffing a non-trump trick (the current trick winner played a non-trump card, so any trump wins), and the declarer has **2 or more trump cards** in the legal cards, ruff with the **second-lowest trump** instead of the lowest. Specifically, sort the legal trump cards by rank ascending and pick index `[1]` (second-lowest) as `must` instead of index `[0]` (lowest). When not declarer, or when only 1 trump is available, or when needing to beat an opponent's trump already in the trick, keep the existing behavior (lowest winning trump). This mirrors the iteration 2 fix exactly but applies it to the ruffing scenario rather than the trump-following scenario.

**Summary:** Extend the "preserve lowest trump for ruffing" principle (iteration 2) to the `trump_last` rule, so declarer ruffs with second-lowest trump instead of lowest.

---

