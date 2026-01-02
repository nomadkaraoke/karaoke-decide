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
      "image_url": null
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
