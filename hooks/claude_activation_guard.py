#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Retired activation guard, kept as a no-op until 0.28.

Settings written by the old ``install_activation_hooks.py --mode on`` still call
this file; exiting 0 lets every prompt and tool call through. Remove the hook
entries that reference this file from Claude settings (``/spec:doctor`` lists them).
"""

import sys

sys.stdin.read()
sys.exit(0)
