"""Geodesy helpers (WGS84 / ITRF ellipsoid = GRS80/WGS84, difference is sub-millimetre)."""
from __future__ import annotations

import numpy as np

A = 6378137.0
F = 1 / 298.257223563
E2 = F * (2 - F)
FT_PER_M = 1 / 0.3048        # international foot
GPS_EPOCH_UNIX = 315964800.0  # 1980-01-06 00:00:00 UTC as unix seconds (no leap seconds)


def geodetic_to_ecef(lat, lon, h):
    lat, lon = np.radians(lat), np.radians(lon)
    n = A / np.sqrt(1 - E2 * np.sin(lat) ** 2)
    x = (n + h) * np.cos(lat) * np.cos(lon)
    y = (n + h) * np.cos(lat) * np.sin(lon)
    z = (n * (1 - E2) + h) * np.sin(lat)
    return x, y, z


def ecef_to_geodetic(x, y, z):
    x, y, z = np.asarray(x, float), np.asarray(y, float), np.asarray(z, float)
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1 - E2))
    for _ in range(6):
        n = A / np.sqrt(1 - E2 * np.sin(lat) ** 2)
        h = p / np.cos(lat) - n
        lat = np.arctan2(z, p * (1 - E2 * n / (n + h)))
    n = A / np.sqrt(1 - E2 * np.sin(lat) ** 2)
    h = p / np.cos(lat) - n
    return np.degrees(lat), np.degrees(lon), h


def radii(lat_deg):
    """Meridian (M) and prime-vertical (N) radii of curvature in metres."""
    s2 = np.sin(np.radians(lat_deg)) ** 2
    n = A / np.sqrt(1 - E2 * s2)
    m = A * (1 - E2) / (1 - E2 * s2) ** 1.5
    return m, n


def ned_offset(lat, lon, h, lat0, lon0, h0):
    """Small-offset north/east/down metres of (lat,lon,h) from (lat0,lon0,h0)."""
    m, n = radii(lat0)
    dn = np.radians(np.asarray(lat) - lat0) * (m + h0)
    de = np.radians(np.asarray(lon) - lon0) * (n + h0) * np.cos(np.radians(lat0))
    dd = -(np.asarray(h) - h0)
    return dn, de, dd


def apply_ned(lat, lon, h, dn, de, dd):
    """Move geodetic points by small north/east/down offsets (metres)."""
    lat = np.asarray(lat, float)
    m, n = radii(lat)
    lat2 = lat + np.degrees(np.asarray(dn) / (m + h))
    lon2 = np.asarray(lon) + np.degrees(np.asarray(de) / ((n + h) * np.cos(np.radians(lat))))
    return lat2, lon2, np.asarray(h) - np.asarray(dd)


def body_to_ned(roll, pitch, heading, vec):
    """Rotate a vehicle-frame vector (X fwd, Y right, Z down) into NED, per epoch.

    roll/pitch/heading in degrees (arrays of length N); vec is a length-3 tuple.
    Returns (N, 3) array.
    """
    r, p, y = (np.radians(np.asarray(a, float)) for a in (roll, pitch, heading))
    cr, sr, cp, sp, cy, sy = np.cos(r), np.sin(r), np.cos(p), np.sin(p), np.cos(y), np.sin(y)
    x, yv, z = vec
    n = cy * cp * x + (cy * sp * sr - sy * cr) * yv + (cy * sp * cr + sy * sr) * z
    e = sy * cp * x + (sy * sp * sr + cy * cr) * yv + (sy * sp * cr - cy * sr) * z
    d = -sp * x + cp * sr * yv + cp * cr * z
    return np.column_stack([n, e, d])


def gps_to_decimal_year(week: int, sow: float) -> float:
    import datetime as dt
    t = dt.datetime(1980, 1, 6) + dt.timedelta(weeks=week, seconds=sow)
    start = dt.datetime(t.year, 1, 1)
    end = dt.datetime(t.year + 1, 1, 1)
    return t.year + (t - start).total_seconds() / (end - start).total_seconds()


def gps_to_datetime(week: int, sow: float):
    """GPS time as a naive datetime on the GPS time scale (not UTC)."""
    import datetime as dt
    return dt.datetime(1980, 1, 6) + dt.timedelta(weeks=week, seconds=float(sow))
