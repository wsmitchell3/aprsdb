CREATE TABLE sessions(
	session_id BIGSERIAL PRIMARY KEY,
	start_time_utc_s BIGINT,
	session_offset BIGINT DEFAULT 0
);

CREATE TABLE common(
	pid BIGSERIAL PRIMARY KEY,
	src VARCHAR(9),
	dest VARCHAR(9),
	path VARCHAR(64),
	via VARCHAR(16),
	format VARCHAR(64) NOT NULL,
	raw VARCHAR(256) NOT NULL,
	rxtime DOUBLE PRECISION,
	rxsession BIGINT REFERENCES sessions(session_id) ON DELETE CASCADE,
	is_subpacket BOOL DEFAULT False
);

CREATE TABLE location(
	lid BIGSERIAL PRIMARY KEY,
	latitude DOUBLE PRECISION,
	longitude DOUBLE PRECISION,
	altitude DOUBLE PRECISION,
	linestring geometry,
	posambiguity VARCHAR(8)
	CONSTRAINT loc_geom_point_chk check(st_geometrytype(linestring) = 'ST_Point'::text OR linestring IS NULL)
);

CREATE INDEX location_idx ON location USING GIST (linestring);

CREATE TABLE map_entry(
	pid BIGSERIAL PRIMARY KEY REFERENCES common(pid) ON DELETE CASCADE,
	lid BIGSERIAL REFERENCES location(lid) ON DELETE CASCADE,
	symbol CHAR,
	symbol_table CHAR,
	course FLOAT,
	speed FLOAT,
	phg VARCHAR(5)
);


CREATE TABLE uncompressed(
	pid BIGINT PRIMARY KEY REFERENCES common(pid) ON DELETE CASCADE,
	raw_timestamp varchar(24),
	messagecapable BOOL,
	has_wx BOOL DEFAULT False,
	comment VARCHAR(256),
	timestamp VARCHAR(24)
);

CREATE TABLE compressed(
	pid BIGINT PRIMARY KEY REFERENCES common(pid) ON DELETE CASCADE,
	radiorange FLOAT, 
	messagecapable BOOL, 
	comment VARCHAR(256), 
	timestamp VARCHAR(24), 
	raw_timestamp VARCHAR(24),
	gpsfixstatus VARCHAR(16), 
	telemetry VARCHAR(64)
);

CREATE TABLE object(
	pid BIGINT PRIMARY KEY REFERENCES common(pid) ON DELETE CASCADE,
	raw_timestamp VARCHAR(15),
	timestamp VARCHAR(15),
	object_format VARCHAR(32),
	comment VARCHAR(256),
	alive BOOL,
	object_name VARCHAR(16),
	has_wx BOOL DEFAULT False
);

CREATE TABLE mic_e(
	pid BIGINT PRIMARY KEY REFERENCES common(pid) ON DELETE CASCADE,
	mbits VARCHAR(64),
	telemetry VARCHAR(64),
	comment VARCHAR(256),
	daodatumbyte CHAR,
	mtype VARCHAR(64)
);

CREATE TABLE message(
	pid BIGINT PRIMARY KEY REFERENCES common(pid) ON DELETE CASCADE,
	msgNo VARCHAR(5), 
	response VARCHAR(24),
	addressee VARCHAR(24),
	message_text VARCHAR(128) 
);

CREATE TABLE status(
	pid BIGINT PRIMARY KEY REFERENCES common(pid) ON DELETE CASCADE,
	timestamp VARCHAR(24), 
	raw_timestamp VARCHAR(24),
	status VARCHAR(128) 
);

CREATE TABLE wx(
	pid BIGINT PRIMARY KEY REFERENCES common(pid) ON DELETE CASCADE,
	wx_raw_timestamp VARCHAR(15), 
	weather VARCHAR(256),
	wind_direction FLOAT,
	wind_speed FLOAT,
	wind_gust FLOAT,
	temperature FLOAT,
	rain_1h FLOAT,
  rain_24h FLOAT,
	rain_since_midnight FLOAT,
	humidity FLOAT,
	pressure FLOAT,
	luminosity FLOAT,
	snow FLOAT,
	rain_raw FLOAT
);

CREATE TABLE thirdparty(
  pid BIGINT PRIMARY KEY REFERENCES common(pid),
	subpacket VARCHAR(2048),
	subpacket_type VARCHAR(32),
	subpacket_id BIGINT REFERENCES common(pid) ON DELETE CASCADE
);

CREATE TABLE telemetry_message(
	pid BIGINT PRIMARY KEY REFERENCES common(pid),
	addressee VARCHAR(16),
	tPARM VARCHAR(256),
	tUNIT VARCHAR(256),
	tBITS VARCHAR(256),
	tEQNS VARCHAR(256),
	title VARCHAR(64)
);
	

