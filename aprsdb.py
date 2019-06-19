#! /usr/bin/python

# 20180315_aprs-db.py
# by Bill Mitchell

# Functions for moving APRS data from aprslib into a psql database

import psycopg2 # Database interface
import aprslib # APRS parsing
import sys, os # General system utilities
import argparse # Parse arguments
import datetime, time, re, configparser, getpass # Time, regex, config, and password entry
import decimal # For truncating floats (e.g. lat/long)
from psycopg2 import sql
try:
    import gpsd # Use the GPS library if we have it
    import aprsgps # custom functions that require gpsd
    use_gps = True
except:
    use_gps = False  # GPS library not found (nor required)

# Parse arguments
parser = argparse.ArgumentParser()
parser.add_argument('-c', '--config', help='APRSDB config file')
args = parser.parse_args(sys.argv[1:])
# Read the config file
config = configparser.ConfigParser()
try:
    config.readfp(open(vars(args)['config'])) # Try to open the preferred config
except TypeError:
    try:
        config.readfp(open(os.path.expanduser("~") + os.path.sep + 'aprsdb.conf')) # Default to a locally-configured config
    except FileNotFoundError:
        try:
            config.readfp(open(os.path.split(sys.argv[0])[0] + 'aprsdb.conf')) # Fallback to the install directory
        except FileNotFoundError:
            config.readfp(open(os.path.join(os.path.split(sys.argv[0])[0], 'generic_aprsdb.conf'))) # Fallback to the generic one

# Set info for receive station
rxinfo={'call':config.get('aprs', 'rxcall'), 'symbol':config.get('aprs', 'rxsymbol'), 'symbol_table': config.get('aprs', 'rxtable'), 'latitude':config.get('aprs', 'latitude'), 'longitude':config.get('aprs','longitude')}


# Set up the timestamp regex
dwts = re.compile("^\[([0-9]*?) ([0-9]{4})([0-9]{2})([0-9]{2})_([0-9]{2})([0-9]{2})([0-9]{2})\]") # Match direwolf timestamp format [0 YYYYMMDD_hhmmss], grouped conveniently


# Set up data for database connection
session_id = 0 # Will attempt to update
my_schema = {'common':[], 'aprsdb_errs':[], 'location':[], 'map_entry':[], 'mic_e':[], 'thirdparty':[], 'uncompressed':[], 'compressed':[], 'status':[], 'object':[], 'wx':[], 'message':[], 'telemetry_message':[]} # Fields will be drawn from the database itself

conn=None
try: # Establish database connection
    if config.getboolean('psqlw', 'localuser')==True: # Use local user authentication
        conn=psycopg2.connect(dbname=config.get('psql', 'dbname'), user=getpass.getuser())
    else: # Use authentication from config
        conn = psycopg2.connect(dbname=config.get('psql', 'dbname'), user=config.get('psqlw', 'dbuser'), host=config.get('psqlw', 'dbhost'), port=config.get('psqlw','dbport'), password=config.get('psqlw', 'dbpass'))
except:
    print("Unable to connect to the database")
    raise
cur = conn.cursor() # Create a database cursor

try: # Establish session ID and time
    cur.execute("INSERT INTO sessions (start_time_utc_s, session_offset) VALUES (%s, %s) RETURNING session_id;", (time.time(), 0))
    session_id = cur.fetchone()[0]
    conn.commit()
except:
    print("Error entering session metadata to database")
    raise # DB connection is mission-critical, so error out here

for packet_format in my_schema: # Get the column names for each packet format
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s;", (packet_format,)) # Query column names for the table
    my_schema[packet_format] = [x[0] for x in cur.fetchall()] # Update column names

