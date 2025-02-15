#!/usr/bin/env python3

from typing7 import List, Optional, Tuple
import re
import math
from datetime import datetime, timezone


class Cloud:
    def __init__(self, cloud_type: str, altitude: int):
        self.type = cloud_type
        self.altitude = altitude

    @classmethod
    def from_token(cls, token: str) -> Optional['Cloud']:
        """
        Parse a cloud token (e.g. "FEW004" or "BKN040///") and return a Cloud object.
        Only the first three digits (hundreds of feet) are used.
        """
        m = re.match(r"^(FEW|SCT|BKN|OVC|VV)(\d{3})", token)
        if m:
            return cls(m.group(1), int(m.group(2)) * 100)
        return None

    def __repr__(self):
        return f"{self.type}{self.altitude}"


class Wind:
    def __init__(self, speed: int, direction: int | str, gust: Optional[int] = None, variable: Optional[Tuple[int, int]] = None):
        self.speed = speed
        self.direction = direction
        self.gust = gust
        self.variable = variable

    @classmethod
    def from_token(cls, token: str) -> 'Wind':
        """
        Parse wind tokens like "07013KT" or "VRB02KT" (with optional gust).
        """
        wind_re = re.compile(r"^(?P<dir>\d{3}|VRB)(?P<speed>\d{2,3})(?:G(?P<gust>\d{2,3}))?KT$")
        m = wind_re.match(token)
        if not m:
            raise ValueError(f"Invalid wind token: {token}")
        direction = m.group("dir")
        direction = int(direction) if direction.isdigit() else direction
        speed = int(m.group("speed"))
        gust = int(m.group("gust")) if m.group("gust") else None
        return cls(speed, direction, gust)

    @classmethod
    def parse_variable(cls, token: str) -> Tuple[int, int]:
        """
        Parse a wind variability token such as "340V060" into a tuple (min, max).
        """
        m = re.match(r"^(\d{3})V(\d{3})$", token)
        if not m:
            raise ValueError(f"Invalid variable wind token: {token}")
        return int(m.group(1)), int(m.group(2))

    def __repr__(self):
        gust_str = f"G{self.gust}" if self.gust else ""
        var_str = f" {self.variable[0]:03d}V{self.variable[1]:03d}" if self.variable else ""
        return f"{self.direction}{self.speed}{gust_str}KT{var_str}"



    def get_max_crosswind_component(self, runway_hdg: int) -> int:
        """
        Calculate the worst-case crosswind component for a given runway heading.

        If a gust is reported, that is used as the wind speed.

        For a fixed wind, the crosswind is calculated from the effective (acute) angle
        between the wind direction and runway heading.

        If the wind is variable (with boundaries given as absolute directions),
        then we check whether either perpendicular candidate (runway+90 or runway–90)
        lies within the reported variable range. If yes, we include a candidate with
        an effective angle of 90° (i.e. full wind speed) in the maximization.
        """
        # Use gust if available; otherwise, use reported wind speed.
        speed = self.gust if self.gust else self.speed

        # Helper: compute the raw difference (in degrees) between a wind direction and the runway heading,
        # normalized to the range [0, 180]. (For crosswind, an angle above 90° is mirrored.)
        def diff_angle(wd: int) -> float:
            d = abs(wd - runway_hdg) % 360
            if d > 180:
                d = 360 - d
            return d

        # Helper: effective (acute) angle for crosswind calculation.
        def effective_angle(d: float) -> float:
            return d if d <= 90 else 180 - d

        # Helper: crosswind component = speed * sin(effective angle)
        def crosswind_component(eff_angle: float) -> float:
            return speed * math.sin(math.radians(eff_angle))

        # If wind direction is VRB, worst-case is assumed to be full speed.
        if isinstance(self.direction, str) and self.direction == 'VRB':
            return speed

        # For a fixed wind direction, simply compute the crosswind.
        if not self.variable and isinstance(self.direction, int):
            d = diff_angle(self.direction)
            return math.ceil(crosswind_component(effective_angle(d)))
        low, high = self.variable
        # Normalize boundaries to [0, 360)
        low %= 360
        high %= 360

        # Helper: check whether angle x (in degrees) lies within the arc defined by low and high.
        # (The range may wrap around zero.)
        def in_range(x: int, low: int, high: int) -> bool:
            if low <= high:
                return low <= x <= high
            else:
                return x >= low or x <= high

        # We'll consider the candidates:
        # 1. The two reported variable boundaries.
        candidate_angles = [low, high]
        # 2. (Optionally) the fixed wind direction provided outside the variable range.
        #    (Some METARs include both a fixed direction and a variable range.)
        candidate_angles.append(self.direction)

        # 3. Check the two perpendicular candidates: runway+90 and runway–90.
        perp1 = (runway_hdg + 90) % 360
        perp2 = (runway_hdg - 90) % 360
        # If either perpendicular candidate is within the variable range, add an effective angle of 90°
        if in_range(perp1, low, high) or in_range(perp2, low, high):
            candidate_angles.append(90)  # use 90° as a marker

        # Now compute the effective crosswind from each candidate.
        # For numeric candidates (i.e. actual wind directions), we compute the effective angle:
        components = []
        for ang in candidate_angles:
            if ang == 90:  # our special marker indicating a perpendicular wind (worst-case)
                components.append(speed)  # sin(90°) = 1 => crosswind = full speed
            else:
                d = diff_angle(ang)
                eff = effective_angle(d)
                components.append(crosswind_component(eff))

        return math.ceil(max(components))

    def get_max_headwind_component(self, runway_hdg: int) -> int:
        """
        Calculate the maximum headwind component for a given runway heading.

        Uses the wind gust if reported.

        For a fixed wind, the headwind is computed as:
             headwind = speed * cos(relative_angle)
        (if the relative angle is greater than 90° the wind is tailwind and headwind is zero)

        For variable winds, candidate angles include:
          - the two variable boundaries,
          - the fixed wind direction (if present), and
          - the runway heading itself, if it falls within the variable range.

        The maximum candidate (i.e. the one with the smallest difference to the runway heading)
        is taken as the worst-case headwind.
        """
        # Use gust if available; otherwise, use reported wind speed.
        speed = self.gust if self.gust else self.speed

        # Helper: difference between a candidate wind direction and runway heading (in degrees),
        # normalized to [0, 180]. (Angles >90° yield tailwind, so headwind is zero.)
        def diff_angle(wd: int) -> float:
            d = abs(wd - runway_hdg) % 360
            if d > 180:
                d = 360 - d
            return d

        # For VRB winds, assume worst-case headwind is full (i.e. speed).
        if isinstance(self.direction, str) and self.direction == 'VRB':
            return speed

        # Fixed wind (no variable range):
        if not self.variable and isinstance(self.direction, int):
            d = diff_angle(self.direction)
            headwind = speed * math.cos(math.radians(d)) if d <= 90 else 0
            return math.ceil(headwind)

        # Otherwise, wind is variable.
        # Unpack variable boundaries and normalize them to [0, 360)
        low, high = self.variable
        low %= 360
        high %= 360

        # Helper: check whether an angle x (in degrees) lies within the arc defined by low and high.
        # (The range may wrap around 0°.)
        def in_range(x: int, low: int, high: int) -> bool:
            if low <= high:
                return low <= x <= high
            else:
                return x >= low or x <= high

        candidates = []
        # Candidate angles: the two reported boundaries and the fixed wind direction.
        candidate_angles = [low, high, self.direction]
        # Additionally, if the runway heading is within the variable range, add it.
        if in_range(runway_hdg, low, high):
            candidate_angles.append(runway_hdg)

        for ang in candidate_angles:
            if isinstance(ang, int):
                d = diff_angle(ang)
                # Only count as headwind if the relative angle is 90° or less.
                if d <= 90:
                    candidates.append(speed * math.cos(math.radians(d)))
                else:
                    candidates.append(0)
        return math.ceil(max(candidates))