CREATE TABLE digis(
	digi_id SERIAL PRIMARY KEY NOT NULL,
	call VARCHAR(9) NOT NULL,
	aprs_sym CHAR(1),
	aprs_table CHAR(1),
	loc GEOMETRY,
	CONSTRAINT digi_call UNIQUE(call),
	CONSTRAINT digi_loc_point_chk CHECK (st_geometrytype(loc) = 'ST_Point'::text OR loc IS NULL)
);

CREATE INDEX digi_spatial_idx ON digis USING GIST (loc);

CREATE TABLE routes(
	route_id SERIAL PRIMARY KEY,
	src VARCHAR(9) NOT NULL,
	dest VARCHAR(9) NOT NULL
);

CREATE TABLE paths(
	pid BIGINT REFERENCES common(pid) ON DELETE CASCADE,
	hop INTEGER,
	route_id INTEGER REFERENCES routes(route_id) ON DELETE CASCADE
);

CREATE VIEW rf_digi_counts AS 
	SELECT ROW_NUMBER() OVER (), d1.call, d1.aprs_sym, 
		d1.aprs_table, COUNT(c1.src), d1.loc  
	FROM digis AS d1 INNER JOIN common AS c1 ON c1.src=d1.call 
	WHERE c1.is_subpacket=False 
	GROUP BY d1.call, d1.aprs_sym, d1.aprs_table, d1.loc 
	ORDER BY count DESC, d1.call;

CREATE VIEW wide3 AS
	SELECT ROW_NUMBER() OVER (ORDER BY count(c1.src)),
		c1.src, COUNT(c1.src), m2.symbol, m2.symbol_table, l3.linestring 
	FROM common AS c1 
		INNER JOIN map_entry AS m2 ON c1.pid=m2.pid 
		INNER JOIN location AS l3 ON m2.lid=l3.lid 
	WHERE c1.path LIKE '%WIDE3%' 
	GROUP BY c1.src, l3.linestring, m2.symbol, m2.symbol_table 
	ORDER BY count(c1.src) DESC, src ASC;

CREATE VIEW first_hops AS
	SELECT ROW_NUMBER() OVER (ORDER BY c1.src),
		c1.src, 
		c1.format,
		d1.call AS digi,
		ST_SetSRID(ST_MakeLine(d1.loc, l1.linestring), 4326) AS hopline, 
		ST_DistanceSphere(d1.loc, l1.linestring)/1000 AS dist_km, 
		COUNT(*) 
	FROM common AS c1 
		INNER JOIN map_entry AS e1 ON c1.pid=e1.pid 
		INNER JOIN location AS l1 ON e1.lid=l1.lid 
		INNER JOIN paths AS p1 ON p1.pid=c1.pid 
		INNER JOIN routes AS r1 ON p1.route_id=r1.route_id 
		INNER JOIN digis AS d1 ON r1.src=d1.call 
	WHERE p1.hop=1 AND c1.is_subpacket=False 
		AND c1.src NOT IN (SELECT call FROM digis) 
	GROUP BY c1.src, c1.format, d1.call, hopline, dist_km 
	ORDER BY count DESC, c1.src, d1.call, dist_km DESC;

CREATE VIEW link_stats AS
	SELECT ROW_NUMBER() OVER (),
		ST_SetSRID(ST_MakeLine(src.loc, dest.loc), 4326), 
		ST_DistanceSphere(src.loc, dest.loc)/1000 AS dist_km, 
		src.call AS src, 
		dest.call AS dest, 
		COUNT(*) 
	FROM routes AS r1 INNER JOIN digis AS src ON r1.src = src.call 
		INNER JOIN digis AS dest ON r1.dest = dest.call 
		INNER JOIN paths AS p1 ON r1.route_id = p1.route_id 
	GROUP BY r1.route_id, src.call, dest.call, src.loc, dest.loc;

CREATE VIEW tx_igate_positions AS
	SELECT ROW_NUMBER() OVER (), 
		d1.call, 
		c1.src, 
		c1.format,
		l1.linestring, 
		COUNT(l1.linestring), 
		ST_DistanceSphere(l1.linestring, d1.loc)/1000 AS dist_km 
	FROM common AS c1 INNER JOIN thirdparty AS t1 ON c1.pid=t1.subpacket_id 
		INNER JOIN common AS c2 on t1.pid=c2.pid 
		INNER JOIN map_entry AS m1 ON c1.pid=m1.pid 
		INNER JOIN location AS l1 ON m1.lid=l1.lid 
		INNER JOIN digis AS d1 ON c2.src=d1.call 
	GROUP BY d1.call, c1.src, c1.format, l1.linestring, ST_DistanceSphere(l1.linestring, d1.loc)/1000;

