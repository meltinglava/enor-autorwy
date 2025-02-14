# ENOR Runway Selector

Automatic runway selector for Norwegian airports based on METAR data from VATSIM. 
Helps controllers by selecting optimal runways based on current weather conditions.

## Requirements

- Python 3.6 or higher

Install dependencies:
```
pip install -r requirements.txt
```

## Usage

The script has to be placed in the base folder of your sectorfile (where .ese, .sct, .rwy files are located).

Run the script either via terminal or via the provided .bat file.

If any of the following conditions are met at ENGM, the script will promt you to select a runway manually:
- Low temprature
- Low cloud layer
- Low visibility
- Fog reported
- RVR reported
- Snow reported
- Variable winds

For ENZV it will prioritize using runway 18/36 if crosswind is less than 15KT.

After script has been run, EuroScope can be opened and the runways will be preselected.

Note: The purpose of the script is to minimize the amount of manual runway input needed when starting up. It is not perfect and there can be conditions that warrants a different runway selection - such as height winds.
