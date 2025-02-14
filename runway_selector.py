#!/usr/bin/env python3

import requests
import math
import re
from datetime import datetime
from typing import Dict, List, Tuple, Union
import os
import json
from dataclasses import dataclass
from airport_config import PREFERRED_RUNWAYS, IGNORED_AIRPORTS
import termcolor

# Helper function for colored text using termcolor
def c(text: str, color: str, attrs: List[str] = None) -> str:
    return termcolor.colored(text, color, attrs=attrs)

class Airport:
    """Class to store runway data for an airport."""
    def __init__(self, rwy1: str, rwy2: str, hdg1: int, hdg2: int, airport: str):
        self.rwy1 = rwy1
        self.rwy2 = rwy2
        self.hdg1 = hdg1
        self.hdg2 = hdg2
        self.airport = airport

def parse_runways(filename: str) -> Dict[str, List[Airport]]:
    runways = {}
    with open(filename, 'r') as f:
        for line in f:
            if line.strip() and not line.startswith('['):
                parts = line.strip().split()
                if len(parts) >= 9:
                    airport = parts[8]
                    # Note: The Airport __init__ now expects 5 arguments.
                    runway = Airport(parts[0], parts[1], int(parts[2]), int(parts[3]), airport)
                    if airport not in runways:
                        runways[airport] = []
                    runways[airport].append(runway)
    return runways

def parse_metar(metar: str) -> dict:
    """Parse METAR string to extract wind information.

    Args:
        metar: Raw METAR string

    Returns:
        Dictionary containing:
        - direction: wind direction in degrees or 'VRB' for variable
        - speed: wind speed in knots
        - raw_metar: original METAR string
        - visibility: visibility in meters (if available)
        - temperature: temperature in Celsius
        - has_rvr: True if RVR is reported
        - has_snow: True if snow is reported
        - low_clouds: True if BKN or OVC at or below 200ft

    Returns None if parsing fails.
    """
    try:
        parts = metar.split()
        wind_data = {'raw_metar': metar}

        # Find wind information
        for part in parts:
            if 'KT' in part and not part.startswith('Q'):
                if part.startswith('VRB'):
                    wind_data['direction'] = 'VRB'
                    wind_data['speed'] = int(part[3:5])
                else:
                    wind_data['direction'] = int(part[0:3])
                    wind_data['speed'] = int(part[3:5])
                break

        # Extract visibility (look for it after the wind part)
        for i, part in enumerate(parts):
            if 'KT' in part:
                if i + 1 < len(parts):
                    next_part = parts[i + 1]
                    if next_part.isdigit():
                        wind_data['visibility'] = int(next_part)
                    elif next_part == '9999':
                        wind_data['visibility'] = 9999
                break

        # Check for RVR (R followed by runway number)
        wind_data['has_rvr'] = any(p.startswith('R') and len(p) > 3 and p[1:3].isdigit() for p in parts)

        # Extract temperature
        for part in parts:
            if '/' in part and not part.startswith('Q'):
                temp_str = part.split('/')[0]
                if temp_str.startswith('M'):
                    wind_data['temperature'] = -int(temp_str[1:])
                else:
                    try:
                        wind_data['temperature'] = int(temp_str)
                    except ValueError:
                        continue
                break

        # Check for snow conditions
        snow_conditions = {'SN', 'SNRA', 'SHSN', 'RASN', '-SN', '+SN'}
        wind_data['has_snow'] = any(cond in parts for cond in snow_conditions)

        # Check for low cloud layers (BKN or OVC at or below 200ft)
        wind_data['low_clouds'] = False
        for part in parts:
            if part.startswith(('BKN', 'OVC')):
                try:
                    height = int(part[3:6])
                    if height <= 2:
                        wind_data['low_clouds'] = True
                        break
                except ValueError:
                    continue

        return wind_data if 'direction' in wind_data and 'speed' in wind_data else None

    except Exception as e:
        print(f"Error parsing METAR: {e}")
        return None

