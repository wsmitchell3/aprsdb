# aprsgps.py
# APRS-related GPS functions

import gpsd
import decimal

def getLoc2D():
    """Get 2D location (lat/long) from GPS
    returns: (latitude, longitude), DD.DDDD format (truncated) """
    gpsd.connect()
    myloc = gpsd.get_current()
    return((float(round(decimal.Decimal(myloc.lat),4)),float(round(decimal.Decimal(myloc.lon),4))))
