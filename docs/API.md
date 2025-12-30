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

## Auth

### POST /api/auth/magic-link

Request a magic link to be sent via email.

**Request:**
```json
{
  "email": "user@example.com"
}
```

**Response:**
```json
{
  "message": "Magic link sent to user@example.com"
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

Invalidate the current session.

**Response:**
```json
{
  "message": "Logged out"
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

## Services (Not Yet Implemented)

### GET /api/services

List connected music services.

### POST /api/services/spotify/connect

Start Spotify OAuth flow.

### GET /api/services/spotify/callback

Spotify OAuth callback.

### POST /api/services/lastfm/connect

Connect Last.fm account.

### POST /api/services/sync

Trigger listening history sync.

---

## My Songs (Not Yet Implemented)

### GET /api/my/songs

Get songs from user's listening history matched to karaoke catalog.

### GET /api/my/songs/top

Get top karaoke recommendations for user.

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
