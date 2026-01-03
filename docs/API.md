# API Reference

Base URL: `https://api.decide.nomadkaraoke.com` (production)
Local: `http://localhost:8000`

## Authentication

Most endpoints require a Bearer token in the Authorization header:

```
Authorization: Bearer <token>
```

---

## Health

### GET /api/health

Health check endpoint for load balancers and monitoring.

**Response:**
```json
{
  "status": "healthy",
  "service": "karaoke-decide"
}
```

### GET /api/health/deep

Deep health check that validates connectivity to all infrastructure components.
Useful for post-deploy verification and scheduled monitoring.

**Response:**
```json
{
  "status": "healthy",
  "service": "karaoke-decide",
  "timestamp": "2025-12-31T12:00:00Z",
  "checks": {
    "firestore": {
      "status": "healthy",
      "message": "Connected, 42 users in database"
    },
    "bigquery": {
      "status": "healthy",
      "message": "Connected, 275,809 songs in catalog"
    },
    "cloud_tasks": {
      "status": "healthy",
      "message": "Queue 'projects/.../queues/sync-queue' accessible"
    }
  }
}
```

**Status values:**
- `healthy` - All checks passed
- `degraded` - One or more checks failed (see individual check status)

---

## Auth ✅ Implemented

### POST /api/auth/guest

Create a guest/anonymous user session. Allows users to try the app (quiz, recommendations) without email verification.

**No authentication required**

**Response:**
```json
{
  "access_token": "<jwt-token>",
  "token_type": "bearer",
  "expires_in": 2592000
}
```

Guest sessions last 30 days. Guest users cannot connect music services until they verify their email.

### POST /api/auth/magic-link

Request a magic link to be sent via email. In dev mode (no SendGrid configured), the link is logged to console.

**Request:**
```json
{
  "email": "user@example.com"
}
```

**Response:**
```json
{
  "message": "If an account exists for this email, you will receive a magic link shortly."
}
```

### POST /api/auth/verify

Verify a magic link token and get an access token.

**Request:**
```json
{
  "token": "<magic-link-token>"
}
```

**Response:**
```json
{
  "access_token": "<jwt-token>",
  "token_type": "bearer",
  "expires_in": 604800
}
```

### POST /api/auth/upgrade

Request to upgrade a guest account to a verified account. Sends a magic link to the provided email.

**Requires:** Bearer token (guest user)

**Request:**
```json
{
  "email": "user@example.com"
}
```

**Response:**
```json
{
  "message": "Verification email sent. Check your inbox to complete the upgrade."
}
```

When the user clicks the magic link, their guest data (quiz results, etc.) is migrated to the verified account. If the email already has an account, the guest data will be merged.

**Error Responses:**
- `400` - Account is already verified

### GET /api/auth/me

Get the current authenticated user.

**Response:**
```json
{
  "id": "user123",
  "email": "user@example.com",
  "display_name": "John Doe",
  "is_guest": false
}
```

### PUT /api/auth/profile

Update the current user's profile settings.

**Requires:** Bearer token

**Request:**
```json
{
  "display_name": "John Doe"
}
```

Use `null` to clear the display name:
```json
{
  "display_name": null
}
```

**Response:**
```json
{
  "id": "user123",
  "email": "user@example.com",
  "display_name": "John Doe"
}
```

**Error Responses:**
- `404` - User not found

### POST /api/auth/logout

Log out the current user (stateless - client should discard token).

**Requires:** Bearer token

**Response:**
```json
{
  "message": "Successfully logged out"
}
```

---

## Catalog

### GET /api/catalog/songs

Search and browse the karaoke catalog.

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| q | string | Search query (artist or title) |
| artist | string | Filter by artist name |
| page | int | Page number (default: 1) |
| per_page | int | Results per page (default: 50, max: 100) |

**Response:**
```json
{
  "songs": [
    {
      "id": "queen-bohemian-rhapsody",
      "artist": "Queen",
      "title": "Bohemian Rhapsody",
      "sources": ["karaokenerds"],
      "is_popular": true
    }
  ],
  "total": 1,
  "page": 1,
  "per_page": 50,
  "has_more": false
}
```