def check_rx_station(conn, parsed):
    """
    Check if the receiving station is in digis and location, adding if needed.
    conn: psycopg2 database connection
    parsed: dictionary with fields call, symbol, symbol_table, latitude, and longitude (e.g. rxinfo)
    returns: void
    """
    cur = conn.cursor()
    mylid = None
    [digi_id, mycall, mysym, mytable, oldloc] = [None for _ in range(5)]
    try:
        # Look to see if rx "digi" is known
        cur.execute("SELECT * FROM digis WHERE call=%s;", (parsed['call'],))
        myresult = cur.fetchone()
        # Get the info for a known digi
        if myresult != None:
            [digi_id, mycall, mysym, mytable, oldloc] = myresult
    
        # Compute the linestring for the rx location
        cur.execute("SELECT ST_SetSRID(ST_MakePoint(CAST(%s AS FLOAT), CAST(%s AS FLOAT)), 4326);", (parsed['longitude'],parsed['latitude']))
        myloc = cur.fetchone()[0]

        # If the rx digi is new, add it
        if digi_id == None:
            cur.execute("INSERT INTO digis (call, aprs_sym, aprs_table, loc) VALUES (%s, %s, %s, %s)", (parsed['call'], parsed['symbol'], parsed['symbol_table'], myloc))

        else: # Digi is known
            # Check if rx digi has changed info
            if not (myloc==oldloc and mysym == parsed['symbol'] and mytable == parsed['symbol_table']):
                # Update rx digi info if needed
                cur.execute("UPDATE digis SET aprs_sym=%s, aprs_table=%s, loc=%s WHERE digi_id=%s;", (parsed['symbol'], parsed['symbol_table'], myloc, digi_id))

        # Check for an existing entry in the location table
        cur.execute("SELECT lid FROM location WHERE linestring=%s;", (myloc,))
        mylid = cur.fetchone()
        if mylid == None: # If location is new, add it
            cur.execute("INSERT INTO location (latitude, longitude, linestring) VALUES(%s, %s, %s) RETURNING lid;", (parsed['latitude'], parsed['longitude'], myloc))
            mylid = cur.fetchone()
        mylid = mylid[0]

        conn.commit()
    except:
        conn.rollback()  # If errors are encountered, abort
        raise
    return mylid


def insert_sql_from_dict(table, mydict, codastring=''):
    """
    Create an SQL query for inserting the keys/values in mydict into table
    table: the table where values will be inserted
    mydict: dictionary of field:value pairs, all of which will be inserted
    codastring: string or sql.SQL to go at the query's end ('RETURNING pid')
    returns: psycopg2.sql.Composed query object for use with a cursor
    """
    myquery = sql.SQL("INSERT INTO {} ({}) VALUES ({}) {};").format(
            sql.Identifier(table),
            sql.SQL(', ').join(map(sql.Identifier, mydict.keys())),
            sql.SQL(', ').join(map(sql.Placeholder, mydict.keys())),
            sql.SQL(codastring))

    return(myquery)

def insert_digi(parsed, cur):
    """Insert values for a new digipeater.
    parsed: location packet for digi to be inserted
    cur: database cursor
    """
    # Check that required information is present
    for key in ['longitude','latitude','src','symbol','symbol_table']:
        if (key not in parsed.keys()):
            raise KeyError("Missing key: " + key) # Something went badly wrong
    # Data checked basic test, insert it.
    try:
        cur.execute("INSERT INTO digis (call, aprs_sym, aprs_table, loc) VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(CAST(%s AS FLOAT), CAST(%s AS FLOAT)), 4326));", (parsed['src'], parsed['symbol'], parsed['symbol_table'], parsed['longitude'], parsed['latitude']))
    except:
        raise

def check_update_digi(parsed, digi_id, cur):
    """
    Check if digipeater location has changed, updating if necessary
    parsed: aprslib parsed packet for digi to be checked/updated
    digi_id: digi_id from digis table to be checked against, and updated if needed
    cur: psycopg2 database cursor
    """
    # Check for requisite info, error if missing
    for key in ['longitude','latitude','src','symbol','symbol_table']:
        if (key not in parsed.keys()):
            raise KeyError("Missing key: " + key)

    # Get the old symbol for this digi
    cur.execute("SELECT aprs_sym FROM digis WHERE call=%s;", (parsed['src'],))
    old_sym = cur.fetchone()[0]
    
    if (old_sym == '#' and parsed['symbol'] != old_sym): # Ignore non-unique gates
        return

    # Get existing digi location, symbol, and symbol table
    cur.execute("SELECT loc, aprs_sym, aprs_table FROM digis WHERE digi_id=%s;", (digi_id,))
    [old_loc, old_sym, old_table] = cur.fetchone()

    # Prepare the new linestring with the digi's location
    if 'linestring' not in parsed.keys():
        cur.execute("SELECT ST_SetSRID(ST_MakePoint(CAST(%s AS FLOAT), CAST(%s AS FLOAT)), 4326);", (parsed['longitude'], parsed['latitude']))
        curr_loc = cur.fetchone()[0]
    else:
        curr_loc = parsed['linestring']

