-- Create the aprs database from a superuser account (e.g. postgres)
--  /usr/bin/createdb aprs

-- Run as admin on aprs database to enable postgis
-- You need only install the features you want
CREATE EXTENSION postgis;
CREATE EXTENSION postgis_topology;
--CREATE EXTENSION postgis_sfcgal;
CREATE EXTENSION fuzzystrmatch;
--CREATE EXTENSION address_standardizer;
--CREATE EXTENSION address_standardizer_data_us;
CREATE EXTENSION postgis_tiger_geocoder;