def get_all_metars() -> Dict[str, dict]:
    metars = {}
    try:
        # Get all Norwegian METARs
        response = requests.get('https://metar.vatsim.net/EN')
        if response.status_code == 200:
            norwegian_metars = response.text.strip().split('\n')
            for metar in norwegian_metars:
                icao = metar.split()[0]
                wind_data = parse_metar(metar)
                if wind_data:
                    metars[icao] = wind_data

        # Get ESKS METAR separately
        response = requests.get('https://metar.vatsim.net/metar.php?id=ESKS')
        if response.status_code == 200:
            metar = response.text.strip()
            wind_data = parse_metar(metar)
            if wind_data:
                metars['ESKS'] = wind_data

    except Exception as e:
        print(f"Error fetching METARs: {e}")

    return metars

def calculate_wind_components(runway_hdg: int, wind_dir: int | str, wind_speed: int) -> Tuple[float, float]:
    try:
        # Handle variable winds
        if isinstance(wind_dir, str) and wind_dir == 'VRB':
            return 0, wind_speed

        # Normalize relative angle to -180...+180 degrees
        relative_angle = ((wind_dir - runway_hdg + 180 + 360) % 360) - 180

        headwind = wind_speed * math.cos(math.radians(relative_angle))
        crosswind = abs(wind_speed * math.sin(math.radians(relative_angle)))

        return headwind, crosswind

    except Exception as e:
        print(f"Error calculating wind components: {e}")
        return 0, 0

def format_wind_info(direction: str, speed: Union[int, str]) -> str:
    """Return formatted wind info with color using termcolor."""
    try:
        speed_int = int(speed) if isinstance(speed, str) else speed
        if direction == 'VRB':
            return c(f"VRB{speed_int:02d}KT", "green")
        else:
            color = "green" if speed_int < 10 else "yellow" if speed_int < 20 else "red"
            return c(f"{direction:03d}@{speed_int:02d}KT", color)
    except (ValueError, TypeError):
        return c(f"{direction}@{speed}KT", "yellow")

def select_runway_enzv(wind_data: dict) -> Tuple[str, str, bool]:
    """Special case for ENZV which has two runway pairs."""
    if not wind_data or wind_data['speed'] == 0:
        return '18', "Calm winds - using preferred runway 18", True

    if wind_data.get('direction') == 'VRB':
        return '18', f"Variable winds {wind_data['speed']}KT - using preferred runway 18", True

    try:
        wind_dir = int(wind_data['direction'])
        wind_speed = int(wind_data['speed'])
        wind_info = format_wind_info(str(wind_dir), wind_speed)

        # First check 18/36 pair
        rwy18_hw, rwy18_xw = calculate_wind_components(177, wind_dir, wind_speed)
        rwy36_hw, rwy36_xw = calculate_wind_components(357, wind_dir, wind_speed)

        if rwy18_hw > rwy36_hw:
            best_primary = ('18', rwy18_xw, rwy18_hw)
        else:
            best_primary = ('36', rwy36_xw, rwy36_hw)

        if best_primary[1] <= 15:
            rwy = best_primary[0]
            if best_primary[1] > 10:
                message = "Selected runway " + c(rwy, "blue") + " (crosswind: " + c(f"{best_primary[1]:.0f}KT", "yellow") + ") with " + wind_info
            else:
                message = "Selected runway " + c(rwy, "blue") + " with " + wind_info
            return rwy, message, True

        # Check secondary runway pair 10/28
        rwy10_hw, rwy10_xw = calculate_wind_components(104, wind_dir, wind_speed)
        rwy28_hw, rwy28_xw = calculate_wind_components(284, wind_dir, wind_speed)

        if rwy10_hw > rwy28_hw:
            best_secondary = ('10', rwy10_xw, rwy10_hw)
        else:
            best_secondary = ('28', rwy28_xw, rwy28_hw)

        message = "High crosswind on 18/36 (" + c(f"{best_primary[1]:.0f}KT", "red") + ") - selected runway " + c(best_secondary[0], "blue")
        if best_secondary[1] > 10:
            message += " (crosswind: " + c(f"{best_secondary[1]:.0f}KT", "yellow") + ")"
        message += " with " + wind_info

        return best_secondary[0], message, True

    except Exception as e:
        return '18', f"Error calculating runway ({e}) - using preferred runway 18", True

