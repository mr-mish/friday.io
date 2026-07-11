"""Autonomy: FRIDAY acting without being prompted.

Schedules and file triggers fire prompts; each fires in its own isolated
agent session under a hard rule — anything the permission gate would ask
about is auto-denied and reported to the inbox instead. Unattended runs
never self-approve.
"""
