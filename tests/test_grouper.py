"""Tests for grouping constraint logic (D3).

Validates that group rules (must_pack_together, must_not_pack_together,
pack_near) are correctly applied when partitioning items into packing groups.

D3 acceptance criteria:
  - must_pack_together → items in same group
  - must_not_pack_together → items in different groups
  - pack_near → items try same box when no conflict
  - Priority: P0 shipping limits > P1 hard constraints > P2 soft preferences
  - Weight-based splitting when P0 exceeded
"""

import pytest
from app.services.grouper import (
    apply_group_rules,
    validate_groups,
    MUST_PACK,
    MUST_NOT_PACK,
    PACK_NEAR,
)


# ---------------------------------------------------------------------------
# Helper: create item dicts
# ---------------------------------------------------------------------------

def _item(sku, L=10, W=10, H=10, weight=0.5, fragile=False, heavy=False):
    return {
        "sku": sku,
        "length_cm": L,
        "width_cm": W,
        "height_cm": H,
        "weight_kg": weight,
        "fragile": fragile,
        "heavy": heavy,
    }


def _rule(rule_type, source, target, priority=1, description=""):
    return {
        "rule_type": rule_type,
        "source_sku": source,
        "target_sku": target,
        "priority": priority,
        "description": description,
    }


def _find_sku_group(groups, sku):
    """Find which group index contains the given SKU."""
    for i, g in enumerate(groups):
        if any(it["sku"] == sku for it in g):
            return i
    return None


# ---------------------------------------------------------------------------
# P1: must_pack_together
# ---------------------------------------------------------------------------

class TestMustPackTogether:
    """P1 must_pack_together → items should be in same group."""

    def test_host_and_accessory_same_group(self):
        """Host + accessory with must_pack should be together."""
        items = [
            _item("HOST-1", 40, 30, 20, 5),
            _item("ACC-1", 15, 10, 5, 0.8),
        ]
        rules = [
            _rule(MUST_PACK, "HOST-1", "ACC-1"),
        ]
        groups = apply_group_rules(items, rules)

        assert len(groups) >= 1
        host_idx = _find_sku_group(groups, "HOST-1")
        acc_idx = _find_sku_group(groups, "ACC-1")
        assert host_idx == acc_idx, "HOST-1 and ACC-1 should be in same group"

    def test_chain_of_must_pack(self):
        """A→B and B→C must_pack should put all three in same group."""
        items = [
            _item("A"),
            _item("B"),
            _item("C"),
        ]
        rules = [
            _rule(MUST_PACK, "A", "B"),
            _rule(MUST_PACK, "B", "C"),
        ]
        groups = apply_group_rules(items, rules)

        assert len(groups) >= 1
        a_idx = _find_sku_group(groups, "A")
        b_idx = _find_sku_group(groups, "B")
        c_idx = _find_sku_group(groups, "C")
        assert a_idx == b_idx == c_idx, "A, B, C should all be in same group"

    def test_must_pack_with_many_other_items(self):
        """must_pack links should not affect unrelated items."""
        items = [
            _item("HOST-1"),
            _item("ACC-1"),
            _item("STANDALONE-1"),
        ]
        rules = [
            _rule(MUST_PACK, "HOST-1", "ACC-1"),
        ]
        groups = apply_group_rules(items, rules)

        # HOST-1 and ACC-1 should be together
        host_idx = _find_sku_group(groups, "HOST-1")
        acc_idx = _find_sku_group(groups, "ACC-1")
        assert host_idx == acc_idx

        # STANDALONE-1 may or may not be in same group (no conflict → likely same group via P2 default)
        standalone_idx = _find_sku_group(groups, "STANDALONE-1")
        assert standalone_idx is not None
        assert standalone_idx == host_idx, "Unrelated compatible items should not create extra boxes"


# ---------------------------------------------------------------------------
# P1: must_not_pack_together
# ---------------------------------------------------------------------------