def handle_variable_winds(airport: str, runway_data: List[Airport], wind_speed: int) -> Tuple[str, str]:
    """Handle variable wind conditions for any airport."""
    if airport in PREFERRED_RUNWAYS:
        selected = PREFERRED_RUNWAYS[airport]
        return selected, c(f"Wind VRB{wind_speed}KT", "yellow") + " - using preferred runway " + c(selected, "blue")
    else:
        selected = runway_data[0].rwy1
        return selected, c(f"Wind VRB{wind_speed}KT", "yellow") + " - defaulting to runway " + c(selected, "blue")

def check_engm_conditions(wind_data: dict) -> List[str]:
    """Check conditions that require manual selection at ENGM."""
    conditions = []
    checks = [
        ('direction', 'VRB', "Variable winds"),
        ('raw_metar', 'FG', "Fog reported"),
        ('visibility', lambda v: v <= 2000, lambda v: f"Low visibility ({v}m)"),
        ('has_rvr', True, "RVR reported"),
        ('temperature', lambda t: t <= 4, lambda t: f"Low temperature ({t}°C)"),
        ('has_snow', True, "Snow reported"),
        ('low_clouds', True, "Low cloud layer (200ft or below)")
    ]

    for key, check, message in checks:
        value = wind_data.get(key)
        if value is None:
            continue
        if callable(check):
            if check(value):
                msg = message(value) if callable(message) else message
                conditions.append(msg)
        elif key == 'raw_metar' and check in value:
            conditions.append(message)
        elif value == check:
            conditions.append(message)

    return conditions

