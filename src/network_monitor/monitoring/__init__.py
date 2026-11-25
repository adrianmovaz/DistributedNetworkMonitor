"""Monitoring coordination layer.

Future workers coordinate protocol clients and storage here; they must not access
HTTP client sockets or perform network operations while holding storage locks.
"""