class Precipitation:
    def __init__(self, precipitation_type: str, intensity: str):
        self.precipitation_type = precipitation_type
        self.intensity = intensity

    @classmethod
    def from_token(cls, token: str) -> Optional['Precipitation']:
        """
        Parse weather tokens such as "-SN", "+RA", or "-SHSN".
        The intensity is determined by the presence of "-" or "+".
        """
        weather_re = re.compile(r"^([-+]?)(TS|SH)?(RA|SN|DZ|GR|GS|PL|IC|UP)$")
        m = weather_re.match(token)
        if m:
            intensity_sym = m.group(1)
            if intensity_sym == "-":
                intensity = "light"
            elif intensity_sym == "+":
                intensity = "heavy"
            else:
                intensity = "moderate"
            ptype = m.group(3)
            return cls(ptype, intensity)
        return None

    def __repr__(self):
        return f"{self.intensity} {self.precipitation_type}"


def parse_temperature(token: str) -> Tuple[int, int]:
    """
    Parse a temperature/dewpoint token like "09/M03" or "M04/M07"
    and return a tuple (temperature, dewpoint).
    """
    m = re.match(r"^(M?\d{1,2})/(M?\d{1,2})$", token)
    if not m:
        raise ValueError(f"Invalid temperature token: {token}")
    def conv(t: str) -> int:
        return -int(t[1:]) if t.startswith("M") else int(t)
    return conv(m.group(1)), conv(m.group(2))


def parse_pressure(token: str) -> int:
    """
    Parse an altimeter token such as "Q1026" and return the pressure.
    """
    m = re.match(r"^Q(\d{4})$", token)
    if not m:
        raise ValueError(f"Invalid pressure token: {token}")
    return int(m.group(1))