def select_runway(airport: str, runway_data: List[Airport], wind_data: dict) -> Tuple[Union[str, List[str]], str, bool, str]:
    message = ""
    should_print = False
    mode = ""

    if not wind_data:
        selected = PREFERRED_RUNWAYS.get(airport, runway_data[0].rwy1)
        message = "No wind data available - " + ("using preferred runway" if airport in PREFERRED_RUNWAYS else "defaulting to runway") + " " + c(selected, "blue")
        return selected, message, True, mode

    if wind_data['speed'] == 0:
        selected = PREFERRED_RUNWAYS.get(airport, runway_data[0].rwy1)
        message = c("Calm winds", "green") + " - " + ("using preferred runway" if airport in PREFERRED_RUNWAYS else "defaulting to runway") + " " + c(selected, "blue")
        return selected, message, True, mode

    if airport == 'ENGM':
        conditions = check_engm_conditions(wind_data)
        if conditions:
            print("\n" + c("ENGM current conditions:", "magenta", attrs=["bold"]) + " " + wind_data['raw_metar'])
            print("\n" + c("Conditions requiring manual selection:", "yellow"))
            for condition in conditions:
                print(f"- {condition}")
            runways, mode = get_engm_config()
            message = "Manual selection required due to conditions. Using " + c(mode, "blue") + " mode with runways " + c(", ".join(runways), "green")
            return runways, message, True, mode

        if wind_data.get('direction') != 'VRB':
            try:
                wind_dir = int(wind_data['direction'])
                wind_speed = int(wind_data['speed'])

                rwy01_hw = wind_speed * math.cos(math.radians(wind_dir - 7))
                rwy19_hw = wind_speed * math.cos(math.radians(wind_dir - 187))

                suggested_rwy = "01" if rwy01_hw > rwy19_hw else "19"
                print("\nBased on " + format_wind_info(str(wind_dir), wind_speed) + ":")
                print("Runway " + c("01", "blue") + ": " + f"{abs(rwy01_hw):.1f}KT " + ("head" if rwy01_hw > 0 else "tail") + "wind")
                print("Runway " + c("19", "blue") + ": " + f"{abs(rwy19_hw):.1f}KT " + ("head" if rwy19_hw > 0 else "tail") + "wind")
                print("Suggested configuration: Runway " + c(suggested_rwy, "green"))
            except (ValueError, TypeError) as e:
                print(c(f"Could not calculate wind components: {e}", "red"))

        if wind_data.get('direction') == 'VRB':
            runways, mode = get_engm_config()
            message = "Variable winds - manual selection required. Using " + c(mode, "blue") + " mode with runways " + c(", ".join(runways), "green")
            return runways, message, True, mode

    if wind_data.get('direction') == 'VRB':
        selected, message = handle_variable_winds(airport, runway_data, wind_data['speed'])
        return selected, message, True, mode

    best_runway = None
    best_score = float('-inf')
    min_crosswind = float('inf')
    wind_info = format_wind_info(str(wind_data['direction']), wind_data['speed'])

    try:
        wind_dir = int(wind_data['direction'])
        wind_speed = int(wind_data['speed'])

        for runway in runway_data:
            for rwy, hdg in [(runway.rwy1, runway.hdg1), (runway.rwy2, runway.hdg2)]:
                hw, xw = calculate_wind_components(hdg, wind_dir, wind_speed)
                score = hw - (xw / 2)

                if score > best_score or (score == best_score and xw < min_crosswind):
                    best_score = score
                    min_crosswind = xw
                    best_runway = rwy
                    best_headwind = hw

        if min_crosswind > 20:
            message = c("High crosswind conditions", "red") + " - selected runway " + c(best_runway, "blue") + " (crosswind: " + c(f"{min_crosswind:.0f}KT", "red") + ") with " + wind_info
            should_print = True
        elif min_crosswind > 15:
            message = c("Moderate crosswind", "yellow") + " - selected runway " + c(best_runway, "blue") + " (crosswind: " + c(f"{min_crosswind:.0f}KT", "yellow") + ") with " + wind_info
            should_print = True
        else:
            message = "Selected runway " + c(best_runway, "blue") + " with " + wind_info
            should_print = False

    except (ValueError, TypeError) as e:
        best_runway = PREFERRED_RUNWAYS.get(airport, runway_data[0].rwy1)
        message = c("Error calculating wind components", "red") + " - " + ("using preferred runway" if airport in PREFERRED_RUNWAYS else "defaulting to runway") + " " + c(best_runway, "blue")
        should_print = True

    return best_runway, message, should_print, mode

def get_engm_config() -> Tuple[List[str], str]:
    print("\n" + c("ENGM Runway Configuration:", "magenta", attrs=["bold"]))
    print("1. " + c("19 MPO", "blue") + " (Mixed Parallel Operations)")
    print("2. " + c("01 MPO", "blue") + " (Mixed Parallel Operations)")
    print("3. " + c("19 SPO", "yellow") + " (Segregated Parallel Operations - 19L DEP, 19R ARR)")
    print("4. " + c("01 SPO", "yellow") + " (Segregated Parallel Operations - 01L DEP, 01R ARR)")
    print("5. " + c("19 SRO", "green") + " (Single Runway Operations - 19R)")
    print("6. " + c("01 SRO", "green") + " (Single Runway Operations - 01L)")

    while True:
        try:
            choice = int(input("Select runway configuration (1-6): "))
            if 1 <= choice <= 6:
                runways = {
                    1: ["19L", "19R"],
                    2: ["01L", "01R"],
                    3: ["19L", "19R"],
                    4: ["01L", "01R"],
                    5: ["19R"],
                    6: ["01L"]
                }[choice]
                mode = "MPO" if choice <= 2 else "SPO" if choice <= 4 else "SRO"
                return runways, mode
        except ValueError:
            pass
        print("Invalid choice. Please enter a number between 1 and 6.")

