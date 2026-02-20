def grid_xyz_to_ned(move: tuple[float, float, float]) -> tuple[float, float, float]:
    """Map VLM grid move (x=E, y=N, z=UP) to NED tuple (N, E, D)."""
    x, y, z = float(move[0]), float(move[1]), float(move[2])
    n_coord = y
    e_coord = x
    d_coord = -z
    return n_coord, e_coord, d_coord