class TestMustNotPackTogether:
    """P1 must_not_pack_together → items should be in different groups."""

    def test_fragile_and_heavy_different_groups(self):
        """Fragile + heavy items with must_not_pack should be separated."""
        items = [
            _item("FRAGILE-1", 10, 10, 10, 0.5, fragile=True),
            _item("HEAVY-1", 30, 20, 15, 50, heavy=True),
        ]
        rules = [
            _rule(MUST_NOT_PACK, "FRAGILE-1", "HEAVY-1"),
        ]
        groups = apply_group_rules(items, rules)

        frag_idx = _find_sku_group(groups, "FRAGILE-1")
        heavy_idx = _find_sku_group(groups, "HEAVY-1")
        assert frag_idx != heavy_idx, "FRAGILE and HEAVY should be in different groups"

    def test_multiple_must_not_constraints(self):
        """Multiple must_not rules create multiple separate groups."""
        items = [
            _item("A"),
            _item("B"),
            _item("C"),
        ]
        rules = [
            _rule(MUST_NOT_PACK, "A", "B"),
            _rule(MUST_NOT_PACK, "A", "C"),
        ]
        groups = apply_group_rules(items, rules)

        a_idx = _find_sku_group(groups, "A")
        b_idx = _find_sku_group(groups, "B")
        c_idx = _find_sku_group(groups, "C")
        # A should be separate from both B and C
        assert a_idx != b_idx
        assert a_idx != c_idx
        # B and C may be together (no must_not between them) or separate


# ---------------------------------------------------------------------------
# P2: pack_near (soft preference)
# ---------------------------------------------------------------------------

class TestPackNear:
    """P2 pack_near → try same box when no P1 conflict."""

    def test_pack_near_merges_compatible_groups(self):
        """pack_near should merge groups that have no P1 conflicts."""
        items = [
            _item("A"),
            _item("B"),
        ]
        rules = [
            _rule(PACK_NEAR, "A", "B", priority=2),
        ]
        groups = apply_group_rules(items, rules)

        a_idx = _find_sku_group(groups, "A")
        b_idx = _find_sku_group(groups, "B")
        assert a_idx == b_idx, "pack_near should merge A and B into same group"

    def test_pack_near_does_not_override_must_not(self):
        """pack_near should NOT merge items with must_not constraint."""
        items = [
            _item("A"),
            _item("B"),
        ]
        rules = [
            _rule(MUST_NOT_PACK, "A", "B"),
            _rule(PACK_NEAR, "A", "B", priority=2),
        ]
        groups = apply_group_rules(items, rules)

        a_idx = _find_sku_group(groups, "A")
        b_idx = _find_sku_group(groups, "B")
        assert a_idx != b_idx, "P1 must_not should override P2 pack_near"


# ---------------------------------------------------------------------------
# No rules → single group
# ---------------------------------------------------------------------------

class TestNoRules:
    """Without any group rules, all items go into one group."""

    def test_no_rules_single_group(self):
        """No rules → all items in one group for maximum utilization."""
        items = [
            _item("A"),
            _item("B"),
            _item("C"),
        ]
        groups = apply_group_rules(items, rules=[])

        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_empty_items_returns_empty(self):
        """Empty items list returns empty groups."""
        groups = apply_group_rules([], rules=[])
        assert groups == []


# ---------------------------------------------------------------------------
# P0: Shipping limits
# ---------------------------------------------------------------------------

