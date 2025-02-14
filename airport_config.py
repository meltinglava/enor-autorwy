"""
Configuration file for Norwegian airport runway preferences and ignored airports.
"""

# Preferred runways for when wind direction cannot be determined
PREFERRED_RUNWAYS = {
    'ENCN': '21',
    'ENTO': '18',
    'ENRY': '30',
    'ENNO': '12',
    'ENZV': '18',
    'ENHD': '13',
    'ENBR': '17',
    'ENSO': '14',
    'ENSD': '26',
    'ENSG': '24',
    'ENFL': '07',
    'ENRO': '31',
    'ENVA': '09',
    'ENAL': '24',
    'ENML': '07',
    'ENKB': '07',
    'ENOL': '15',
    'ENBN': '03',
    'ENRA': '31',
    'ENBO': '07',
    'ENLK': '02',
    'ENEV': '17',
    'ENAN': '14',
    'ENNA': '34',
    'ENAT': '14',
    'ENTC': '18',
    'ENKR': '23',
    'ENSH': '36',
    'ENDU': '28',
    'ENMS': '33'
}

# Airports to ignore
IGNORED_AIRPORTS = {
    'ENRE', 'ENGK', 'ENLI', 'ENKJ', 'ENHA', 
    'ENEG', 'ENJA', 'ENBM', 'ENAX'
}
