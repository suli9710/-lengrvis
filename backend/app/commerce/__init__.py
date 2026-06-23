"""Commercialization layer: plan-based feature gating (entitlements).

This package binds product capabilities to the active commercialization plan
(Free / Pro / Team-Self-hosted). High-risk capabilities such as remote desktop
control are gated to paid tiers here; entitlement never replaces the per-action
strong-approval flow enforced elsewhere in the policy engine.
"""
