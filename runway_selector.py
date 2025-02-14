import requests
import math
import re
from datetime import datetime
from typing import Dict, List, Tuple, Union
import os
import json
from dataclasses import dataclass
from airport_config import PREFERRED_RUNWAYS, IGNORED_AIRPORTS

# ANSI color codes
class Colors:
    HEADER = '\033[95m'     # Purple
    BLUE = '\033[94m'       # Blue
    GREEN = '\033[92m'      # Green
    YELLOW = '\033[93m'     # Yellow
    RED = '\033[91m'        # Red
    ENDC = '\033[0m'        # Reset color
    BOLD = '\033[1m'        # Bold

class Runway:
    def __init__(self, rwy1: str, rwy2: str, hdg1: int, hdg2: int, airport: str):
        self.rwy1 = rwy1
        self.rwy2 = rwy2
        self.hdg1 = hdg1
        self.hdg2 = hdg2
        self.airport = airport

def parse_runways(filename: str) -> Dict[str, List[Runway]]:
    runways = {}
    with open(filename, 'r') as f:
        for line in f:
            if line.strip() and not line.startswith('['):
                parts = line.strip().split()
                if len(parts) >= 9:
                    airport = parts[8]
                    runway = Runway(parts[0], parts[1], int(parts[2]), int(parts[3]), airport)
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
        Returns None if parsing fails
    """
    try:
        parts = metar.split()
        wind_data = {'raw_metar': metar}
        
        # Find wind information
        wind_found = False
        for i, part in enumerate(parts):
            if 'KT' in part and not part.startswith('Q'):
                if part.startswith('VRB'):
                    wind_data['direction'] = 'VRB'
                    wind_data['speed'] = int(part[3:5])
                else:
                    wind_data['direction'] = int(part[0:3])
                    wind_data['speed'] = int(part[3:5])
                wind_found = True
                # Remove check for variable wind range since we want to use the primary direction
                break
        
        # Extract visibility - look for it after the wind part
        for i, part in enumerate(parts):
            if 'KT' in part:  # Find the wind part
                # Look at the next part for visibility
                if i + 1 < len(parts):
                    next_part = parts[i + 1]
                    if next_part.isdigit():  # Pure digits = visibility
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
                if temp_str.startswith('M'):  # Negative temperature
                    wind_data['temperature'] = -int(temp_str[1:])
                else:
                    try:
                        wind_data['temperature'] = int(temp_str)
                    except ValueError:
                        continue  # Skip if not a valid temperature
                break
        
        # Check for snow conditions
        snow_conditions = {'SN', 'SNRA', 'SHSN', 'RASN', '-SN', '+SN'}
        wind_data['has_snow'] = any(cond in parts for cond in snow_conditions)
        
        # Check for low cloud layers (BKN or OVC at or below 200ft)
        wind_data['low_clouds'] = False
        for part in parts:
            if part.startswith(('BKN', 'OVC')):
                try:
                    height = int(part[3:6])  # Extract the height in hundreds of feet
                    if height <= 2:  # 200 feet or below
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
            return 0, wind_speed  # Assume maximum crosswind for variable winds
            
        # Calculate the relative wind angle
        # Normalize the difference between wind direction and runway heading to -180 to +180
        relative_angle = ((wind_dir - runway_hdg + 180 + 360) % 360) - 180
        
        # Calculate headwind (positive is headwind, negative is tailwind)
        # Use cosine of the relative angle - this gives positive for headwind (wind from ahead)
        # and negative for tailwind (wind from behind)
        headwind = wind_speed * math.cos(math.radians(relative_angle))
        
        # Calculate crosswind (absolute value)
        # Use sine of the relative angle - the absolute value gives the crosswind magnitude
        crosswind = abs(wind_speed * math.sin(math.radians(relative_angle)))
        
        return headwind, crosswind
        
    except Exception as e:
        print(f"Error calculating wind components: {e}")
        return 0, 0

def select_runway_enzv(wind_data: dict) -> Tuple[str, str, bool]:
    """Special case handler for ENZV which has two runway pairs.
    Prioritizes 18/36 runway pair and only uses 10/28 when crosswind on 18/36 exceeds 15KT."""
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
        
        # Select best runway from 18/36 pair
        if rwy18_hw > rwy36_hw:
            best_primary = ('18', rwy18_xw, rwy18_hw)
        else:
            best_primary = ('36', rwy36_xw, rwy36_hw)
            
        # If crosswind on primary runway pair is acceptable, use it
        if best_primary[1] <= 15:
            rwy = best_primary[0]
            if best_primary[1] > 10:  # Only show crosswind info if it's significant
                message = f"Selected runway {Colors.BLUE}{rwy}{Colors.ENDC} (crosswind: {Colors.YELLOW}{best_primary[1]:.0f}KT{Colors.ENDC}) with {wind_info}"
            else:
                message = f"Selected runway {Colors.BLUE}{rwy}{Colors.ENDC} with {wind_info}"
            return rwy, message, True
            
        # If we get here, check 10/28 pair as crosswind on 18/36 is too high
        rwy10_hw, rwy10_xw = calculate_wind_components(104, wind_dir, wind_speed)
        rwy28_hw, rwy28_xw = calculate_wind_components(284, wind_dir, wind_speed)
        
        # Select best runway from 10/28 pair
        if rwy10_hw > rwy28_hw:
            best_secondary = ('10', rwy10_xw, rwy10_hw)
        else:
            best_secondary = ('28', rwy28_xw, rwy28_hw)
            
        # Use the secondary runway
        message = f"High crosswind on 18/36 ({best_primary[1]:.0f}KT) - selected runway {Colors.BLUE}{best_secondary[0]}{Colors.ENDC}"
        if best_secondary[1] > 10:  # Add crosswind info if significant
            message += f" (crosswind: {Colors.YELLOW}{best_secondary[1]:.0f}KT{Colors.ENDC})"
        message += f" with {wind_info}"
        
        return best_secondary[0], message, True
        
    except Exception as e:
        return '18', f"Error calculating runway ({e}) - using preferred runway 18", True

def handle_variable_winds(airport: str, runway_data: List[Runway], wind_speed: int) -> Tuple[str, str]:
    """Handle variable wind conditions for any airport."""
    if airport in PREFERRED_RUNWAYS:
        selected = PREFERRED_RUNWAYS[airport]
        return selected, f"{Colors.YELLOW}Wind VRB{wind_speed}KT{Colors.ENDC} - using preferred runway {Colors.BLUE}{selected}{Colors.ENDC}"
    else:
        selected = runway_data[0].rwy1
        return selected, f"{Colors.YELLOW}Wind VRB{wind_speed}KT{Colors.ENDC} - defaulting to runway {Colors.BLUE}{selected}{Colors.ENDC}"

def check_engm_conditions(wind_data: dict) -> List[str]:
    """Check all conditions that require manual selection at ENGM."""
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

def select_runway(airport: str, runway_data: List[Runway], wind_data: dict) -> Tuple[Union[str, List[str]], str, bool, str]:
    message = ""
    should_print = False
    mode = ""  # Initialize mode
    
    if not wind_data:
        selected = PREFERRED_RUNWAYS.get(airport, runway_data[0].rwy1)
        message = f"No wind data available - {'using preferred runway' if airport in PREFERRED_RUNWAYS else 'defaulting to runway'} {Colors.BLUE}{selected}{Colors.ENDC}"
        return selected, message, True, mode
    
    # Handle calm winds (0 knots)
    if wind_data['speed'] == 0:
        selected = PREFERRED_RUNWAYS.get(airport, runway_data[0].rwy1)
        message = f"{Colors.GREEN}Calm winds{Colors.ENDC} - {'using preferred runway' if airport in PREFERRED_RUNWAYS else 'defaulting to runway'} {Colors.BLUE}{selected}{Colors.ENDC}"
        return selected, message, True, mode
    
    # Special case for ENGM
    if airport == 'ENGM':
        # Show current conditions if any
        conditions = check_engm_conditions(wind_data)
        if conditions:
            print(f"\n{Colors.HEADER}ENGM current conditions:{Colors.ENDC} {wind_data['raw_metar']}")
            print(f"\n{Colors.YELLOW}Conditions requiring manual selection:{Colors.ENDC}")
            for condition in conditions:
                print(f"- {condition}")
            # Force manual selection when conditions are detected
            runways, mode = get_engm_config()
            message = f"Manual selection required due to conditions. Using {Colors.BLUE}{mode}{Colors.ENDC} mode with runways {Colors.GREEN}{', '.join(runways)}{Colors.ENDC}"
            return runways, message, True, mode
        
        # Always show wind information for ENGM
        if wind_data.get('direction') != 'VRB':
            try:
                wind_dir = int(wind_data['direction'])
                wind_speed = int(wind_data['speed'])
                
                rwy01_hw = wind_speed * math.cos(math.radians(wind_dir - 7))
                rwy19_hw = wind_speed * math.cos(math.radians(wind_dir - 187))
                
                suggested_rwy = "01" if rwy01_hw > rwy19_hw else "19"
                print(f"\nBased on {format_wind_info(str(wind_dir), wind_speed)}:")
                print(f"Runway {Colors.BLUE}01{Colors.ENDC}: {abs(rwy01_hw):.1f}KT {'head' if rwy01_hw > 0 else 'tail'}wind")
                print(f"Runway {Colors.BLUE}19{Colors.ENDC}: {abs(rwy19_hw):.1f}KT {'head' if rwy19_hw > 0 else 'tail'}wind")
                print(f"Suggested configuration: Runway {Colors.GREEN}{suggested_rwy}{Colors.ENDC}")
            except (ValueError, TypeError) as e:
                print(f"{Colors.RED}Could not calculate wind components: {e}{Colors.ENDC}")
        
        # Get ENGM configuration if variable winds
        if wind_data.get('direction') == 'VRB':
            runways, mode = get_engm_config()
            message = f"Variable winds - manual selection required. Using {Colors.BLUE}{mode}{Colors.ENDC} mode with runways {Colors.GREEN}{', '.join(runways)}{Colors.ENDC}"
            return runways, message, True, mode
    
    # Handle variable winds for other airports
    if wind_data.get('direction') == 'VRB':
        selected, message = handle_variable_winds(airport, runway_data, wind_data['speed'])
        return selected, message, True, mode
    
    # For all other airports, find runway with best wind components
    best_runway = None
    best_score = float('-inf')  # Higher score is better
    min_crosswind = float('inf')
    wind_info = format_wind_info(str(wind_data['direction']), wind_data['speed'])
    
    try:
        wind_dir = int(wind_data['direction'])
        wind_speed = int(wind_data['speed'])
        
        for runway in runway_data:
            for rwy, hdg in [(runway.rwy1, runway.hdg1), (runway.rwy2, runway.hdg2)]:
                hw, xw = calculate_wind_components(hdg, wind_dir, wind_speed)
                # Score formula: prioritize headwind heavily over crosswind
                # Heavily penalize tailwind (negative headwind)
                # This ensures we always prefer a runway with headwind over one with tailwind
                score = hw - (xw / 2)  # Headwind is twice as important as crosswind
                
                if score > best_score or (score == best_score and xw < min_crosswind):
                    best_score = score
                    min_crosswind = xw
                    best_runway = rwy
                    best_headwind = hw
        
        # Show message for moderate and high crosswind conditions
        if min_crosswind > 20:
            message = f"{Colors.RED}High crosswind conditions{Colors.ENDC} - selected runway {Colors.BLUE}{best_runway}{Colors.ENDC} (crosswind: {Colors.RED}{min_crosswind:.0f}KT{Colors.ENDC}) with {wind_info}"
            should_print = True
        elif min_crosswind > 15:
            message = f"{Colors.YELLOW}Moderate crosswind{Colors.ENDC} - selected runway {Colors.BLUE}{best_runway}{Colors.ENDC} (crosswind: {Colors.YELLOW}{min_crosswind:.0f}KT{Colors.ENDC}) with {wind_info}"
            should_print = True
        else:
            message = f"Selected runway {Colors.BLUE}{best_runway}{Colors.ENDC} with {wind_info}"
            should_print = False
        
    except (ValueError, TypeError) as e:
        # Fallback to default runway if wind calculations fail
        best_runway = PREFERRED_RUNWAYS.get(airport, runway_data[0].rwy1)
        message = f"{Colors.RED}Error calculating wind components{Colors.ENDC} - {'using preferred runway' if airport in PREFERRED_RUNWAYS else 'defaulting to runway'} {Colors.BLUE}{best_runway}{Colors.ENDC}"
        should_print = True
    
    return best_runway, message, should_print, mode

def get_engm_config() -> Tuple[List[str], str]:
    """Get ENGM runway configuration from user input."""
    print(f"\n{Colors.HEADER}ENGM Runway Configuration:{Colors.ENDC}")
    print(f"1. {Colors.BLUE}19 MPO{Colors.ENDC} (Mixed Parallel Operations)")
    print(f"2. {Colors.BLUE}01 MPO{Colors.ENDC} (Mixed Parallel Operations)")
    print(f"3. {Colors.YELLOW}19 SPO{Colors.ENDC} (Segregated Parallel Operations - 19L DEP, 19R ARR)")
    print(f"4. {Colors.YELLOW}01 SPO{Colors.ENDC} (Segregated Parallel Operations - 01L DEP, 01R ARR)")
    print(f"5. {Colors.GREEN}19 SRO{Colors.ENDC} (Single Runway Operations - 19R)")
    print(f"6. {Colors.GREEN}01 SRO{Colors.ENDC} (Single Runway Operations - 01L)")
    
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
    """Update ENGM runway configuration based on mode.
    
    Operation types:
    1 = Departure
    0 = Arrival
    Not in file = Inactive
    """
    # Read the file
    with open(filename, 'r') as f:
        lines = f.readlines()
    
    # Remove existing ENGM entries
    updated_lines = [line for line in lines if not (
        line.startswith('ACTIVE_RUNWAY:ENGM:') or 
        line.startswith('ENGM_ARR') or 
        line.startswith('ENGM_DEP')
    )]
    
    if mode == "MPO":
        # Both runways active for both operations
        for runway in runways:
            updated_lines.extend([
                f'ACTIVE_RUNWAY:ENGM:{runway}:1\n',  # DEP
                f'ACTIVE_RUNWAY:ENGM:{runway}:0\n'   # ARR
            ])
    elif mode == "SPO":
        # 19L/01L for departures, 19R/01R for arrivals
        dep_rwy = runways[0]  # 19L/01L
        arr_rwy = runways[1]  # 19R/01R
        updated_lines.extend([
            f'ACTIVE_RUNWAY:ENGM:{dep_rwy}:1\n',  # Left runway DEP only
            f'ACTIVE_RUNWAY:ENGM:{arr_rwy}:0\n'   # Right runway ARR only
        ])
    else:  # SRO
        # Single runway for both operations
        updated_lines.extend([
            f'ACTIVE_RUNWAY:ENGM:{runways[0]}:1\n',  # DEP
            f'ACTIVE_RUNWAY:ENGM:{runways[0]}:0\n'   # ARR
        ])
    
    # Write back to file
    with open(filename, 'w') as f:
        f.writelines(updated_lines)

def update_rwy_file(filename: str, airport: str, runway: str):
    """Update runway file with new active runway configuration."""
    with open(filename, 'r+') as f:
        lines = [line for line in f.readlines() if not line.startswith(f'ACTIVE_RUNWAY:{airport}:')]
        # Add new active runway for both departure (1) and arrival (0)
        lines.extend([
            f'ACTIVE_RUNWAY:{airport}:{runway}:1\n',
            f'ACTIVE_RUNWAY:{airport}:{runway}:0\n'
        ])
        f.seek(0)
        f.writelines(lines)
        f.truncate()

def format_wind_info(direction: str, speed: Union[int, str]) -> str:
    """Format wind information with color based on wind speed."""
    try:
        speed_int = int(speed) if isinstance(speed, str) else speed
        if direction == 'VRB':
            return f"{Colors.GREEN}VRB{speed_int:02d}KT{Colors.ENDC}"
        else:
            color = (Colors.GREEN if speed_int < 10 else
                    Colors.YELLOW if speed_int < 20 else
                    Colors.RED)
            return f"{color}{direction:03d}@{speed_int:02d}KT{Colors.ENDC}"
    except (ValueError, TypeError):
        # Fallback for any parsing errors
        return f"{Colors.YELLOW}{direction}@{speed}KT{Colors.ENDC}"

def main():
    try:
        # Get METARs for all airports
        all_metars = get_all_metars()
        
        # Parse runway data
        runways = parse_runways('runway.txt')
        
        # Get list of .rwy files in current directory
        rwy_files = []
        for file in os.listdir():
            if file.endswith('.rwy'):
                rwy_files.append(file)
        
        if not rwy_files:
            print("No .rwy files found in current directory")
            return
        
        print("Updating Runways...")
        print("-" * 50)
        
        airports_without_data = []
        
        # Always process ENGM first if it exists in runways
        if 'ENGM' in runways:
            wind_data = all_metars.get('ENGM', {})  # Get ENGM weather data, empty dict if none
            selected_runway, message, should_print, mode = select_runway('ENGM', runways['ENGM'], wind_data)
            if mode:  # Only update if we got a mode back
                for rwy_file in rwy_files:
                    update_engm_runways(rwy_file, selected_runway, mode)
            if should_print:
                print(f"ENGM: {message}")
                print("-" * 50)  # Separator after ENGM configuration
        
        # Then process ENZV
        if 'ENZV' in all_metars:
            selected_runway, message, _ = select_runway_enzv(all_metars['ENZV'])
            for rwy_file in rwy_files:
                update_rwy_file(rwy_file, 'ENZV', selected_runway)
            print(f"ENZV: {message}")
        else:
            airports_without_data.append('ENZV')
        
        # Process other airports
        for airport in runways:
            if airport in ['ENZV', 'ENGM'] or airport in IGNORED_AIRPORTS:  # Skip already processed airports
                continue
                
            wind_data = all_metars.get(airport)
            if not wind_data:
                airports_without_data.append(airport)
                continue
            
            selected_runway, message, should_print, mode = select_runway(airport, runways[airport], wind_data)
            for rwy_file in rwy_files:
                update_rwy_file(rwy_file, airport, selected_runway)
            if should_print:
                print(f"{airport}: {message}")
        
        if airports_without_data:
            print(f"\n{Colors.YELLOW}No METAR data available for: {', '.join(airports_without_data)}{Colors.ENDC}")
        
        print("-" * 50)    
        print("Runway update complete!")
        
    except Exception as e:
        print(f"{Colors.RED}An error occurred: {str(e)}{Colors.ENDC}")
        raise  # Re-raise the exception to show the full traceback

if __name__ == "__main__":
    try:
        main()
        input("\nPress Enter to exit...")  # Add pause before exit
    except Exception as e:
        print(f"{Colors.RED}Error: {str(e)}{Colors.ENDC}")
        input("\nPress Enter to exit...")  # Add pause on error too
