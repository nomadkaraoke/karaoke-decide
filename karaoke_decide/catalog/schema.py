"""SQLite schema for catalog.db (per-song facets, artists, similarity, KaraokeNerds id map)."""

# 2: songs.vr_* + artists.vr_* (vocal range, Phase 3c). Additive; readers select by column name.
CATALOG_SCHEMA_VERSION = 2

DDL = """
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE songs (
    song_key TEXT PRIMARY KEY,
    artist TEXT,
    title TEXT,
    artist_key TEXT,
    brand_count INTEGER,
    is_classic INTEGER,
    explicit INTEGER,
    duration_ms INTEGER,
    year INTEGER,
    decade TEXT,
    lb_listeners INTEGER,
    spotify_pop INTEGER,
    popularity INTEGER,
    energy REAL,
    valence REAL,
    danceability REAL,
    tempo REAL,
    mood TEXT,
    lead_gender TEXT,
    genres_json TEXT,
    in_kn INTEGER,
    vr_low REAL,
    vr_high REAL,
    vr_p10 REAL,
    vr_p50 REAL,
    vr_p90 REAL,
    vr_tier TEXT,
    vr_octave_conf REAL,
    vr_duet INTEGER
);

CREATE TABLE song_genres (
    song_key TEXT,
    genre_id TEXT,
    PRIMARY KEY (genre_id, song_key)
) WITHOUT ROWID;

CREATE TABLE artists (
    artist_key TEXT PRIMARY KEY,
    name TEXT,
    mbid TEXT,
    lb_listeners INTEGER,
    popularity INTEGER,
    gender TEXT,
    genres_json TEXT,
    vr_p10 REAL,
    vr_p50 REAL,
    vr_p90 REAL,
    vr_basis TEXT
);

CREATE TABLE artist_similar (
    artist_key TEXT,
    similar_key TEXT,
    score REAL,
    source TEXT,
    PRIMARY KEY (artist_key, similar_key)
) WITHOUT ROWID;

CREATE TABLE genre_map (
    raw_genre TEXT PRIMARY KEY,
    genre_id TEXT
);

CREATE TABLE kn_song_map (
    kn_id INTEGER PRIMARY KEY,
    song_key TEXT,
    artist TEXT,
    title TEXT,
    brands TEXT,
    brand_count INTEGER,
    match_key TEXT
);

CREATE INDEX idx_songs_artist_key ON songs(artist_key);
CREATE INDEX idx_songs_popularity ON songs(popularity DESC);
CREATE INDEX idx_songs_decade ON songs(decade);
CREATE INDEX idx_songs_year ON songs(year);
CREATE INDEX idx_kn_song_map_match_key ON kn_song_map(match_key);
CREATE INDEX idx_kn_song_map_song_key ON kn_song_map(song_key);
"""