### GET /api/catalog/songs/{song_id}

Get details for a specific song.

**Response:**
```json
{
  "id": "queen-bohemian-rhapsody",
  "artist": "Queen",
  "title": "Bohemian Rhapsody",
  "sources": ["karaokenerds"],
  "is_popular": true
}
```

### GET /api/catalog/songs/{song_id}/links

Get karaoke links for a specific song. Returns URLs to find or create karaoke videos.

**Response:**
```json
{
  "song_id": 1,
  "artist": "Queen",
  "title": "Bohemian Rhapsody",
  "links": [
    {
      "type": "youtube_search",
      "url": "https://www.youtube.com/results?search_query=Queen+Bohemian+Rhapsody+karaoke",
      "label": "Search YouTube",
      "description": "Find existing karaoke videos on YouTube"
    },
    {
      "type": "karaoke_generator",
      "url": "https://gen.nomadkaraoke.com?artist=Queen&title=Bohemian+Rhapsody",
      "label": "Create with Generator",
      "description": "Generate a custom karaoke video with Nomad Karaoke"
    }
  ]
}
```

**Link Types:**
- `youtube_search` - YouTube search results for karaoke videos
- `karaoke_generator` - Link to Nomad Karaoke Generator to create custom videos

### GET /api/catalog/stats

Get catalog statistics.

**Response:**
```json
{
  "total_songs": 50000,
  "total_artists": 12000,
  "last_updated": "2024-01-15T03:00:00Z"
}
```

---

## Services ✅ Implemented

**Note:** All services endpoints require a verified (non-guest) user. Guest users receive a `403 Forbidden` response with message: "Email verification required. Please verify your email to use this feature."

### GET /api/services

List connected music services.

**Requires:** Bearer token (verified user only)

**Response:**
```json
[
  {
    "service_type": "spotify",
    "service_username": "SpotifyUser123",
    "last_sync_at": "2024-12-30T12:00:00Z",
    "sync_status": "idle",
    "sync_error": null,
    "tracks_synced": 150
  }
]
```

### POST /api/services/spotify/connect

Start Spotify OAuth flow. Returns an authorization URL to redirect the user.

**Requires:** Bearer token

**Response:**
```json
{
  "auth_url": "https://accounts.spotify.com/authorize?..."
}
```

### GET /api/services/spotify/callback

Spotify OAuth callback. Called by Spotify after user authorization.
Redirects to frontend success/error page.

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| code | string | Authorization code from Spotify |
| state | string | CSRF protection state token |
| error | string | Error code if auth failed |

**Response:** Redirect to `{frontend_url}/services/spotify/success` or `{frontend_url}/services/spotify/error?message=...`

### POST /api/services/lastfm/connect

Connect Last.fm account by username.

**Requires:** Bearer token

**Request:**
```json
{
  "username": "lastfmuser"
}
```

**Response:**
```json
{
  "service_type": "lastfm",
  "service_username": "lastfmuser",
  "last_sync_at": null,
  "sync_status": "idle",
  "sync_error": null,
  "tracks_synced": 0
}
```

### DELETE /api/services/{service_type}

Disconnect a music service.

**Requires:** Bearer token

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| service_type | string | "spotify" or "lastfm" |

**Response:**
```json
{
  "message": "Successfully disconnected spotify"
}
```

### POST /api/services/sync

Trigger async listening history sync for all connected services. Sync runs in background via Cloud Tasks.

**Requires:** Bearer token

**Response (202 Accepted):**
```json
{
  "job_id": "sync_user123_1735621234",
  "status": "pending",
  "message": "Sync job created. Poll /api/services/sync/status for progress."
}
```

### GET /api/services/sync/status

Get current sync status for all connected services and any active sync job.

**Requires:** Bearer token

