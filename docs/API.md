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

Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "service": "karaoke-decide"
}
```

---

## Auth ✅ Implemented

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

### GET /api/auth/me

Get the current authenticated user.

**Response:**
```json
{
  "id": "user123",
  "email": "user@example.com",
  "display_name": "John Doe"
}
```

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

### GET /api/services

List connected music services.

**Requires:** Bearer token

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

Trigger listening history sync for all connected services.

**Requires:** Bearer token

**Response:**
```json
{
  "results": [
    {
      "service_type": "spotify",
      "tracks_fetched": 100,
      "tracks_matched": 75,
      "user_songs_created": 50,
      "user_songs_updated": 25,
      "error": null
    }
  ]
}
```

### GET /api/services/sync/status

Get current sync status for all connected services.

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
  ]
}
```

---

## Quiz ✅ Implemented

### GET /api/quiz/songs

Get quiz songs for onboarding. Returns popular karaoke songs for users to identify which they know.

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

Submit quiz responses with known songs and preferences.

**Requires:** Bearer token

**Request:**
```json
{
  "known_song_ids": ["1", "2", "3"],
  "decade_preference": "1980s",
  "energy_preference": "high"
}
```

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
      "is_saved": true,
      "times_sung": 2,
      "last_played_at": "2024-01-15T12:00:00Z",
      "last_sung_at": "2024-01-10T20:00:00Z",
      "average_rating": 4.5,
      "notes": "Great for warming up"
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

---

## My Songs (Not Yet Implemented)

### GET /api/my/history

Get songs the user has sung.

### POST /api/my/songs/{song_id}/sung

Mark a song as sung with optional rating.

---

## Playlists (Not Yet Implemented)

### GET /api/playlists

List user's playlists.

### POST /api/playlists

Create a new playlist.

### GET /api/playlists/{playlist_id}

Get playlist details.

### PUT /api/playlists/{playlist_id}

Update a playlist.

### DELETE /api/playlists/{playlist_id}

Delete a playlist.

### POST /api/playlists/{playlist_id}/songs

Add a song to a playlist.

### DELETE /api/playlists/{playlist_id}/songs/{song_id}

Remove a song from a playlist.

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