# Update digi record if it has moved or changed symbol [table]
    if ((curr_loc != old_loc) or (parsed['symbol'] != old_sym) or (parsed['symbol_table'] != old_table)):
        cur.execute("UPDATE digis SET loc=%s, aprs_sym=%s, aprs_table=%s WHERE digi_id=%s;", (curr_loc, parsed['symbol'], parsed['symbol_table'], digi_id))



def process_digi(parsed, cur):
    """Insert values for a new digipeater from a parsed packet
    parsed: an aprslib parsed packet
    cur: psycopg2 database cursor
    """

    # Check for crucial information
    if ('symbol' not in parsed.keys()):
        raise KeyError("Missing key: symbol")

    # Get existing record for digi
    cur.execute("SELECT digi_id, aprs_sym FROM digis WHERE call=%s;", (parsed['src'],))
    if (cur.rowcount == 0): # No matching digi
        insert_digi(parsed, cur)
    else: #Digis are forced unique by callsign, so can only be one row matching
        myresult = cur.fetchone()
        # Check if updates are needed and make them
        check_update_digi(parsed, myresult[0], cur)


def remove_NULL_path(path):
    """
    Remove NULL entries from APRS path
    path: path of parsed packet (list of strings)
    returns: list of digipeaters with NULL entries removed, in order
    """
    digipath = []
    for digi in path:
        if digi!='NULL':
            digipath.append(digi)
    return(digipath)
    
def process_path(path, packet_id, conn):
    """
    Insert the path routing info to the database
    path: Python list of path routing elements (e.g. N0QVC-1, WIDE1*, N0PBA-1,WIDE2-1)
    packet_id: packet_id from packets table corresponding to the entry being processed
    conn: psycopg2 database connection
    """
    cur = conn.cursor()
    path = aprslib.util.remove_WIDEn_N(path) # Get rid of WIDEn-N and asterisks
    if 'NULL' in path:
        path.remove('NULL') # remove first NULL value
    
    digi_src = False
    cur.execute("SELECT d1.call FROM common AS c1 INNER JOIN digis AS d1 ON d1.call=c1.src WHERE c1.pid=%s;", (packet_id, )) # Check if source is a known digi
    if cur.rowcount == 1: # 1 digi matched
        myresult = cur.fetchone()[0]
        path.insert(0, myresult) # Add first hop to inter-digi list
        digi_src = True


    if (path == None or path == []): # No route info
        return

    for call in path:  # Check for new digis!
        cur.execute("SELECT call FROM digis WHERE call=%s;", (call,))
        myresult = cur.fetchone()
        if myresult == None: # Found new digi
            cur.execute("INSERT INTO digis (call) VALUES (%s);", (call,))

    path.append(rxinfo['call']) # All packets end at RX site

    for i in range(len(path)-1): # Split path into single hops
        src = path[i]
        dest = path[i+1]
        hop = i+1
        if (digi_src): # 0-index for digi-sourced packets
            hop = i
        route_id = 0

        # Check if route exists, and get its ID if it does
        cur.execute("SELECT route_id FROM routes WHERE src=%s AND dest=%s;", (src, dest))
        myresult = cur.fetchone()
        if myresult != None: # Route exists
            route_id = myresult[0]
        else: # Route is new
            cur.execute("INSERT INTO routes (src, dest) VALUES (%s, %s) RETURNING route_id;", (src, dest))
            route_id = cur.fetchone()[0]

        # Add the hop and route to the paths table
        cur.execute("INSERT INTO paths (pid, hop, route_id) VALUES (%s, %s, %s);", (packet_id, hop, route_id)) 

    conn.commit()


