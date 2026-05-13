"""
player.mixins — Behaviour mixins for IcosahedronPlayer.

Each mixin is a plain Python class (no Qt base class) that implements
one focused slice of IcosahedronPlayer's behaviour. IcosahedronPlayer inherits them all
via multiple inheritance. They communicate through shared instance
attributes set in IcosahedronPlayer.__init__ and init_ui.
"""
