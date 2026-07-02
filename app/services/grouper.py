"""Grouping constraint service.

Applies business rules that constrain which items must (or must not)
be packed together in the same box. Uses a priority-based approach:

  P0 (highest): Shipping transport limits (max length/width/height/weight)
    → Items exceeding limits together get split into separate groups
    → BUT: exceeding limits does NOT prohibit; just means surcharges apply

  P1 (high): must_pack_together / must_not_pack_together
    → Hard constraints: host+accessory must be in same group,
      fragile+heavy must NOT be in same group

  P2 (lowest): pack_near (try same box)
    → Soft preference: no conflict → try to pack together

Algorithm:
  1. Start with all items in one group
  2. Apply P1 "must_not_pack_together" → split groups to separate conflicting items
  3. Apply P1 "must_pack_together" → merge groups that contain linked items
  4. Apply P2 "pack_near" → try to merge compatible groups for better utilization
  5. Apply P0 "shipping limits" → check each group against limits, flag exceeded
"""

from __future__ import annotations

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rule types
# ---------------------------------------------------------------------------

MUST_PACK = "must_pack_together"
MUST_NOT_PACK = "must_not_pack_together"
PACK_NEAR = "pack_near"


# ---------------------------------------------------------------------------
# Union-Find helper for efficient group merging
# ---------------------------------------------------------------------------

class UnionFind:
    """Union-Find (Disjoint Set) data structure for efficient group merging."""

    def __init__(self, elements: list[str]):
        self.parent = {e: e for e in elements}
        self.rank = {e: 0 for e in elements}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]  # path compression
            x = self.parent[x]
        return x

    def union(self, x: str, y: str) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1

    def groups(self) -> dict[str, list[str]]:
        """Return dict mapping root → list of members."""
        result = defaultdict(list)
        for e in self.parent:
            result[self.find(e)].append(e)
        return dict(result)


# ---------------------------------------------------------------------------
# Core grouping logic
# ---------------------------------------------------------------------------

