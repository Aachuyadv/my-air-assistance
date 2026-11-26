"""Initial 20 monitored cities (name, state, latitude, longitude). Admins can add more."""
from __future__ import annotations

SEED_CITIES: tuple[tuple[str, str, float, float], ...] = (
    ("Delhi", "Delhi", 28.6139, 77.2090),
    ("Mumbai", "Maharashtra", 19.0760, 72.8777),
    ("Gurugram", "Haryana", 28.4595, 77.0266),
    ("Noida", "Uttar Pradesh", 28.5355, 77.3910),
    ("Ghaziabad", "Uttar Pradesh", 28.6692, 77.4538),
    ("Faridabad", "Haryana", 28.4089, 77.3178),
    ("Lucknow", "Uttar Pradesh", 26.8467, 80.9462),
    ("Kanpur", "Uttar Pradesh", 26.4499, 80.3319),
    ("Patna", "Bihar", 25.5941, 85.1376),
    ("Jaipur", "Rajasthan", 26.9124, 75.7873),
    ("Kolkata", "West Bengal", 22.5726, 88.3639),
    ("Chennai", "Tamil Nadu", 13.0827, 80.2707),
    ("Bengaluru", "Karnataka", 12.9716, 77.5946),
    ("Hyderabad", "Telangana", 17.3850, 78.4867),
    ("Chandigarh", "Chandigarh", 30.7333, 76.7794),
    ("Bhopal", "Madhya Pradesh", 23.2599, 77.4126),
    ("Indore", "Madhya Pradesh", 22.7196, 75.8577),
    ("Pune", "Maharashtra", 18.5204, 73.8567),
    ("Ahmedabad", "Gujarat", 23.0225, 72.5714),
    ("Ludhiana", "Punjab", 30.9010, 75.8573),
)
