"""Backup retention tier engine (NFM-3036).

Provides GFS (Grandfather-Father-Son) style tiered retention classification
for database backup files, along with the Pydantic configuration schema
that supports both the new ``retention`` object and the deprecated
``retentionDays`` flat integer.
"""