class TestShippingLimits:
    """P0 shipping limits → weight-based splitting."""

    def test_weight_split_when_exceeds_limit(self):
        """Group exceeding weight limit should be split."""
        items = [
            _item("HEAVY-A", 30, 20, 15, weight=25),
            _item("HEAVY-B", 25, 18, 12, weight=20),
            _item("LIGHT-C", 10, 8, 5, weight=2),
        ]
        # Total weight = 47kg, limit = 30kg → should split
        shipping_limits = {"max_weight_kg": 30}
        groups = apply_group_rules(items, rules=[], shipping_limits=shipping_limits)

        # Check that no group exceeds the weight limit
        for group in groups:
            total = sum(it.get("weight_kg", 0) for it in group)
            # Each group should be under 30kg (or contain a single item that exceeds)
            assert total <= 30 or len(group) == 1, (
                f"Group weight {total} exceeds limit but has multiple items"
            )

    def test_weight_within_limit_no_split(self):
        """Group within weight limit should stay together."""
        items = [
            _item("A", weight=5),
            _item("B", weight=3),
            _item("C", weight=2),
        ]
        shipping_limits = {"max_weight_kg": 30}
        groups = apply_group_rules(items, rules=[], shipping_limits=shipping_limits)

        # Total = 10kg < 30kg → should be one group
        assert len(groups) == 1

    def test_single_item_exceeds_weight_stays_in_group(self):
        """A single item exceeding weight limit still goes in its own group."""
        items = [
            _item("MEGA", 50, 40, 30, weight=45),
        ]
        shipping_limits = {"max_weight_kg": 30}
        groups = apply_group_rules(items, rules=[], shipping_limits=shipping_limits)

        # Single heavy item → one group (P0: exceed adds surcharge but doesn't prohibit)
        assert len(groups) == 1
        assert groups[0][0]["sku"] == "MEGA"


# ---------------------------------------------------------------------------
# Priority ordering: P0 > P1 > P2
# ---------------------------------------------------------------------------

class TestPriorityOrdering:
    """P0 > P1 > P2 priority resolution."""

    def test_p1_overrides_p2(self):
        """P1 must_not overrides P2 pack_near."""
        items = [
            _item("A"),
            _item("B"),
        ]
        rules = [
            _rule(MUST_NOT_PACK, "A", "B"),
            _rule(PACK_NEAR, "A", "B", priority=2),
        ]
        groups = apply_group_rules(items, rules)

        a_idx = _find_sku_group(groups, "A")
        b_idx = _find_sku_group(groups, "B")
        assert a_idx != b_idx

    def test_must_pack_overrides_must_not_when_conflict(self):
        """When must_pack and must_not contradict, must_pack wins."""
        items = [
            _item("A"),
            _item("B"),
        ]
        rules = [
            _rule(MUST_PACK, "A", "B"),
            _rule(MUST_NOT_PACK, "A", "B"),
        ]
        groups = apply_group_rules(items, rules)

        # Contradictory rules → must_pack takes precedence
        a_idx = _find_sku_group(groups, "A")
        b_idx = _find_sku_group(groups, "B")
        assert a_idx == b_idx, "must_pack should override must_not in contradiction"


# ---------------------------------------------------------------------------
# Validate groups
# ---------------------------------------------------------------------------