def apply_group_rules(
    items: list[dict],
    rules: list[dict],
    shipping_limits: dict | None = None,
) -> list[list[dict]]:
    """Partition items into packing groups based on constraint rules.

    Priority ordering:
      P0: Shipping limits (max L/W/H/weight) — checked after grouping,
          flags exceeded groups but doesn't force splitting
      P1: must_pack_together / must_not_pack_together — hard constraints
      P2: pack_near — soft preference (try same box)

    Args:
        items: List of item dicts, each with keys:
            - sku (str): product identifier
            - length_cm, width_cm, height_cm, weight_kg: physical attrs
            - fragile, heavy, tags: optional attributes from Product model
        rules: List of group rule dicts, each with keys:
            - rule_type (str): MUST_PACK / MUST_NOT_PACK / PACK_NEAR
            - source_sku (str): first product SKU
            - target_sku (str): second product SKU
            - priority (int): 0=P0, 1=P1, 2=P2
            - description (str, optional): human-readable reason
        shipping_limits: Optional dict with max_length_cm, max_width_cm,
            max_height_cm, max_weight_kg for P0 checking.

    Returns:
        List of item groups (each group is a list of item dicts).
        Each group will be independently solved by the engine.
    """
    if not items:
        return []

    # Build SKU → item dict for lookup
    sku_map = {it["sku"]: it for it in items}
    all_skus = [it["sku"] for it in items]

    # Step 0: If no P1/P2 rules → start with all items in one group
    # (maximize utilization by default, then P0 will split if needed)
    p1_rules = [r for r in rules if r.get("rule_type") in (MUST_PACK, MUST_NOT_PACK)]
    p2_rules = [r for r in rules if r.get("rule_type") == PACK_NEAR]

    if not p1_rules and not p2_rules:
        # No P1/P2 rules → single group, then apply P0 weight splitting
        groups = [items]
        if shipping_limits:
            groups = _apply_weight_split(groups, shipping_limits)
        return groups

    # Step 1: Initialize Union-Find — each item starts as its own group
    uf = UnionFind(all_skus)

    # Step 2: Apply P1 must_pack_together → merge groups
    must_pack_rules = [r for r in p1_rules if r["rule_type"] == MUST_PACK]
    for rule in must_pack_rules:
        src = rule["source_sku"]
        tgt = rule["target_sku"]
        if src in sku_map and tgt in sku_map:
            uf.union(src, tgt)
            logger.debug(f"P1 merge: {src} + {tgt} (must_pack_together)")

    # Step 3: Apply P1 must_not_pack_together → force separation
    # This requires checking if two conflicting items ended up in the same group,
    # and if so, splitting that group.
    must_not_rules = [r for r in p1_rules if r["rule_type"] == MUST_NOT_PACK]
    conflict_pairs = set()
    for rule in must_not_rules:
        src = rule["source_sku"]
        tgt = rule["target_sku"]
        if src in sku_map and tgt in sku_map:
            conflict_pairs.add((src, tgt))

    # Check for P1 conflicts and split if needed
    # We need to iteratively resolve conflicts because merging from must_pack
    # may have put conflicting items together
    groups_dict = uf.groups()
    needs_split = False
    for (src, tgt) in conflict_pairs:
        if uf.find(src) == uf.find(tgt):
            # Conflict found: both are in same group but must be separated
            # This means must_pack and must_not_pack are contradictory for this pair
            logger.warning(
                f"P1 conflict: {src} and {tgt} must_pack AND must_not_pack "
                f"— must_pack takes precedence (they stay together)"
            )
            # In real deployment, this should be flagged as a rule error
            # For MVP, must_pack wins over must_not_pack when contradictory

    # Step 4: Build initial groups from Union-Find
    groups_dict = uf.groups()

    # Convert groups_dict to list of item groups
    item_groups = []
    for root, members in groups_dict.items():
        group_items = [sku_map[sku] for sku in members if sku in sku_map]
        if group_items:
            item_groups.append(group_items)

    # Step 5: Apply P2 pack_near → try to merge compatible groups
    # "pack_near" means these items prefer to be together IF no P1 conflicts
    for rule in p2_rules:
        src = rule["source_sku"]
        tgt = rule["target_sku"]
        if src not in sku_map or tgt not in sku_map:
            continue

        src_group_idx = _find_group_index(item_groups, src)
        tgt_group_idx = _find_group_index(item_groups, tgt)

        if src_group_idx is None or tgt_group_idx is None:
            continue

        if src_group_idx == tgt_group_idx:
            continue  # already in same group

        # Check if merging would violate any must_not_pack constraint
        src_group_skus = {it["sku"] for it in item_groups[src_group_idx]}
        tgt_group_skus = {it["sku"] for it in item_groups[tgt_group_idx]}

        if _would_create_conflict(src_group_skus, tgt_group_skus, conflict_pairs):
            logger.debug(
                f"P2 skip: merging {src} and {tgt} would create P1 conflict"
            )
            continue

        # Merge: combine the two groups
        merged = item_groups[src_group_idx] + item_groups[tgt_group_idx]
        # Remove both old groups, add merged
        new_groups = []
        for i, g in enumerate(item_groups):
            if i == src_group_idx or i == tgt_group_idx:
                continue
            new_groups.append(g)
        new_groups.append(merged)
        item_groups = new_groups
        logger.debug(f"P2 merge: {src} + {tgt} (pack_near)")

    # Step 6: Apply P0 shipping limits → weight-based splitting
    if shipping_limits:
        item_groups = _apply_weight_split(item_groups, shipping_limits)

    return item_groups


