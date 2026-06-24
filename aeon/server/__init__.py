"""Transport. Moves serialized world state out to the browser and god-actions back
in. Holds no simulation logic — every mutation routes through Engine.god_action,
which uses the same validated directive path as the world-spirit.
"""
