"""Exceptions for the Atmos Energy integration."""

class AtmosEnergyException(Exception):
    """Base exception for Atmos Energy integration."""

class AuthenticationError(AtmosEnergyException):
    """Authentication failed."""

class APIError(AtmosEnergyException):
    """API request failed."""

class DataParseError(AtmosEnergyException):
    """Failed to parse data."""
