#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared exceptions for entity_profile_sys."""


class EntityProfileNetworkError(RuntimeError):
    """Raised when entity profile processing hits a network/API dependency failure."""
