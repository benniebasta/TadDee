"""3D relief from a 2D file, then ball-nose G-code for NC Studio.

Plain, deterministic code: same input and settings always give the same G-code.
Pipeline: load_artwork -> build_heightmap -> rough/finish toolpaths -> write_gcode.
"""
