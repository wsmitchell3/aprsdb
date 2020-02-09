# APRSDB Overview
#### by Bill Mitchell, AE0EE

## Introduction
APRSDB is a set of programs and code that work together to provide a way to store APRS data.  Analysis of this data can provide insight on radio propagation, network traffic, and identify potential problems in digipeater configuration.

This project was inspired by NG0E's VHF Propagation website, which uses APRS-IS data.  Unlike that system, APRSDB uses only local data, and is designed to be fully independent of the internet.  Furthermore, it analyzes all the packets it reeceives, where APRS-IS seems to remove duplicates---throwing out perfectly good data.

At this time APRSDB takes some work to set up and is only operational on Linux.  I am not prepared to support other operating systems, but all the tools are cross-platform so it should be feasible.

This platform is split into two major parts: the collection/database back-end, and the yet-to-be-released web front-end (_aprs-moose_).  These two parts are split because it should make keeping things updated easier.  For now, most GIS software should be able to interact with the data; _QGIS_ certainly can.  Common queries have been saved as views for easy access.

## How It Works
There are several steps involved in the storage, analysis, and display of APRS data.  Different open-source programs are used for these steps.

* aprsdb (back-end)
** _Direwolf_ (soundcard packet decoder)
** _aprs-python_ (Python3-based APRS parser)
** _aprsdb_ (Python3 code for database insertion)
** _Postgres_ (database) with _PostGIS_ extensions
* aprs-moose (front-end)
** _Apache_ (webserver)
** _MapServer_ (mapping backend)
** _GeoMoose_ (javascript map frontend)

If you are interested in more detailed spatial analysis, use a full GIS program, e.g. _QGIS_

Additional scripts, configuration files, and mapfiles provide a base from which to work without requiring expert knowledge of each of the programs in the stack.

## Installation
The above programs need to be installed.  On Debian-based systems try
`sudo apt-get install postgres postgis qgis apache2 git`

Get Direwolf and the Python dependencies
```bash
mkdir ~/Install
cd ~/Install
git clone https://github.com/wsmitchell3/aprs-python 
git clone https://github.com/wsmitchell3/aprsdb
git clone https://github.com/wb2osz/direwolf
sudo pip3 install ~/Install/aprs-python
sudo pip3 install psycopg2
sudo pip3 install gpsd-py3
```

Install Direwolf
```bash
cd ~/Install/direwolf
make
sudo make install
make install-conf
cd ~
```
You will need to edit the installed direwolf configuration to add your callsign, etc.

Enable PostGIS (as a superuser), then create the users _moose_ and _radio_.  We will later restrict permissions for these users.  If you want to customize the passwords for these accounts, edit the db_roles.sql file before running it.
```bash
$ sudo su postgres
# createdb aprs
# psql -d aprs -f /home/[user]/Install/aprsdb/enable_postgis.sql
# psql -d aprs -f /home/[user]/Install/aprsdb/db_roles.sql
# exit
```

Create the database tables, views, and indexes.  The default password for the radio user is found in db_roles.sql
```bash
$ psql -d aprs -h 127.0.0.1 -p 5432 --user radio -f ~/Install/aprsd/aprsdb_creation.sql
```

Update (restrict) the _moose_ access privileges (read-only) and grant the _radio_ access privileges (write, but not DB admin)
*NOTE!* This script is intended for use on a single-database system, and may well have adverse side-effects on other databases.  Use with care.
```bash
$ sudo su postgres
postgres$ psql -d aprs -f /home/[user]/Install/aprsdb/db_access_privs.sql
postgres$ exit
```

Copy *generic_aprsdb.conf* to _~/aprsdb.conf_, and update _~/aprsdb.conf_ with the appropriate _Receive station info_ and any configuration customizations you made.  If you don't want the config file in your home directory, it can be specified via the command line --config (-c) option.

## Usage
### Real-time on-air
Provide an audio input for direwolf.  This can either be through an SDR or by hooking up a radio's output to the line-in of your soundcard.  You will likely need to set this in the _direwolf.conf_ file.

Start direwolf.  I usually prefer to do this using the _screen_ command, in a way that invokes direwolf then immediately detaches:
```bash
$ screen -S direwolf -d -m direwolf
```
You can get back to the screen with
```bash
$ screen -r direwolf
```

Once direwolf is running, we can use the _kissutil_ program to give us the plaintext.  This can be sent to a file for later processing, sent straight into the database, or both.
```bash
$ # Option 2: direct to database
$ screen -S kissutil
$ kissutil | python ~/Install/aprsdb/aprsdb.py
```

### Off-line
Text output from kissutil or other raw APRS text can be fed directly into the database:
```bash
$ cat [input] | python ~/Install/aprsdb/aprsdb.py
```
