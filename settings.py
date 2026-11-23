"""Edit the numbers here, then save the file in GitHub."""

TIMEZONE = "Europe/London"
CACHE_HOURS = 6
REFRESH_COOLDOWN_MINUTES = 15
LOOK_AHEAD_DAYS = 120
REQUEST_DELAY = 2
REQUEST_TIMEOUT = 20
USER_AGENT = "WembleyEventPlanner/1.0 (public event planning demo)"

LAYERS = ["Stadium", "Arena", "Theatre", "BOXPARK", "Cineworld", "LDO", "Calendar"]
CAPACITY = {"Stadium": 90000, "Arena": 12500, "Theatre": 2000}
FILL_RATE = {
    "stadium sports": .95, "stadium concert": .95,
    "arena concert": .80, "family show": .70, "theatre": .70,
    "other": .70,
}
RATINGS = ["Low", "Medium", "High", "Very High"]
ATTENDANCE_THRESHOLDS = [2000, 10000, 40000]
RULE_IMPACT = {
    "family film": "Medium", "major film": "Medium", "film release": "Low",
    "black friday": "High", "sale": "Medium", "outlet event": "Medium",
    "boxpark event": "Medium", "bank holiday": "Medium",
    "school holiday": "Low", "local event": "Low", "other": "Low",
}
# Matching titles are only indicators; verify local cinema screenings.
MAJOR_FILM_KEYWORDS = ["avengers", "avatar", "toy story", "frozen", "jumanji"]
WEEKEND_BUMP = 1
MULTIPLE_THINGS_BUMP = 1
SCHOOL_HOLIDAY_BUMP = 1
STADIUM_BUMP = 1
ACTIONS = {
    "Low": {"staff": 0, "prep": 0, "stock": 0},
    "Medium": {"staff": 1, "prep": 10, "stock": 10},
    "High": {"staff": 2, "prep": 25, "stock": 20},
    "Very High": {"staff": 4, "prep": 40, "stock": 35},
}
HOURS_BEFORE_DOORS = 2
ASSUMED_HOURS_DOORS_TO_FINISH = 4
HOURS_AFTER_FINISH = 1
COLOURS = {"Low": "#F5EBDD", "Medium": "#D6B99A",
           "High": "#BB2026", "Very High": "#181818"}

# The third item chooses the public-page reader, not a paid API.
SOURCES = {
    "Wembley Stadium": ("https://www.wembleystadium.com/events", "Stadium", "events"),
    "OVO Arena Wembley": ("https://www.ovoarena.co.uk/events", "Arena", "events"),
    "Troubadour": ("https://www.troubadourtheatres.com/wembley-park-theatre", "Theatre", "events"),
    "BOXPARK Wembley": ("https://www.boxpark.co.uk/venues/wembley/events", "BOXPARK", "events"),
    "Wembley Park": ("https://wembleypark.com/whats-on/", "Calendar", "events"),
    "Cineworld coming soon": ("https://www.cineworld.co.uk/films/", "Cineworld", "films"),
    "London Designer Outlet": ("https://www.londondesigneroutlet.com/offers/", "LDO", "offers"),
    "UK bank holidays": ("https://www.gov.uk/bank-holidays.json", "Calendar", "bank"),
    "Brent school holidays": ("https://www.brent.gov.uk/termdates", "Calendar", "school"),
}
