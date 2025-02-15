@echo off
pip install -q -r requirements.txt
echo Updating Runways...
python runway_selector.py
pause