"""Resolution Engine — the single source of truth for dice, numbers, and state.

Per design §4.0: the program owns dice/state/judgment; the AI never touches numbers.
Nothing in this package imports the AI layer.
"""
