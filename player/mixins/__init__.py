"""
player.mixins — Behaviour mixins for SonarPlayer.

Each mixin is a plain Python class (no Qt base class) that implements
one focused slice of SonarPlayer's behaviour. SonarPlayer inherits them all
via multiple inheritance. They communicate through shared instance
attributes set in SonarPlayer.__init__ and init_ui.
"""
