-- Script for handling Postgres user access permissions for APRSDB user moose
REVOKE ALL ON SCHEMA public FROM public; -- Restrict db access (esp. CREATE)
REVOKE ALL ON SCHEMA public FROM moose; 
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM moose;
GRANT USAGE ON SCHEMA public TO PUBLIC;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO PUBLIC;
-- Give the new user SELECT privileges on public tables
GRANT USAGE ON SCHEMA public TO moose;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO moose;
GRANT ALL ON DATABASE aprs TO radio;
GRANT ALL ON SCHEMA public TO radio;
GRANT ALL ON ALL TABLES IN SCHEMA public TO radio;

