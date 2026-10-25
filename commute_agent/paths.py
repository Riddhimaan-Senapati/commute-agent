from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
OTP_DIR = DATA / "otp"
OTP_CACHE = DATA / "cache" / "otp"
ALERT_SNAPSHOTS = DATA / "alert_snapshots"
EMISSION_FACTORS = DATA / "emission_factors.yaml"
TRAFFIC_FACTORS = DATA / "traffic_factors.yaml"
SHOWCASE_TRIPS = DATA / "showcase_trips.yaml"
PREFERENCES_DB = DATA / "preferences.db"
NUMINA_ZONES = RAW / "numina_zones.csv"
WEB = ROOT / "web"
EVAL = ROOT / "eval"