def process_packet(packet, conn, rxtime=None, is_subpacket=False): 
    """
    Load an unparsed APRS packet into the database
    packet: APRS packet string
    conn: psycopg2 database connection
    rxtime: time packet was received, as seconds since epoch (1/1/1970); if omitted or wrong format, uses system clock
    is_subpacket: boolean flag for whether this is a sub-packet
    returns packet_id (positive bigint) if successful, negative integer if not
    """
    # Check for reasonable timestamp
    if (type(rxtime) is not float and type(rxtime) is not int or (rxtime is None)):
        rxtime=time.time()

    try:  # Parse it
        parsed = aprslib.parse(packet)
    except aprslib.exceptions.ParseError as pe:
        try: # Salvage what data we can from the header of an unparseable packet
            parsed = pe.parsed
            parsed['format']="parseerror"
        except: # Couldn't salvage anything
            print("Unable to partially parse packet: '" + packet + "' at time "+ str(rxtime)) # DEBUG
            return(-6) # Unable to partially parse packet

    except aprslib.exceptions.UnknownFormat as uf:
        # Save what headers we can if the format is unknown
        parsed = uf.parsed
        parsed['format']="unknown"
    except:
        # Something else went wrong
        print("Unable to parse packet") # DEBUG
        raise
        return(-5) # Unable to parse packet

    # Add the receiving station metadata to the parsed data
    parsed.update({'rxtime':rxtime, 'rxsession':session_id, 'is_subpacket':is_subpacket})

    # Send the parsed data on for further processing
    return(process_parsed(parsed, conn, rxtime, is_subpacket))