def update_engm_runways(filename: str, runways: List[str], mode: str):
    """Update ENGM runway configuration based on mode."""
    with open(filename, 'r') as f:
        lines = f.readlines()

    updated_lines = [line for line in lines if not (
        line.startswith('ACTIVE_RUNWAY:ENGM:') or
        line.startswith('ENGM_ARR') or
        line.startswith('ENGM_DEP')
    )]

    if mode == "MPO":
        for runway in runways:
            updated_lines.extend([
                f'ACTIVE_RUNWAY:ENGM:{runway}:1\n',
                f'ACTIVE_RUNWAY:ENGM:{runway}:0\n'
            ])
    elif mode == "SPO":
        dep_rwy = runways[0]
        arr_rwy = runways[1]
        updated_lines.extend([
            f'ACTIVE_RUNWAY:ENGM:{dep_rwy}:1\n',
            f'ACTIVE_RUNWAY:ENGM:{arr_rwy}:0\n'
        ])
    else:  # SRO
        updated_lines.extend([
            f'ACTIVE_RUNWAY:ENGM:{runways[0]}:1\n',
            f'ACTIVE_RUNWAY:ENGM:{runways[0]}:0\n'
        ])

    with open(filename, 'w') as f:
        f.writelines(updated_lines)

def update_rwy_file(filename: str, airport: str, runway: str):
    """Update runway file with new active runway configuration."""
    with open(filename, 'r+') as f:
        lines = [line for line in f.readlines() if not line.startswith(f'ACTIVE_RUNWAY:{airport}:')]
        lines.extend([
            f'ACTIVE_RUNWAY:{airport}:{runway}:1\n',
            f'ACTIVE_RUNWAY:{airport}:{runway}:0\n'
        ])
        f.seek(0)
        f.writelines(lines)
        f.truncate()

def main():
    try:
        # Get METARs for all airports
        all_metars = get_all_metars()

        # Parse runway data
        runways = parse_runways('runway.txt')

        # Get list of .rwy files in current directory
        rwy_files = [file for file in os.listdir() if file.endswith('.rwy')]

        if not rwy_files:
            print("No .rwy files found in current directory")
            return

        print("Updating Runways...")
        print("-" * 50)

        airports_without_data = []

        # Process ENGM first
        if 'ENGM' in runways:
            wind_data = all_metars.get('ENGM', {})
            selected_runway, message, should_print, mode = select_runway('ENGM', runways['ENGM'], wind_data)
            if mode:
                for rwy_file in rwy_files:
                    update_engm_runways(rwy_file, selected_runway, mode)
            if should_print:
                print("ENGM: " + message)
                print("-" * 50)

        # Then process ENZV
        if 'ENZV' in all_metars:
            selected_runway, message, _ = select_runway_enzv(all_metars['ENZV'])
            for rwy_file in rwy_files:
                update_rwy_file(rwy_file, 'ENZV', selected_runway)
            print("ENZV: " + message)
        else:
            airports_without_data.append('ENZV')

        # Process other airports
        for airport in runways:
            if airport in ['ENZV', 'ENGM'] or airport in IGNORED_AIRPORTS:
                continue

            wind_data = all_metars.get(airport)
            if not wind_data:
                airports_without_data.append(airport)
                continue

            selected_runway, message, should_print, mode = select_runway(airport, runways[airport], wind_data)
            for rwy_file in rwy_files:
                update_rwy_file(rwy_file, airport, selected_runway)
            if should_print:
                print(airport + ": " + message)

        if airports_without_data:
            print("\n" + c("No METAR data available for: " + ", ".join(airports_without_data), "yellow"))

        print("-" * 50)
        print("Runway update complete!")

    except Exception as e:
        print(c(f"An error occurred: {str(e)}", "red"))
        raise

if __name__ == "__main__":
    try:
        main()
        input("\nPress Enter to exit...")
    except Exception as e:
        print(c(f"Error: {str(e)}", "red"))
        input("\nPress Enter to exit...")
