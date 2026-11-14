"""Find and download NOAA CORS base station data from the NOAA open-data bucket.

Paths (from https://geodesy.noaa.gov/CORS/data.shtml):
  daily obs : {bucket}/rinex/YYYY/DDD/ssss/ssssDDD0.YYo.gz   (RINEX 2.11)
  GPS nav   : {bucket}/rinex/YYYY/DDD/brdcDDD0.YYn.gz
  GLO nav   : {bucket}/rinex/YYYY/DDD/brdcDDD0.YYg.gz
  day list  : {bucket}/rinex/YYYY/DDD/YYYY.DDD.files.list
  coords    : {bucket}/coord/coord_20/itrf2020_xyz.comp.txt  (ARP, ITRF2020 @ 2020.0 + velocity)

Note: NOAA thins data older than 30 days to 30-second spacing.
"""
from __future__ import annotations

import datetime as dt
import gzip
import math
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .geo import ecef_to_geodetic, geodetic_to_ecef

COORD_PATH = "coord/coord_20/itrf2020_xyz.comp.txt"
COORD_MAX_AGE_S = 30 * 86400
TIMEOUT = 60


class CorsError(RuntimeError):
    pass


@dataclass
class Station:
    site: str
    xyz2020: tuple[float, float, float]
    vel_mm: tuple[float, float, float]
    status: str
    lat: float = 0.0
    lon: float = 0.0
    dist_km: float = 0.0

    def xyz_at(self, year: float) -> tuple[float, float, float]:
        dtyr = year - 2020.0
        return tuple(p + v / 1000.0 * dtyr for p, v in zip(self.xyz2020, self.vel_mm))


@dataclass
class BaseData:
    station: Station
    obs_files: list[Path]
    nav_files: list[Path]
    xyz: tuple[float, float, float]      # ITRF2020 ARP at epoch of data
    interval_s: float


class CorsClient:
    def __init__(self, bucket: str, cache_dir: str | Path, log=print):
        self.bucket = bucket.rstrip("/")
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.log = log
        self.session = requests.Session()

    # ---------- downloads ----------
    def _get(self, rel: str, dest: Path, max_age: float | None = None) -> Path | None:
        if dest.exists() and (max_age is None or time.time() - dest.stat().st_mtime < max_age):
            return dest
        url = f"{self.bucket}/{rel}"
        r = self.session.get(url, timeout=TIMEOUT)
        if r.status_code == 404 or r.status_code == 403:
            return None
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(r.content)
        tmp.replace(dest)
        return dest

    def _get_gz(self, rel: str, dest: Path) -> Path | None:
        if dest.exists():
            return dest
        gz = self._get(rel, dest.with_name(dest.name + ".gz"))
        if gz is None:
            return None
        with gzip.open(gz, "rb") as fi, open(dest, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        gz.unlink()
        return dest

    # ---------- stations ----------
    def stations(self) -> dict[str, Station]:
        f = self._get(COORD_PATH, self.cache / "itrf2020_xyz.comp.txt", COORD_MAX_AGE_S)
        if f is None:
            raise CorsError("Could not download the NOAA CORS coordinate list.")
        return parse_coord_file(f.read_text(errors="replace"))

    def sites_on_day(self, day: dt.date) -> set[str]:
        doy = day.timetuple().tm_yday
        rel = f"rinex/{day.year}/{doy:03d}/{day.year}.{doy:03d}.files.list"
        f = self._get(rel, self.cache / "lists" / f"{day.year}.{doy:03d}.files.list")
        if f is None:
            return set()
        return set(re.findall(rf"rinex/{day.year}/{doy:03d}/(\w{{4}})/\w{{4}}{doy:03d}0\.\d\do\.gz",
                              f.read_text(errors="replace")))

    def nearest(self, lat: float, lon: float, days: list[dt.date], max_km: float) -> list[Station]:
        allst = self.stations()
        avail = None
        for d in days:
            s = self.sites_on_day(d)
            avail = s if avail is None else avail & s
        avail = avail or set()
        x0, y0, z0 = geodetic_to_ecef(lat, lon, 0.0)
        out = []
        for site in avail:
            st = allst.get(site.upper())
            if not st:
                continue
            st.dist_km = math.dist((x0, y0, z0), st.xyz2020) / 1000.0
            st.lat, st.lon, _ = (float(v) for v in ecef_to_geodetic(*st.xyz2020))
            if st.dist_km <= max_km:
                out.append(st)
        return sorted(out, key=lambda s: s.dist_km)

    def download(self, st: Station, days: list[dt.date], year_frac: float) -> BaseData:
        obs, nav = [], []
        for d in days:
            doy, yy = d.timetuple().tm_yday, d.year % 100
            base = f"rinex/{d.year}/{doy:03d}"
            folder = self.cache / "rinex" / f"{d.year}" / f"{doy:03d}"
            name = f"{st.site.lower()}{doy:03d}0.{yy:02d}o"
            f = self._get_gz(f"{base}/{st.site.lower()}/{name}.gz", folder / name)
            if f is None:
                raise CorsError(f"No data for {st.site} on {d} (day {doy}).")
            obs.append(f)
            for ext in ("n", "g"):
                nname = f"brdc{doy:03d}0.{yy:02d}{ext}"
                nf = self._get_gz(f"{base}/{nname}.gz", folder / nname)
                if nf:
                    nav.append(nf)
        return BaseData(st, obs, nav, st.xyz_at(year_frac), rinex_interval(obs[0]))


def parse_coord_file(text: str) -> dict[str, Station]:
    out = {}
    for line in text.splitlines():
        p = line.split()
        if len(p) < 8 or not re.fullmatch(r"\w{4}", p[0]):
            continue
        try:
            float(p[1])
            xyz = tuple(float(v) for v in p[2:5])
            vel = tuple(float(v) for v in p[5:8])
        except ValueError:
            continue
        out[p[0].upper()] = Station(p[0].upper(), xyz, vel, p[-1])
    return out


def rinex_interval(path: Path) -> float:
    with open(path, errors="replace") as f:
        for line in f:
            if "INTERVAL" in line[60:]:
                return float(line[:10])
            if "END OF HEADER" in line[60:]:
                break
    return float("nan")


def days_covered(start: dt.datetime, end: dt.datetime) -> list[dt.date]:
    d, out = start.date(), []
    while d <= end.date():
        out.append(d)
        d += dt.timedelta(days=1)
    return out