**Response:**
```json
{
  "services": [
    {
      "service_type": "spotify",
      "service_username": "SpotifyUser123",
      "last_sync_at": "2024-12-30T12:00:00Z",
      "sync_status": "idle",
      "sync_error": null,
      "tracks_synced": 150
    }
  ],
  "active_job": {
    "job_id": "sync_user123_1735621234",
    "status": "in_progress",
    "progress": {
      "current_service": "spotify",
      "current_phase": "matching",
      "total_tracks": 500,
      "processed_tracks": 250,
      "matched_tracks": 180,
      "percentage": 50
    },
    "results": null,
    "error": null,
    "created_at": "2024-12-30T12:00:00Z",
    "completed_at": null
  }
}
```

**Job statuses:** `pending`, `in_progress`, `completed`, `failed`

---

## Quiz ✅ Implemented

### GET /api/quiz/artists

Get quiz artists for onboarding (recommended). Returns popular karaoke artists with their top songs for users to identify which they know. Each selected artist adds multiple songs to the user's library.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| count | int | Number of artists (default: 25, min: 10, max: 50) |
| genres | list[str] | Filter by genre IDs (e.g., `genres=rock&genres=pop`) |
| exclude | list[str] | Exclude artist names for pagination (e.g., `exclude=Queen&exclude=ABBA`) |

**Response:**
```json
{
  "artists": [
    {
      "name": "Queen",
      "song_count": 45,
      "top_songs": ["Bohemian Rhapsody", "Don't Stop Me Now", "We Will Rock You"],
      "total_brand_count": 312,
      "primary_decade": "1970s",
      "genres": ["classic rock", "glam rock", "arena rock"],
      "image_url": null
    }
  ]
}
```

**Note:** Genre filtering requires the Spotify artist genres table to be populated via ETL. Falls back to unfiltered results if table doesn't exist.

### GET /api/quiz/decade-artists

Get example artists for each decade. Useful for helping users understand what era each decade represents.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| artists_per_decade | int | Artists per decade (default: 5, min: 3, max: 10) |

**Response:**
```json
{
  "decades": [
    {
      "decade": "1980s",
      "artists": [
        { "name": "Michael Jackson", "top_song": "Billie Jean" },
        { "name": "Prince", "top_song": "Purple Rain" },
        { "name": "Madonna", "top_song": "Like a Prayer" }
      ]
    }
  ]
}
```

### GET /api/quiz/songs

Get quiz songs for onboarding (legacy). Returns popular karaoke songs for users to identify which they know.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| count | int | Number of songs (default: 20, min: 5, max: 30) |

**Response:**
```json
{
  "songs": [
    {
      "id": "1",
      "artist": "Queen",
      "title": "Bohemian Rhapsody",
      "decade": "1970s",
      "popularity": 85,
      "brand_count": 8
    }
  ]
}
```

### POST /api/quiz/submit

Submit quiz responses with known artists or songs and preferences.

**Requires:** Bearer token

**Request (Artist-based - recommended):**
```json
{
  "known_artists": ["Queen", "ABBA", "Elton John"],
  "decade_preference": "1980s",
  "energy_preference": "high"
}
```