def validate_groups(
    groups: list[list[dict]],
    rules: list[dict],
) -> dict:
    """Validate that the proposed packing groups satisfy all constraint rules.

    Args:
        groups: Proposed item groups (list of lists of item dicts).
        rules: Group constraint rules to check against.

    Returns:
        Dict with keys:
          - valid (bool): whether all P1 hard constraints are satisfied
          - violations (list): descriptions of violated rules
          - p2_satisfied (list): P2 rules that are satisfied
          - p2_violated (list): P2 rules that are violated (soft, not blocking)
    """
    violations = []
    p2_satisfied = []
    p2_violated = []

    # Build SKU → group_index mapping
    sku_to_group = {}
    for idx, group in enumerate(groups):
        for it in group:
            sku_to_group[it["sku"]] = idx

    for rule in rules:
        src = rule["source_sku"]
        tgt = rule["target_sku"]
        rule_type = rule["rule_type"]

        src_group = sku_to_group.get(src)
        tgt_group = sku_to_group.get(tgt)

        if src_group is None or tgt_group is None:
            # SKU not found in any group → skip validation
            continue

        if rule_type == MUST_PACK:
            if src_group != tgt_group:
                violations.append(
                    f"P1 violation: {src} and {tgt} must_pack_together "
                    f"but are in groups {src_group} and {tgt_group}"
                )

        elif rule_type == MUST_NOT_PACK:
            if src_group == tgt_group:
                violations.append(
                    f"P1 violation: {src} and {tgt} must_not_pack_together "
                    f"but are in same group {src_group}"
                )

        elif rule_type == PACK_NEAR:
            if src_group == tgt_group:
                p2_satisfied.append(
                    f"{src} and {tgt} packed together (pack_near satisfied)"
                )
            else:
                p2_violated.append(
                    f"{src} and {tgt} in different groups "
                    f"(pack_near not satisfied, but acceptable)"
                )

    valid = len(violations) == 0

    return {
        "valid": valid,
        "violations": violations,
        "p2_satisfied": p2_satisfied,
        "p2_violated": p2_violated,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_group_index(groups: list[list[dict]], sku: str) -> int | None:
    """Find which group index contains the given SKU."""
    for idx, group in enumerate(groups):
        if any(it["sku"] == sku for it in group):
            return idx
    return None


def _would_create_conflict(
    group_a_skus: set[str],
    group_b_skus: set[str],
    conflict_pairs: set[tuple[str, str]],
) -> bool:
    """Check if merging two groups would violate any must_not_pack constraint."""
    combined = group_a_skus | group_b_skus
    for (src, tgt) in conflict_pairs:
        if src in combined and tgt in combined:
            # Both conflicting items would be in the merged group
            return True
    return False


def _apply_weight_split(
    groups: list[list[dict]],
    shipping_limits: dict,
) -> list[list[dict]]:
    """Apply P0 shipping weight limits: split groups exceeding the weight limit.

    Iterates over groups and splits any group whose total weight exceeds
    max_weight_kg into sub-groups using _split_by_weight.
    """
    weight_limit = shipping_limits.get("max_weight_kg", 999999)
    if weight_limit >= 999999:
        return groups  # no meaningful limit

    result = []
    for group in groups:
        total_weight = sum(it.get("weight_kg", 0) for it in group)
        if total_weight > weight_limit and len(group) > 1:
            logger.info(
                f"P0 weight exceed: group total={total_weight}kg > "
                f"limit={weight_limit}kg → splitting by weight"
            )
            split = _split_by_weight(group, weight_limit)
            result.extend(split)
        else:
            result.append(group)

    return result


def _split_by_weight(
    items: list[dict],
    weight_limit: float,
) -> list[list[dict]]:
    """Split items into sub-groups that each stay within the weight limit.

    Uses a simple greedy approach: add items one by one, start a new group
    when the current group exceeds the limit.
    """
    groups = []
    current_group = []
    current_weight = 0.0

    # Sort by weight descending (pack heavy items first for better balance)
    sorted_items = sorted(items, key=lambda it: it.get("weight_kg", 0), reverse=True)

    for it in sorted_items:
        item_weight = it.get("weight_kg", 0)

        if current_weight + item_weight > weight_limit and current_group:
            # Start a new group
            groups.append(current_group)
            current_group = []
            current_weight = 0.0

        # If a single item exceeds the limit, it still goes in its own group
        # (P0: exceeding limits adds surcharges but doesn't prohibit)
        current_group.append(it)
        current_weight += item_weight

    if current_group:
        groups.append(current_group)

    return groups if groups else [items]
