"""Automated trading layer: broker abstraction, central risk manager, position sizing, trade management.

SAFE BY DEFAULT: demo mode, simulated broker, live trading disabled. Strategies never talk to a broker: they produce
signals, the arbitration engine and the central risk manager decide, and only the order manager places orders.
"""