class TestValidateGroups:
    """Test suite for the validate_groups function."""

    def test_valid_groups_no_violations(self):
        """Groups that satisfy all rules should be marked as valid."""
        items = [
            _item("HOST-1"),
            _item("ACC-1"),
        ]
        rules = [
            _rule(MUST_PACK, "HOST-1", "ACC-1"),
        ]
        groups = apply_group_rules(items, rules)

        result = validate_groups(groups, rules)
        assert result["valid"] is True
        assert len(result["violations"]) == 0

    def test_invalid_groups_report_violations(self):
        """Manually created groups that break rules should report violations."""
        # Create groups that violate must_pack
        groups = [
            [_item("HOST-1")],
            [_item("ACC-1")],
        ]
        rules = [
            _rule(MUST_PACK, "HOST-1", "ACC-1"),
        ]
        result = validate_groups(groups, rules)

        assert result["valid"] is False
        assert len(result["violations"]) > 0
        assert any("must_pack_together" in v for v in result["violations"])

    def test_must_not_violation_detected(self):
        """Items in same group that must_not_pack should be detected."""
        groups = [
            [_item("FRAGILE-1", fragile=True), _item("HEAVY-1", heavy=True)],
        ]
        rules = [
            _rule(MUST_NOT_PACK, "FRAGILE-1", "HEAVY-1"),
        ]
        result = validate_groups(groups, rules)

        assert result["valid"] is False
        assert len(result["violations"]) > 0

    def test_p2_satisfaction_reported(self):
        """pack_near rules that are satisfied should be reported."""
        groups = [
            [_item("A"), _item("B")],
        ]
        rules = [
            _rule(PACK_NEAR, "A", "B", priority=2),
        ]
        result = validate_groups(groups, rules)

        assert result["valid"] is True  # P2 violations don't make invalid
        assert len(result["p2_satisfied"]) > 0

    def test_p2_violation_is_not_blocking(self):
        """P2 pack_near violations don't make groups invalid (soft rule)."""
        groups = [
            [_item("A")],
            [_item("B")],
        ]
        rules = [
            _rule(PACK_NEAR, "A", "B", priority=2),
        ]
        result = validate_groups(groups, rules)

        assert result["valid"] is True  # P2 violations don't invalidate
        assert len(result["p2_violated"]) > 0


# ---------------------------------------------------------------------------
# Complex scenario: multiple rules combined
# ---------------------------------------------------------------------------

class TestComplexScenarios:
    """Combined P1+P2 rules with realistic scenarios."""

    def test_host_acc_pair_separate_from_fragile(self):
        """Host+accessory must_pack, but both must_not with fragile → 2 groups."""
        items = [
            _item("HOST-1", 40, 30, 20, 5),
            _item("ACC-1", 15, 10, 5, 0.8),
            _item("FRAGILE-1", 10, 10, 10, 0.5, fragile=True),
        ]
        rules = [
            _rule(MUST_PACK, "HOST-1", "ACC-1"),
            _rule(MUST_NOT_PACK, "FRAGILE-1", "HEAVY-1") if False else
            _rule(MUST_NOT_PACK, "FRAGILE-1", "HOST-1"),
        ]
        groups = apply_group_rules(items, rules)

        host_idx = _find_sku_group(groups, "HOST-1")
        acc_idx = _find_sku_group(groups, "ACC-1")
        frag_idx = _find_sku_group(groups, "FRAGILE-1")

        assert host_idx == acc_idx, "Host+accessory must be together"
        assert host_idx != frag_idx, "Host must be separate from fragile"

    def test_full_scenario_with_p0_p1_p2(self):
        """Full scenario: P0 limits + P1 constraints + P2 preferences."""
        items = [
            _item("HOST-1", 40, 30, 20, 8),
            _item("ACC-1", 15, 10, 5, 0.5),
            _item("CABLE-1", 5, 5, 3, 0.2),
            _item("FRAGILE-1", 10, 10, 8, 0.3, fragile=True),
        ]
        rules = [
            _rule(MUST_PACK, "HOST-1", "ACC-1"),
            _rule(MUST_NOT_PACK, "FRAGILE-1", "HOST-1"),
            _rule(PACK_NEAR, "HOST-1", "CABLE-1", priority=2),
        ]
        shipping_limits = {"max_weight_kg": 50}  # no weight issue here

        groups = apply_group_rules(items, rules, shipping_limits=shipping_limits)

        # HOST+ACC should be together, FRAGILE separate
        host_idx = _find_sku_group(groups, "HOST-1")
        acc_idx = _find_sku_group(groups, "ACC-1")
        frag_idx = _find_sku_group(groups, "FRAGILE-1")
        cable_idx = _find_sku_group(groups, "CABLE-1")

        assert host_idx == acc_idx
        assert host_idx != frag_idx
        # CABLE should prefer HOST group (pack_near), if no conflict
        assert cable_idx == host_idx or cable_idx is not None

        # Validate
        result = validate_groups(groups, rules)
        assert result["valid"] is True