CREATE VIEW tx_igate_counts AS
	SELECT ROW_NUMBER() OVER (),
		d1.call,
		d1.loc,
		COUNT(c1.src)
	FROM common AS c1 INNER JOIN digis AS d1 on d1.call=c1.src
	WHERE c1.format='thirdparty'
	GROUP BY d1.call, d1.loc;

CREATE VIEW rf_objects AS
	SELECT row_number() OVER (ORDER BY c1.src, o1.object_name),
		o1.object_name AS object_name, 
		l1.linestring AS linestring, 
		m1.symbol AS symbol, 
		m1.symbol_table AS symbol_table, 
		o1.comment AS comment, 
		count(o1.object_name), 
		c1.src AS src, 
		o1.has_wx AS has_wx 
	FROM object AS o1 
		INNER JOIN common AS c1 ON o1.pid=c1.pid
		INNER JOIN map_entry AS m1 ON o1.pid=m1.pid 
		INNER JOIN location AS l1 ON m1.lid=l1.lid 
	WHERE c1.is_subpacket=False 
	GROUP BY c1.src, o1.object_name, l1.linestring, m1.symbol, m1.symbol_table, o1.comment, o1.has_wx;

CREATE VIEW rf_positions AS
	SELECT row_number() OVER (), 
	CASE WHEN c1.format='object' THEN o1.object_name
		ELSE c1.src
		END AS src,
		c1.format, 
		m1.symbol, 
		m1.symbol_table, 
		count(l1.linestring), 
		l1.linestring 
	FROM map_entry AS m1 
		INNER JOIN common AS c1 ON m1.pid=c1.pid 
		INNER JOIN location AS l1 ON l1.lid=m1.lid 
		FULL JOIN object AS o1 ON m1.pid=o1.pid
	WHERE c1.is_subpacket=False 
	GROUP BY c1.src, c1.format, m1.symbol, m1.symbol_table, l1.linestring, o1.object_name;

CREATE VIEW digi_stats AS
	SELECT d1.*, 
		coalesce(inbound.count,0) AS heard,
		coalesce(outbound.count,0) AS heard_by
	FROM digis AS d1 
		FULL OUTER JOIN 
			(SELECT r1.src, count(coalesce(r1.src)) 
				FROM routes AS r1 FULL OUTER JOIN digis AS d1 ON r1.src=d1.call 
				GROUP BY r1.src) 
			AS outbound ON d1.call=outbound.src 
		FULL OUTER JOIN 
			(SELECT r2.dest, count(coalesce(r2.src)) 
				FROM routes AS r2 FULL OUTER JOIN digis AS d2 ON r2.dest=d2.call 
				GROUP BY r2.dest) 
			AS inbound ON d1.call=inbound.dest;

CREATE VIEW links_last_10 AS
	SELECT ROW_NUMBER() OVER(),
		ST_SetSRID(ST_MakeLine(src.loc, dest.loc), 4326),
		ST_DistanceSphere(src.loc, dest.loc)/1000 AS dist_km,
		src.call AS src,
		dest.call AS dest,
		COUNT(*)
	FROM common AS c1 INNER JOIN paths AS p1 ON c1.pid=p1.pid
		INNER JOIN routes AS r1 ON r1.route_id=p1.route_id
		INNER JOIN digis AS src ON r1.src=src.call
		INNER JOIN digis AS dest ON r1.dest=dest.call
	WHERE c1.rxtime > (SELECT max(rxtime)-600 FROM common)
		AND c1.is_subpacket=False
	GROUP BY r1.route_id, src.call, dest.call, src.loc, dest.loc;

CREATE VIEW links_last_60 AS
	SELECT ROW_NUMBER() OVER(),
		ST_SetSRID(ST_MakeLine(src.loc, dest.loc), 4326),
		ST_DistanceSphere(src.loc, dest.loc)/1000 AS dist_km,
		src.call AS src,
		dest.call AS dest,
		COUNT(*)
	FROM common AS c1 INNER JOIN paths AS p1 ON c1.pid=p1.pid
		INNER JOIN routes AS r1 ON r1.route_id=p1.route_id
		INNER JOIN digis AS src ON r1.src=src.call
		INNER JOIN digis AS dest ON r1.dest=dest.call
	WHERE c1.rxtime > (SELECT max(rxtime)-3600 FROM common)
		AND c1.is_subpacket=False
	GROUP BY r1.route_id, src.call, dest.call, src.loc, dest.loc;