class Metar:
    def __init__(self,
                 icao: str,
                 time: datetime,
                 wind: Wind,
                 visibility: int,
                 temperature: int,
                 dewpoint: int,
                 pressure: int,
                 precipitation: Optional[Precipitation],
                 clouds: List[Cloud]):
        self.icao = icao
        self.time = time
        self.wind = wind
        self.visibility = visibility
        self.temperature = temperature
        self.dewpoint = dewpoint
        self.pressure = pressure
        self.precipitation = precipitation
        self.clouds = clouds

    @classmethod
    def from_tokens(cls, tokens: List[str]) -> 'Metar':
        """
        Build a Metar object from a list of tokens.
        This function is responsible for splitting the METAR into parts and calling
        the appropriate constructors for each component.
        """
        idx = 0
        # 1. Station identifier (skip or store if needed)
        station = tokens[idx]
        idx += 1

        # 2. Time token (e.g. "141550Z")
        time_token = tokens[idx]
        idx += 1
        m_time = re.match(r"(\d{2})(\d{2})(\d{2})Z", time_token)
        if not m_time:
            raise ValueError("Invalid time token")
        day, hour, minute = map(int, m_time.groups())
        now = datetime.now(UTC)
        # Use current year and month (UTC) – note: no month rollover handling.
        metar_time = datetime(now.year, now.month, day, hour, minute, tzinfo=UTC)

        # 3. Optional "AUTO" token
        if idx < len(tokens) and tokens[idx] == "AUTO":
            idx += 1

        # 4. Wind token
        wind = Wind.from_token(tokens[idx])
        idx += 1

        # 5. Optional wind variability token
        if idx < len(tokens) and re.match(r"^\d{3}V\d{3}$", tokens[idx]):
            wind.variable = Wind.parse_variable(tokens[idx])
            idx += 1

        # 6. Visibility token
        vis_token = tokens[idx]
        if vis_token == "CAVOK":
            visibility = 9999
            idx += 1
        else:
            m_vis = re.match(r"(\d+)", vis_token)
            visibility = int(m_vis.group(1)) if m_vis else 0
            idx += 1

        # 7. Optional precipitation token
        precipitation = None
        if idx < len(tokens):
            ppt = Precipitation.from_token(tokens[idx])
            if ppt:
                precipitation = ppt
                idx += 1

        # 8. Cloud tokens
        clouds: List[Cloud] = []
        while idx < len(tokens):
            # Stop if the token looks like a temperature token
            if re.match(r"^(M?\d{1,2})/(M?\d{1,2})$", tokens[idx]):
                break
            # Also break on known remark indicators.
            if tokens[idx] in {"RMK", "TEMPO", "NOSIG", "BECMG", "NSC"}:
                if tokens[idx] == "NSC":
                    clouds = []  # NSC means no significant clouds.
                    idx += 1
                break
            cloud = Cloud.from_token(tokens[idx])
            if cloud:
                clouds.append(cloud)
                idx += 1
            else:
                break

        # 9. Temperature/dewpoint token
        temp_token = tokens[idx]
        temperature, dewpoint = parse_temperature(temp_token)
        idx += 1

        # 10. Pressure token
        pressure = parse_pressure(tokens[idx])
        idx += 1

        return cls(icao=station,
                   time=metar_time,
                   wind=wind,
                   visibility=visibility,
                   temperature=temperature,
                   dewpoint=dewpoint,
                   pressure=pressure,
                   precipitation=precipitation,
                   clouds=clouds)

    def __repr__(self):
        return (f"Metar(time={self.time}, wind={self.wind}, visibility={self.visibility}, "
                f"temperature={self.temperature}, dewpoint={self.dewpoint}, pressure={self.pressure}, "
                f"precipitation={self.precipitation}, clouds={self.clouds})")


def parse_metar(metar_str: str) -> Metar:
    """
    Split the METAR string into tokens and build a Metar object using the component classes.
    """
    tokens = metar_str.split()
    return Metar.from_tokens(tokens)

_url_cache = {}

def _fetch_metars(url: str) -> Dict[str, Metar]:
    """
    Fetches content from the given URL.

    If the same URL is requested within 5 minutes (300 seconds),
    returns the cached content instead of making a new HTTP request.

    Args:
        url (str): The URL to fetch.

    Returns:
        str: The content returned from the URL.

    Raises:
        requests.HTTPError: If the HTTP request returned an unsuccessful status code.
    """
    now = time.time()
    # Check if the URL is in the cache and still fresh.
    if url in _url_cache:
        cached_time, content = _url_cache[url]
        if now - cached_time < 300:  # 5 minutes = 300 seconds
            return content

    # If not cached or cache expired, perform a new request.
    response = requests.get(url)
    response.raise_for_status()  # Raise an error for bad responses.
    content = response.text  # or response.content for binary data

    metars = _parse_metars(content)

    # Cache the result with the current timestamp.
    _url_cache[url] = (now, metars)
    return metars

def _parse_metars(input: str) -> Dict[str, Metar]:
    metars: Dict[str, Metar] = {}
    for metar in input.splitlines():
        try:
            metar_obj = parse_metar(metar)
            metars[metar_obj.icao] = metar_obj
        except ValueError as e:
            print(f"Error parsing METAR: {e}")

    return metars

def fetch_metar(icao: str) -> Optional[Metar]:
    if icao.startswith("EN"):
        return _fetch_metars('https://metar.vatsim.net/EN')[icao]
    else:
        return _fetch_metars(f'https://metar.vatsim.net/metar.php?id={icao}')[icao]

if __name__ == '__main__':
    pass
