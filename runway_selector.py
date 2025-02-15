#!/usr/bin/env python3

import math
from typing import Dict, List, Optional, Tuple, Union, Set, Tuple, Union, override
import os
import csv
from airport_config import PREFERRED_RUNWAYS, IGNORED_AIRPORTS
from datetime import datetime
import termcolor
import requests

import re

from weather import Metar, fetch_metar

_vatsim_atis_cache: Dict[str, Tuple[datetime, Dict]] = {}

def get_atis_runways(icao: str) -> Tuple[List[str], List[str]]:
    """Get active runways from ATIS."""
    url = 'https://data.vatsim.net/v3/vatsim-data.json'
    now = datetime.now()
    if icao in _vatsim_atis_cache and (now - _vatsim_atis_cache[icao][0]).seconds < 300:
        data = _vatsim_atis_cache[icao][1]
    else:
        data = requests.get(url).json()
        _vatsim_atis_cache[icao] = (now, data)

    arrival_runways = []
    departure_runways = []
    for atis in data['atis'].values():
        if atis['callsign'].startswith(icao):
            arr, dep = get_runway_config_from_atis(' '.join(atis['text_atis']))
            arrival_runways.extend(arr)
            departure_runways.extend(dep)
    return arrival_runways, departure_runways

SINGLE_RUNWAY = re.compile(r'RUNWAY IN USE ([0-9]{2}[LRC]*)')
ARRIVAL_RUNWAY = re.compile(r'APPROACH RUNWAY ([0-9]{2}[LRC]*)')
DEPARTURE_RUNWAY = re.compile(r'DEPARTURE RUNWAY ([0-9]{2}[LRC]*)')
MULTI_RUNWAY = re.compile(r'RUNWAYS ([0-9]{2}[LRC]*) AND ([0-9]{2}[LRC]*) IN USE')
def get_runway_config_from_atis(atis: str) -> Tuple[List[str], List[str]]:
    arrival_runways = []
    departure_runways = []

    single_runway_match = SINGLE_RUNWAY.search(atis)
    if single_runway_match:
        arrival_runways.append(single_runway_match.group(1))
        departure_runways.append(single_runway_match.group(1))

    arrival_runway_match = ARRIVAL_RUNWAY.search(atis)
    if arrival_runway_match:
        arrival_runways.append(arrival_runway_match.group(1))

    departure_runway_match = DEPARTURE_RUNWAY.search(atis)
    if departure_runway_match:
        departure_runways.append(departure_runway_match.group(1))

    multi_runway_match = MULTI_RUNWAY.search(atis)
    if multi_runway_match:
        arrival_runways.append(multi_runway_match.group(1))
        arrival_runways.append(multi_runway_match.group(2))
        departure_runways.append(multi_runway_match.group(1))
        departure_runways.append(multi_runway_match.group(2))

    if len(arrival_runways) == 0 or len(departure_runways) == 0:
        print('Unable to find runway in ATIS')

    return arrival_runways, departure_runways

# Helper function for colored text using termcolor
def c(text: str, color: str, attrs: List[str] = None) -> str:
    return termcolor.colored(text, color, attrs=attrs)

class Runway:
    """Class to store runway data."""
    identifiers: Tuple[str, str]
    headings: Tuple[int, int]

    def __init__(self, identifiers: Tuple[str, str], headings: Tuple[int, int]):
        self.identifiers = identifiers
        self.headings = headings

    def find_best_runway(self, metar: Metar):
        candiates: Dict[str, int] = {}
        for runway, direction in zip(self.identifiers, self.headings):
            headwind = metar.wind.get_max_headwind_component(direction);
            candiates[runway] = headwind
        if max(candiates.values()) > 5:
            return max(candiates, key=candiates.get)
        elif max(candiates.values()) > 2:
            candidate = max(candiates, key=candiates.get)
        # TODO: Take LVP into account
        return candidate


class Airport:
    """Class to store runway data for an airport."""

    identifier: str
    runway: Runway
    arrival_runways_in_use: List[str]


    def __init__(self, airport: str):
        self.identifier = airport

    def set_runway(self, runway: Runway):
        self.runway = runway

    def get_runway_in_use_from_atis(self) -> Tuple:



    def get_metar(self) -> Metar:
        metar = fetch_metar(self.identifier)
        if metar is None:
            raise ValueError("No METAR data available")
        return metar

    def get_default_runway(self):
        runway = PREFERRED_RUNWAYS.get(self.identifier)
        return runway

    def select_runway(self):
        try:
            metar = self.get_metar()
        except Exception as e:
            return self.get_default_runway()

        return self.runway.find_best_runway(metar) or self.get_default_runway()

class ENGM(Airport):
    runways: Dict[str, Runway]

    @override
    def set_runway(self, runway: Runway):
        self.runways[min(runway.identifiers)] = runway

    @override
    def select_runway(self):
        return super().select_runway()

class ENZV(Airport):
    runways: Dict[str, Runway]

    @override
    def set_runway(self, runway: Runway):
        self.runways[min(runway.identifiers)] = runway

    @override
    def select_runway(self):
        try:
            metar = self.get_metar()
        except Exception as e:
            return self.get_default_runway()
        main_crosswind = metar.wind.get_max_crosswind_component(self.runways['18'].headings[0])
        if main_crosswind > 15:
            self.main_runway = self.runways['18'].find_best_runway(metar)
            if main_crosswind < 10:
                self.secondary_runway = self.runways['10'].find_best_runway(metar)
        else:
            self.main_runway = self.runways['10'].find_best_runway(metar)


class AirportDefaultDict(dict):
    def __missing__(self, key):
        # When the key is missing, create a new Airport with the key as its identifier.
        if key == 'ENGM':
            return ENGM(key)
        elif key == 'ENZV':
            return ENZV(key)
        else:
            return Airport(key)

def parse_airports(filename: str) -> Dict[str, Airport]:
    airports = AirportDefaultDict()
    with open(filename, 'r') as f:
        f.readline()  # Skip header
        runways = csv.reader(f, delimiter=' ')
        for runway_row in runways:
            icao = runway_row[8]
            airport: Airport = airports[icao]
            identifiers = (runway_row[0], runway_row[1])
            headings = (int(runway_row[2]), int(runway_row[3]))
            runway = Runway(identifiers, headings)
            airport.set_runway(runway)
    return airports

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
    airports = parse_airports('runway.txt')
    for airport in airports:
        airport.select_runway()

def main_old():
    try:
        # Get METARs for all airports
        all_metars = get_all_metars()

        # Parse runway data
        runways = parse_airports('runway.txt')

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