def process_parsed(parsed, conn, rxtime=time.time(), is_subpacket=False):
    """
    Load a parsed APRS packet into the database
    parsed: parsed aprs packet dictionary from aprslib
    conn: psycopg2 database connection
    rxtime: time packet was received, as seconds since epoch (1/1/1970); if omitted or wrong format, uses system clock
    is_subpacket: boolean flag for whether this is a sub-packet
    returns packet_id (positive bigint) if successful, negative integer if not
    """
    parsed['is_subpacket']=is_subpacket
    parsed['rxtime']=rxtime
    parsed['rx_loc_id'] = rxinfo['rx_loc_id'] # Get current loc_id for rx station
    if use_gps: #GPS features enabled (i.e. rover)
        if time.time() - rxinfo['gps_loc_time'] > 30: # seconds since prev. GPS point
            try:
                (mylat, mylon) = aprsgps.getLoc2D() # Get truncated coordinates
                rxinfo['latitude'] = mylat
                rxinfo['longitude'] = mylon
                rxinfo['rx_loc_id'] = check_rx_station(conn, rxinfo) # Update rx_station location, getting the location id back (for inserting in the common table)
                parsed['rx_loc_id'] = rxinfo['rx_loc_id'] # Put it into the parsed packet structure for insertion
                rxinfo['gps_loc_time'] = time.time() # Update the timestamp for the rx_loc
            except:
                print("Unable to get GPS coordinates") # DEBUG


    # Work around potential SQL reserved words, characters, and case-sensitivity
    parsed['dest'] = parsed.pop('to')
    parsed['src'] = parsed.pop('from')
    if parsed['format'] == 'mic-e':
        parsed['format'] = 'mic_e'
    if parsed['format'] == 'telemetry-message':
        parsed['format'] = 'telemetry_message'
    try:
        parsed['addressee'] = parsed['addresse']
    except:
        pass

    # Ensure all field names are lower-case
    parsed = dict((k.lower(), v) for k,v in parsed.items())
            

    # Insert data into common table
    # Start by finding the fields we have that go in common
    in_common = {x: parsed[x] for x in my_schema['common'] if x in parsed}
    try:
        # Put them in, and get the pid back for future reference
        cur.execute(insert_sql_from_dict('common', in_common, 'RETURNING pid'), in_common)
        mypacketid = cur.fetchone()[0]
        parsed['pid']=mypacketid
        conn.commit()
    except psycopg2.DataError as de:
        conn.rollback()
        print(de.pgerror)
        parsed['msg']=de.pgerror
        try:
            in_errs = {x:parsed[x] for x in my_schema['aprsdb_errs'] if x in parsed}
            cur.execute(insert_sql_from_dict('aprsdb_errs', in_errs), in_errs)
            conn.commit()
            return -7
        except:
            conn.rollback()
            raise
            return -8
    except:
        # Take it all back if something fails
        conn.rollback()
        raise
        return -2 # Unable to insert common table data
    
    # Check for digis
    if 'symbol' in parsed.keys():
        # Digis and igates are # and &
        if parsed['symbol'] in ['#','&']:
            process_digi(parsed, cur)
        # Watch out for digis not using standard symbols
        cur.execute("SELECT call FROM digis WHERE call=%s;", (parsed['src'],))
        if cur.fetchone() != None: # Call is a known digi
            if parsed['format'] not in ('object','item'): # Don't use digipeater data from objects or items
                process_digi(parsed, cur)

    # Handle third-party packets
    if parsed['format']=='thirdparty':
        # Source is a digi; check that it is known
        cur.execute("SELECT call FROM digis WHERE call=%s;", (parsed['src'],))
        myresult = cur.fetchone()
        if myresult == None: # Found new digi
            cur.execute("INSERT INTO digis (call) VALUES (%s);", (parsed['src'],))

        # Get the subpacket type
        parsed['subpacket_type'] = parsed['subpacket']['format']
        # Process the subpacket, getting its pid for back reference
        parsed['subpacket_id'] = process_parsed(parsed['subpacket'], conn, rxtime, True)
        if parsed['subpacket_id'] <0: # Error encountered
            conn.rollback()
            return -4 # Unable to handle third-party packet

    # Handle weather packets
    if 'weather' in parsed.keys():
        parsed['has_wx']=True
        for key in parsed['weather'].keys():
            parsed[key]=parsed['weather'][key]
        if parsed['format']!='wx': # Watch out for objects/positions with weather
            # Find wx fields in the packet and their values
            in_schema = {x: parsed[x] for x in my_schema['wx'] if x in parsed}
            for x in in_schema:
                if type(in_schema[x]) is dict:
                    # Stringify dictionaries
                    in_schema[x] = str(in_schema[x])
            # wx format packets will be entered later, but objects need their wx data entered now
            cur.execute(insert_sql_from_dict('wx', in_schema), in_schema)
            conn.commit()

    # Handle linestring creation and location entries
    if 'latitude' in parsed.keys() and 'longitude' in parsed.keys():
        try:
            # Get the linestring (text representation of geospatial data)
            cur.execute("SELECT ST_SetSRID(ST_MakePoint(CAST(%s AS FLOAT), CAST(%s AS FLOAT)), 4326);", (parsed['longitude'], parsed['latitude']))
            parsed['linestring'] = cur.fetchone()[0]

            # Check if we know this location
            cur.execute("SELECT lid FROM location WHERE latitude=%s AND longitude=%s;", (parsed['latitude'], parsed['longitude']))
            myresult = cur.fetchall()
            if len(myresult)==0: # No results found
                # Enter location data into a table
                in_schema = {x: parsed[x] for x in my_schema['location'] if x in parsed}
                for x in in_schema:
                    # Stringify dictionaries
                    if type(in_schema[x]) is dict:
                        in_schema[x] = str(in_schema[x])
                cur.execute(insert_sql_from_dict('location', in_schema, 'RETURNING lid'), in_schema)
                parsed['lid']=cur.fetchall()[0]
                conn.commit()
            else: # Location known, we just need its id
                parsed['lid']=myresult[0]

            # Enter symbol/table, course/speed, and PHG into map_entry table
            in_schema = {x: parsed[x] for x in my_schema['map_entry'] if x in parsed}
            for x in in_schema:
                if type(in_schema[x]) is dict:
                    in_schema[x] = str(in_schema[x])
            cur.execute(insert_sql_from_dict('map_entry', in_schema), in_schema)

            conn.commit()
        except:
            conn.rollback()
            raise


    # Insert format-specific data into proper table
    try:
        in_schema = {x: parsed[x] for x in my_schema[parsed['format']] if x in parsed}
    except KeyError:
        # Format missing; salvage path info
        conn.commit()
        # Process path, but only if the path is RF
        if is_subpacket==False:
            process_path(parsed['path'], mypacketid, conn)
        return mypacketid

    # Get the main packet data ready for insertion
    for x in in_schema:
        if type(in_schema[x]) is dict: # Stringify dictionaries
            in_schema[x] = str(in_schema[x])
    try:
        # Insert the packet's data into the format table
        cur.execute(insert_sql_from_dict(parsed['format'], in_schema), in_schema)
        conn.commit()
    except:
        conn.rollback()
        raise
        #return -3

    # Process path, only for RF paths
    if is_subpacket==False:
        process_path(parsed['path'], mypacketid, conn)

    return mypacketid