**Request (Song-based - legacy):**
```json
{
  "known_song_ids": ["1", "2", "3"],
  "decade_preference": "1980s",
  "energy_preference": "high"
}

**Response:**
```json
{
  "message": "Quiz completed successfully",
  "songs_added": 3,
  "recommendations_ready": true
}
```

### GET /api/quiz/status

Get user's quiz completion status.

**Requires:** Bearer token

**Response:**
```json
{
  "completed": true,
  "completed_at": "2024-01-15T12:00:00Z",
  "songs_known_count": 5
}
```

---

## My Songs ✅ Implemented

### GET /api/my/songs

Get songs from user's listening history matched to karaoke catalog.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| page | int | Page number (default: 1, min: 1) |
| per_page | int | Results per page (default: 20, max: 100) |

**Response:**
```json
{
  "songs": [
    {
      "id": "user123:1",
      "song_id": "1",
      "artist": "Queen",
      "title": "Bohemian Rhapsody",
      "source": "spotify",
      "play_count": 10,
      "playcount": 250,
      "rank": 5,
      "spotify_popularity": 85,
      "is_saved": true,
      "times_sung": 2
    }
  ],
  "total": 150,
  "page": 1,
  "per_page": 20,
  "has_more": true
}
```

### GET /api/my/recommendations

Get personalized karaoke song recommendations.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| limit | int | Number of recommendations (default: 20, min: 1, max: 50) |
| decade | string | Filter by decade (e.g., "1980s") |
| min_popularity | int | Minimum popularity (0-100) |

**Response:**
```json
{
  "recommendations": [
    {
      "song_id": "100",
      "artist": "Queen",
      "title": "We Will Rock You",
      "score": 0.85,
      "reason": "You listen to Queen",
      "reason_type": "known_artist",
      "brand_count": 8,
      "popularity": 80
    }
  ]
}
```

**Reason Types:**
- `known_artist` - Song by an artist you already listen to
- `similar_genre` - Similar genre to your preferences
- `decade_match` - Matches your decade preference from quiz
- `crowd_pleaser` - Popular karaoke song for all audiences
- `popular` - High popularity score on streaming services
- `generate_karaoke` - Song without karaoke version (use Generator)

### GET /api/my/recommendations/categorized

Get recommendations organized into categories with rich filtering options.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| has_karaoke | bool | Filter by karaoke availability (true/false/null for all) |
| min_popularity | int | Minimum Spotify popularity (0-100) |
| max_popularity | int | Maximum Spotify popularity (0-100, for "hidden gems") |
| exclude_explicit | bool | Hide explicit content (default: false) |
| min_duration_ms | int | Minimum song duration in milliseconds |
| max_duration_ms | int | Maximum song duration in milliseconds |
| classics_only | bool | Only show songs with brand_count >= 20 (default: false) |

**Response:**
```json
{
  "from_artists_you_know": [
    {
      "song_id": "40718",
      "artist": "Elton John",
      "title": "I'm Still Standing",
      "score": 0.779,
      "reason": "You listen to Elton John",
      "reason_type": "known_artist",
      "brand_count": 21,
      "popularity": 84,
      "has_karaoke_version": true,
      "is_classic": true,
      "duration_ms": null,
      "explicit": false
    }
  ],
  "create_your_own": [
    {
      "song_id": "spotify:sub-focus:push-the-tempo",
      "artist": "Sub Focus",
      "title": "Push The Tempo",
      "score": 0.35,
      "reason": "Generate karaoke for Sub Focus",
      "reason_type": "generate_karaoke",
      "brand_count": 0,
      "popularity": 50,
      "has_karaoke_version": false,
      "is_classic": false,
      "duration_ms": 180000,
      "explicit": false
    }
  ],
  "crowd_pleasers": [
    {
      "song_id": "100",
      "artist": "Queen",
      "title": "Bohemian Rhapsody",
      "score": 0.65,
      "reason": "Popular karaoke song",
      "reason_type": "crowd_pleaser",
      "brand_count": 50,
      "popularity": 90,
      "has_karaoke_version": true,
      "is_classic": true,
      "duration_ms": null,
      "explicit": false
    }
  ],
  "total_count": 35,
  "filters_applied": {
    "has_karaoke": null,
    "min_popularity": null,
    "max_popularity": null,
    "exclude_explicit": false,
    "min_duration_ms": null,
    "max_duration_ms": null,
    "classics_only": false
  }
}
```

**Categories:**
- `from_artists_you_know` - Karaoke songs by artists in your library (max 15, 3 per artist)
- `create_your_own` - Songs from your library without karaoke versions (max 10)
- `crowd_pleasers` - Popular karaoke songs for discovery (max 10)

---

## My Data ✅ Implemented

User data management endpoints for the unified My Data page.

### GET /api/my/data/summary

Get aggregated summary of user's data.

**Requires:** Bearer token

**Response:**
```json
{
  "services": {
    "spotify": {
      "connected": true,
      "username": "SpotifyUser123",
      "tracks_synced": 150,
      "last_sync_at": "2024-12-30T12:00:00Z"
    },
    "lastfm": {
      "connected": false
    }
  },
  "artists": {
    "total": 25,
    "by_source": {
      "spotify": 20,
      "quiz": 5
    }
  },
  "songs": {
    "total": 150,
    "with_karaoke": 120
  },
  "preferences": {
    "completed": true,
    "decade": "1980s",
    "energy": "high",
    "genres": ["rock", "pop"]
  }
}
```

### GET /api/my/data/artists

Get user's artists from all sources (sync + quiz + manual).

**Requires:** Bearer token

**Response:**
```json
{
  "artists": [
    {
      "artist_name": "Queen",
      "source": "spotify",
      "rank": 1,
      "time_range": "medium_term",
      "popularity": 90,
      "genres": ["rock", "glam rock"],
      "playcount": null
    },
    {
      "artist_name": "ABBA",
      "source": "quiz",
      "rank": 0,
      "time_range": "",
      "popularity": null,
      "genres": [],
      "playcount": null
    }
  ],
  "total": 2
}
```

### POST /api/my/data/artists

Add an artist manually.

**Requires:** Bearer token

**Request:**
```json
{
  "artist_name": "Elton John"
}
```

**Response:**
```json
{
  "message": "Artist added successfully",
  "artist_name": "Elton John"
}
```

**Error Responses:**
- `400` - Artist already in user's data

### DELETE /api/my/data/artists/{artist_name}

Remove an artist from user's data.

**Requires:** Bearer token

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| artist_name | string | Artist name (URL encoded) |

**Response:**
```json
{
  "message": "Artist removed successfully"
}
```

### GET /api/my/data/preferences

Get user's quiz/recommendation preferences.

**Requires:** Bearer token

**Response:**
```json
{
  "decade_preference": "1980s",
  "energy_preference": "high",
  "genres": ["rock", "pop"]
}
```

### PUT /api/my/data/preferences

Update user's preferences.

**Requires:** Bearer token

**Request:**
```json
{
  "decade_preference": "1990s",
  "energy_preference": "medium",
  "genres": ["rock", "pop", "metal"]
}
```

All fields are optional. Use `null` to clear a preference.

**Response:**
```json
{
  "decade_preference": "1990s",
  "energy_preference": "medium",
  "genres": ["rock", "pop", "metal"]
}
```

---

## Known Songs ✅ Implemented

Endpoints for users to manually add songs they already know they like singing, independent of music service sync.

### GET /api/known-songs

Get user's known songs list.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| page | int | Page number (default: 1, min: 1) |
| per_page | int | Results per page (default: 20, max: 100) |

**Response:**
```json
{
  "songs": [
    {
      "id": "user123:1",
      "user_id": "user123",
      "song_id": "1",
      "source": "known_songs",
      "is_saved": true,
      "artist": "Queen",
      "title": "Bohemian Rhapsody",
      "created_at": "2024-01-01T12:00:00Z",
      "updated_at": "2024-01-01T12:00:00Z"
    }
  ],
  "total": 1,
  "page": 1,
  "per_page": 20
}
```

### POST /api/known-songs

Add a song to user's known songs.

**Requires:** Bearer token

**Request:**
```json
{
  "song_id": 1
}
```

**Response (201 Created):**
```json
{
  "added": true,
  "song_id": "1",
  "artist": "Queen",
  "title": "Bohemian Rhapsody",
  "already_existed": false
}
```

**Error Responses:**
- `404` - Song not found in catalog

### POST /api/known-songs/bulk

Bulk add multiple songs to user's known songs.

**Requires:** Bearer token

**Request:**
```json
{
  "song_ids": [1, 2, 3]
}
```

**Response:**
```json
{
  "added": 2,
  "already_existed": 1,
  "not_found": 0,
  "total_requested": 3
}
```

### DELETE /api/known-songs/{song_id}

Remove a song from user's known songs.

**Requires:** Bearer token

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| song_id | int | Song ID to remove |

**Response:** `204 No Content`

**Error Responses:**
- `404` - Song not in user's known songs (or was added from different source)

**Note:** Only songs with source "known_songs" can be removed via this endpoint. Songs synced from Spotify/Last.fm use different endpoints.

---

## My Songs (Not Yet Implemented)

### GET /api/my/history

Get songs the user has sung.

### POST /api/my/songs/{song_id}/sung

Mark a song as sung with optional rating.

---

## Playlists ✅ Implemented

### GET /api/playlists

List user's playlists.

**Requires:** Bearer token

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| limit | int | Max playlists to return (default: 50, max: 100) |
| offset | int | Number of playlists to skip (default: 0) |

**Response:**
```json
{
  "playlists": [
    {
      "id": "playlist-uuid",
      "name": "Friday Night Karaoke",
      "description": "Songs for Friday night sessions",
      "song_ids": ["1", "2", "3"],
      "song_count": 3,
      "created_at": "2024-12-30T12:00:00Z",
      "updated_at": "2024-12-30T12:00:00Z"
    }
  ],
  "total": 1
}
```

### POST /api/playlists

Create a new playlist.

**Requires:** Bearer token

**Request:**
```json
{
  "name": "Friday Night Karaoke",
  "description": "Songs for Friday night sessions"
}
```

**Response:** `201 Created`
```json
{
  "id": "playlist-uuid",
  "name": "Friday Night Karaoke",
  "description": "Songs for Friday night sessions",
  "song_ids": [],
  "song_count": 0,
  "created_at": "2024-12-30T12:00:00Z",
  "updated_at": "2024-12-30T12:00:00Z"
}
```

### GET /api/playlists/{playlist_id}

Get playlist details.

**Requires:** Bearer token

**Response:**
```json
{
  "id": "playlist-uuid",
  "name": "Friday Night Karaoke",
  "description": "Songs for Friday night sessions",
  "song_ids": ["1", "2", "3"],
  "song_count": 3,
  "created_at": "2024-12-30T12:00:00Z",
  "updated_at": "2024-12-30T12:00:00Z"
}
```

**Error Responses:**
- `404` - Playlist not found
- `403` - Access denied (not your playlist)

### PUT /api/playlists/{playlist_id}

Update a playlist's name or description.

**Requires:** Bearer token

**Request:**
```json
{
  "name": "Updated Name",
  "description": "Updated description"
}
```

**Response:**
```json
{
  "id": "playlist-uuid",
  "name": "Updated Name",
  "description": "Updated description",
  "song_ids": ["1", "2", "3"],
  "song_count": 3,
  "created_at": "2024-12-30T12:00:00Z",
  "updated_at": "2024-12-30T14:00:00Z"
}
```

### DELETE /api/playlists/{playlist_id}

Delete a playlist.

**Requires:** Bearer token

**Response:** `204 No Content`

### POST /api/playlists/{playlist_id}/songs

Add a song to a playlist.

**Requires:** Bearer token

**Request:**
```json
{
  "song_id": "queen-bohemian-rhapsody"
}
```

**Response:**
```json
{
  "id": "playlist-uuid",
  "name": "Friday Night Karaoke",
  "description": "Songs for Friday night sessions",
  "song_ids": ["1", "2", "3", "queen-bohemian-rhapsody"],
  "song_count": 4,
  "created_at": "2024-12-30T12:00:00Z",
  "updated_at": "2024-12-30T14:00:00Z"
}
```

Note: Duplicate songs are silently ignored (no error, but not added again).

### DELETE /api/playlists/{playlist_id}/songs/{song_id}

Remove a song from a playlist.

**Requires:** Bearer token

**Response:** `204 No Content`

Note: If the song is not in the playlist, this is a no-op (no error).

---

## Admin ✅ Implemented

Admin endpoints for system management. All endpoints require admin privileges (`is_admin: true` on user).

### GET /api/admin/stats

Get dashboard statistics.

**Requires:** Bearer token (admin)

**Response:**
```json
{
  "users": {
    "total": 150,
    "verified": 100,
    "guests": 50,
    "active_7d": 45
  },
  "sync_jobs_24h": {
    "pending": 0,
    "in_progress": 2,
    "completed": 25,
    "failed": 3
  },
  "services": {
    "spotify_connected": 85,
    "lastfm_connected": 30
  }
}
```

### GET /api/admin/users

List all users with pagination and filtering.

**Requires:** Bearer token (admin)

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| limit | int | Results per page (default: 20, max: 100) |
| offset | int | Number to skip (default: 0) |
| filter | string | "all", "verified", or "guests" |
| search | string | Search by email |

**Response:**
```json
{
  "users": [
    {
      "id": "user-uuid",
      "email": "user@example.com",
      "display_name": "John Doe",
      "is_guest": false,
      "is_admin": false,
      "created_at": "2024-12-30T12:00:00Z",
      "last_sync_at": "2024-12-30T14:00:00Z",
      "quiz_completed_at": "2024-12-30T12:30:00Z",
      "total_songs_known": 150
    }
  ],
  "total": 150
}
```

### GET /api/admin/users/{user_id}

Get detailed user information.

**Requires:** Bearer token (admin)

**Response:**
```json
{
  "id": "user-uuid",
  "email": "user@example.com",
  "display_name": "John Doe",
  "is_guest": false,
  "is_admin": false,
  "created_at": "2024-12-30T12:00:00Z",
  "last_sync_at": "2024-12-30T14:00:00Z",
  "quiz_completed_at": "2024-12-30T12:30:00Z",
  "total_songs_known": 150,
  "services": [
    {
      "service_type": "spotify",
      "service_username": "SpotifyUser123",
      "sync_status": "idle",
      "last_sync_at": "2024-12-30T14:00:00Z",
      "tracks_synced": 150,
      "sync_error": null
    }
  ],
  "sync_jobs": [
    {
      "id": "job-uuid",
      "status": "completed",
      "created_at": "2024-12-30T14:00:00Z",
      "completed_at": "2024-12-30T14:05:00Z",
      "error": null
    }
  ],
  "data_summary": {
    "artists_count": 50,
    "songs_count": 150,
    "playlists_count": 3
  }
}
```

### GET /api/admin/sync-jobs

List sync jobs with pagination and filtering.

**Requires:** Bearer token (admin)

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| limit | int | Results per page (default: 20, max: 100) |
| offset | int | Number to skip (default: 0) |
| status | string | "all", "pending", "in_progress", "completed", or "failed" |

**Response:**
```json
{
  "jobs": [
    {
      "id": "job-uuid",
      "user_id": "user-uuid",
      "user_email": "user@example.com",
      "status": "completed",
      "created_at": "2024-12-30T14:00:00Z",
      "completed_at": "2024-12-30T14:05:00Z",
      "error": null
    }
  ],
  "total": 100
}
```

### GET /api/admin/sync-jobs/{job_id}

Get detailed sync job information.

**Requires:** Bearer token (admin)

**Response:**
```json
{
  "id": "job-uuid",
  "user_id": "user-uuid",
  "user_email": "user@example.com",
  "status": "completed",
  "created_at": "2024-12-30T14:00:00Z",
  "completed_at": "2024-12-30T14:05:00Z",
  "error": null,
  "progress": {
    "current_service": null,
    "current_phase": null,
    "total_tracks": 500,
    "processed_tracks": 500,
    "matched_tracks": 350,
    "percentage": 100
  },
  "results": [
    {
      "service_type": "spotify",
      "tracks_fetched": 500,
      "tracks_matched": 350,
      "user_songs_created": 200,
      "user_songs_updated": 150,
      "artists_stored": 75,
      "error": null
    }
  ]
}
```

**Error Responses:**
- `403` - Admin access required
- `404` - Job not found

---

## Error Responses

All errors follow this format:

```json
{
  "detail": "Error message here"
}
```

Common status codes:
- `400` - Bad request (invalid input)
- `401` - Not authenticated
- `403` - Not authorized
- `404` - Resource not found
- `429` - Rate limited
- `500` - Internal server error
- `501` - Not implemented