def set_session_offset (session_id, conn, start_year=2018, start_month=1, start_day=1, start_hour=0, start_minute=0, start_second=0, start_usec=0, start_tz=datetime.timezone.utc):
    """
    Set the session_offest field for a session, given a starting date/time.
    conn: psycopg2 database cursor
    start_tz: defaults to UTC
    Returns: True if successful, False if error
    """
    true_start = datetime.datetime(start_year, start_month, start_day, start_hour, start_minute, start_second, start_usec, tzinfo=start_tz)
    cur = conn.cursor()
    cur.execute("SELECT start_time_utc_s FROM sessions WHERE session_id=%s;", (session_id,))
    apparent_start = datetime.datetime.fromtimestamp(cur.fetchone()[0], tz=datetime.timezone.utc)

    dt = (true_start - apparent_start).total_seconds()
    try:
        cur.execute("UPDATE sessions SET session_offset=%s WHERE session_id=%s;", (dt, session_id))
        conn.commit()
    except:
        conn.rollback()
        return(False)
    return(True)

def get_direwolf_timestamp(packet):
    """
    Parse the timestamp from a direwolf packet header
    packet: the direwolf output line to decode
    returns: seconds since 1970 (possibly fractional) or None
    """
    # This really needs to be redone to pull the format from the config
    # Use %z flag for UTC offset
    # Use %s flag for epoch
    myresult = re.match(dwts, packet)
    try:
        (Y, m, d, H, M, S) = (int(x) for x in myresult.group(2,3,4,5,6,7))
    except:
        return None

    packet_time = datetime.datetime(Y, m, d, H, M, S).timestamp()-time.localtime().tm_gmtoff

def process_direwolf(line):
    """Parse direwolf output, returning the packet and epoch timestamp (or None).
    line: single line of raw Direwolf output
    returns: (channel=None, epoch=None, packet)
    """
    # Build regex for the direwolf header (radio channel and optional timestamp)
    # Save the packet, too
    m = re.match("^\[([0-9]*) *([0-9_\-]*)] (.*)", line)
    try:
        channel=int(m.group(1)) # Radio channel should be an integer
    except:
        try:
            return(None, None, m.group(3).strip()) # Return what the regex thinks is the packet
        except:
            return(None, None, line.strip()) # Return the whole line

    try:
        epoch=int(m.group(2)) # Look for a timestamp
    except:
        try:
            return(channel, None, m.group(3).strip()) # Channel and packet, no time
        except:
        # Should we check for a packet in group 2?
            return(channel, None, line.strip()) # Something probably went wrong;  return it anyway because maybe it actually works

    try:
        return(channel, epoch, m.group(3).strip()) # It's all there!  Wonderful!
    except:
        return(channel, epoch, line.strip()) # Something is probably wrong, but we'll return what we can and it can fail later

def get_valid_line():
    """Get a valid line from stdin
    returns: boolean: is_valid, string: text"""
    is_valid=False
    text='a'
    try:
        text = sys.stdin.readline()
        is_valid=True
    except UnicodeDecodeError as ude: # Not sure where these come from, but they can't stop the collector
        pass
    return (is_valid, text)


def hex_replace(matchobj):
    """Replacement formula for doing regex of non-printing ASCII characters
    matchobj: an re.match object, which should be a two-digit hex code
    returns: the character corresponding to the two-digit hex code, or null string
    """
    if int(matchobj.group(1), 16) != 0:
        return(chr(int(matchobj.group(1), 16)))
    else:
        return("")

def direwolf_escape(text):
    """Escapes direwolf non-printable characters"""
    # Use the hex_replace function to determine how to substitute the matched text (i.e. non-printing ASCII)
    return(re.sub(r"<0x([0-9A-Fa-f]{2})>", hex_replace, text))


if __name__ == "__main__": # Program is running directly
    rxinfo['rx_loc_id'] = check_rx_station(conn, rxinfo) # Test connectivity to database, and get rx_loc_id while we're at it
    print("Connection OK")
    lastline = 'a'
    rxinfo['gps_loc_time'] = time.time()
    while lastline != '': # Keep parsing packets from stdin
        is_valid=False
        while is_valid==False: # Keep trying to parse lines until one is valid
            (is_valid, lastline) = get_valid_line()
        if lastline.strip()=='q' or lastline.strip()=='2legit': # to quit
            exit(0)
        lastline = direwolf_escape(lastline) # Catch non-printing ASCII
        print(lastline) # DEBUG

        # Parse the direwolf output (channel, timestamp, packet)
        (channel, mytime, mypacket) = process_direwolf(lastline)
        # Process that output
        process_packet(mypacket, conn, rxtime=mytime)
