/**
 * API client with authentication support
 */

import { API_BASE_URL, AUTH_TOKEN_KEY } from "./constants";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class NetworkError extends Error {
  constructor(
    message: string,
    public originalError?: Error
  ) {
    super(message);
    this.name = "NetworkError";
  }

  static isNetworkError(error: unknown): error is NetworkError {
    return error instanceof NetworkError;
  }

  static getHelpfulMessage(error: Error): string {
    const msg = error.message.toLowerCase();

    if (msg.includes("failed to fetch") || msg.includes("networkerror") || msg.includes("network request failed")) {
      return "Unable to connect to server. Please check your internet connection, try disabling VPN or ad-blockers, or try using mobile data instead of WiFi.";
    }

    if (msg.includes("timeout") || msg.includes("timed out")) {
      return "The request timed out. Please check your internet connection and try again.";
    }

    if (msg.includes("cors") || msg.includes("cross-origin")) {
      return "Connection blocked by browser security. Try disabling browser extensions or using incognito mode.";
    }

    if (msg.includes("ssl") || msg.includes("certificate")) {
      return "Secure connection failed. Please check your network settings or try a different network.";
    }

    return "A network error occurred. Please check your internet connection and try again.";
  }
}

/**
 * Get the stored auth token
 */
export function getAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(AUTH_TOKEN_KEY);
}

/**
 * Store the auth token
 */
export function setAuthToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(AUTH_TOKEN_KEY, token);
}

/**
 * Clear the auth token
 */
export function clearAuthToken(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(AUTH_TOKEN_KEY);
}

/**
 * Make an authenticated API request
 */
async function apiRequest<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const token = getAuthToken();

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${endpoint}`, {
      ...options,
      headers,
    });
  } catch (error) {
    // Network-level errors (no response received)
    const originalError = error instanceof Error ? error : new Error(String(error));
    const helpfulMessage = NetworkError.getHelpfulMessage(originalError);
    throw new NetworkError(helpfulMessage, originalError);
  }

  // Handle 401 - clear token and redirect
  if (response.status === 401) {
    clearAuthToken();
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    throw new ApiError(401, "Unauthorized");
  }

  // Handle other errors
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const message = errorData.detail || `API error: ${response.status}`;
    throw new ApiError(response.status, message);
  }

  // Handle empty responses
  const text = await response.text();
  if (!text) {
    return {} as T;
  }

  return JSON.parse(text) as T;
}

/**
 * API client with typed methods
 */
export const api = {
  // Generic methods
  get: <T>(endpoint: string) => apiRequest<T>(endpoint, { method: "GET" }),

  post: <T>(endpoint: string, body?: unknown) =>
    apiRequest<T>(endpoint, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),

  delete: <T>(endpoint: string) => apiRequest<T>(endpoint, { method: "DELETE" }),

  // ============================================================================
  // Auth endpoints
  // ============================================================================

  auth: {
    requestMagicLink: (email: string) =>
      api.post<{ message: string }>("/api/auth/magic-link", { email }),

    verifyToken: (token: string) =>
      api.post<{ access_token: string; token_type: string; expires_in: number }>(
        "/api/auth/verify",
        { token }
      ),

    createGuestSession: () =>
      api.post<{ access_token: string; token_type: string; expires_in: number }>(
        "/api/auth/guest"
      ),

    upgradeGuest: (email: string) =>
      api.post<{ message: string }>("/api/auth/upgrade", { email }),

    getMe: () =>
      api.get<{
        id: string;
        email: string | null;
        display_name: string | null;
        is_guest: boolean;
        is_admin: boolean;
      }>("/api/auth/me"),

    updateProfile: (data: { display_name?: string | null }) =>
      apiRequest<{
        id: string;
        email: string | null;
        display_name: string | null;
        is_guest: boolean;
      }>("/api/auth/profile", {
        method: "PUT",
        body: JSON.stringify(data),
      }),

    logout: () => api.post<{ message: string }>("/api/auth/logout"),
  },

  // ============================================================================
  // Catalog endpoints (public)
  // ============================================================================

  catalog: {
    searchSongs: (query: string, perPage: number = 20) =>
      api.get<{
        songs: Array<{
          id: number;
          artist: string;
          title: string;
          brands: string[];
          brand_count: number;
          is_popular: boolean;
        }>;
        total: number;
        page: number;
        per_page: number;
        has_more: boolean;
      }>(`/api/catalog/songs?q=${encodeURIComponent(query)}&per_page=${perPage}`),

    getPopularSongs: (limit: number = 20) =>
      api.get<
        Array<{
          id: number;
          artist: string;
          title: string;
          brands: string[];
          brand_count: number;
          is_popular: boolean;
        }>
      >(`/api/catalog/songs/popular?limit=${limit}`),

    getSongLinks: (songId: number) =>
      api.get<{
        song_id: number;
        artist: string;
        title: string;
        links: Array<{
          type: "youtube_search" | "karaoke_generator";
          url: string;
          label: string;
          description: string;
        }>;
      }>(`/api/catalog/songs/${songId}/links`),

    searchArtists: (query: string, limit: number = 10) =>
      api.get<{
        artists: Array<{
          artist_id: string;
          artist_name: string;
          popularity: number;
          genres: string[];
        }>;
        total: number;
      }>(`/api/catalog/artists?q=${encodeURIComponent(query)}&limit=${limit}`),

    searchTracks: (query: string, limit: number = 10) =>
      api.get<{
        tracks: Array<{
          track_id: string;
          track_name: string;
          artist_name: string;
          artist_id: string;
          popularity: number;
          duration_ms: number;
          explicit: boolean;
        }>;
        total: number;
      }>(`/api/catalog/tracks?q=${encodeURIComponent(query)}&limit=${limit}`),
  },

  // ============================================================================
  // My Data / Songs / Recommendations endpoints
  // ============================================================================

  my: {
    // My Data summary
    getDataSummary: () =>
      api.get<{
        services: Record<
          string,
          {
            connected: boolean;
            username?: string;
            tracks_synced?: number;
            songs_synced?: number;
            artists_synced?: number;
            last_sync_at?: string;
          }
        >;
        artists: {
          total: number;
          by_source: Record<string, number>;
        };
        songs: {
          total: number;
          with_karaoke: number;
          known_songs: number;
        };
        preferences: {
          completed: boolean;
          decade?: string;
          energy?: string;
          genres?: string[];
        };
      }>("/api/my/data/summary"),

    // My Data artists
    getDataArtists: (page: number = 1, perPage: number = 100) =>
      api.get<{
        artists: Array<{
          artist_name: string;
          sources: string[];
          spotify_rank: number | null;
          spotify_time_range: string | null;
          lastfm_rank: number | null;
          lastfm_playcount: number | null;
          popularity: number | null;
          genres: string[];
          is_excluded: boolean;
          is_manual: boolean;
        }>;
        total: number;
        page: number;
        per_page: number;
        has_more: boolean;
      }>(`/api/my/data/artists?page=${page}&per_page=${perPage}`),

    addDataArtist: (artistName: string, spotifyArtistId?: string) =>
      api.post<{ artists: string[]; added: string }>("/api/my/data/artists", {
        artist_name: artistName,
        spotify_artist_id: spotifyArtistId,
      }),

    removeDataArtist: (artistName: string) =>
      api.delete<{ removed: string; removed_from: string[]; success: boolean }>(
        `/api/my/data/artists/${encodeURIComponent(artistName)}`
      ),

    excludeArtist: (artistName: string) =>
      api.post<{ artist_name: string; excluded: boolean; success: boolean }>(
        `/api/my/data/artists/exclude?artist_name=${encodeURIComponent(artistName)}`
      ),

    includeArtist: (artistName: string) =>
      api.delete<{ artist_name: string; excluded: boolean; success: boolean }>(
        `/api/my/data/artists/exclude?artist_name=${encodeURIComponent(artistName)}`
      ),

    // My Data preferences
    getPreferences: () =>
      api.get<{
        decade_preference: string | null;
        energy_preference: "chill" | "medium" | "high" | null;
        genres: string[];
      }>("/api/my/data/preferences"),

    updatePreferences: (data: {
      decade_preference?: string | null;
      energy_preference?: "chill" | "medium" | "high" | null;
      genres?: string[];
    }) =>
      apiRequest<{
        decade_preference: string | null;
        energy_preference: "chill" | "medium" | "high" | null;
        genres: string[];
      }>("/api/my/data/preferences", {
        method: "PUT",
        body: JSON.stringify(data),
      }),

    // Songs
    getSongs: (page: number = 1, perPage: number = 20) =>
      api.get<{
        songs: Array<{
          id: string;
          song_id: string;
          artist: string;
          title: string;
          source: string;
          play_count: number;
          is_saved: boolean;
          times_sung: number;
        }>;
        total: number;
        page: number;
        per_page: number;
        has_more: boolean;
      }>(`/api/my/songs?page=${page}&per_page=${perPage}`),

    getRecommendations: (
      limit: number = 20,
      decade?: string,
      minPopularity?: number
    ) => {
      const params = new URLSearchParams({ limit: limit.toString() });
      if (decade) params.append("decade", decade);
      if (minPopularity !== undefined)
        params.append("min_popularity", minPopularity.toString());
      return api.get<{
        recommendations: Array<{
          song_id: string;
          artist: string;
          title: string;
          score: number;
          reason: string;
          reason_type: string;
          brand_count: number;
          popularity: number;
          has_karaoke_version: boolean;
          is_classic: boolean;
          duration_ms: number | null;
          explicit: boolean;
        }>;
      }>(`/api/my/recommendations?${params.toString()}`);
    },

    getCategorizedRecommendations: (filters?: {
      has_karaoke?: boolean | null;
      min_popularity?: number;
      max_popularity?: number;
      exclude_explicit?: boolean;
      min_duration_ms?: number;
      max_duration_ms?: number;
      classics_only?: boolean;
    }) => {
      const params = new URLSearchParams();
      if (filters) {
        if (filters.has_karaoke !== undefined && filters.has_karaoke !== null) {
          params.append("has_karaoke", filters.has_karaoke.toString());
        }
        if (filters.min_popularity !== undefined) {
          params.append("min_popularity", filters.min_popularity.toString());
        }
        if (filters.max_popularity !== undefined) {
          params.append("max_popularity", filters.max_popularity.toString());
        }
        if (filters.exclude_explicit !== undefined) {
          params.append("exclude_explicit", filters.exclude_explicit.toString());
        }
        if (filters.min_duration_ms !== undefined) {
          params.append("min_duration_ms", filters.min_duration_ms.toString());
        }
        if (filters.max_duration_ms !== undefined) {
          params.append("max_duration_ms", filters.max_duration_ms.toString());
        }
        if (filters.classics_only !== undefined) {
          params.append("classics_only", filters.classics_only.toString());
        }
      }
      const queryString = params.toString();
      const url = queryString
        ? `/api/my/recommendations/categorized?${queryString}`
        : "/api/my/recommendations/categorized";
      return api.get<{
        from_artists_you_know: Array<{
          song_id: string;
          artist: string;
          title: string;
          score: number;
          reason: string;
          reason_type: string;
          brand_count: number;
          popularity: number;
          has_karaoke_version: boolean;
          is_classic: boolean;
          duration_ms: number | null;
          explicit: boolean;
        }>;
        create_your_own: Array<{
          song_id: string;
          artist: string;
          title: string;
          score: number;
          reason: string;
          reason_type: string;
          brand_count: number;
          popularity: number;
          has_karaoke_version: boolean;
          is_classic: boolean;
          duration_ms: number | null;
          explicit: boolean;
        }>;
        crowd_pleasers: Array<{
          song_id: string;
          artist: string;
          title: string;
          score: number;
          reason: string;
          reason_type: string;
          brand_count: number;
          popularity: number;
          has_karaoke_version: boolean;
          is_classic: boolean;
          duration_ms: number | null;
          explicit: boolean;
        }>;
        total_count: number;
        filters_applied: Record<string, string | number | boolean | null>;
      }>(url);
    },

    getArtists: (
      source?: string,
      timeRange?: string,
      limit: number = 100
    ) => {
      const params = new URLSearchParams({ limit: limit.toString() });
      if (source) params.append("source", source);
      if (timeRange) params.append("time_range", timeRange);
      return api.get<{
        artists: Array<{
          id: string;
          artist_name: string;
          source: string;
          rank: number;
          time_range: string;
          popularity: number | null;
          genres: string[];
        }>;
        total: number;
        sources: Record<string, number>;
      }>(`/api/my/artists?${params.toString()}`);
    },
  },

  // ============================================================================
  // Quiz endpoints
  // ============================================================================

  quiz: {
    getSongs: (count: number = 15) =>
      api.get<{
        songs: Array<{
          id: string;
          artist: string;
          title: string;
          decade: string;
          popularity: number;
          brand_count: number;
        }>;
      }>(`/api/quiz/songs?count=${count}`),

    getArtists: (count: number = 25, genres?: string[], exclude?: string[]) => {
      const params = new URLSearchParams({ count: count.toString() });
      if (genres && genres.length > 0) {
        genres.forEach((g) => params.append("genres", g));
      }
      if (exclude && exclude.length > 0) {
        exclude.forEach((e) => params.append("exclude", e));
      }
      return api.get<{
        artists: Array<{
          name: string;
          song_count: number;
          top_songs: string[];
          total_brand_count: number;
          primary_decade: string;
          genres: string[];
          image_url: string | null;
        }>;
      }>(`/api/quiz/artists?${params.toString()}`);
    },

    getDecadeArtists: (artistsPerDecade: number = 5) =>
      api.get<{
        decades: Array<{
          decade: string;
          artists: Array<{
            name: string;
            top_song: string;
          }>;
        }>;
      }>(`/api/quiz/decade-artists?artists_per_decade=${artistsPerDecade}`),

    submit: (data: {
      known_song_ids?: string[];
      known_artists?: string[];
      decade_preference?: string | null;
      energy_preference?: "chill" | "medium" | "high" | null;
    }) =>
      api.post<{
        message: string;
        songs_added: number;
        recommendations_ready: boolean;
      }>("/api/quiz/submit", data),

    getStatus: () =>
      api.get<{
        completed: boolean;
        completed_at: string | null;
        songs_known_count: number;
      }>("/api/quiz/status"),

    /**
     * Submit songs the user enjoys singing (quiz step 4)
     * @param songs - Array of songs with optional metadata
     */
    submitEnjoySinging: (
      songs: Array<{
        song_id: string;
        singing_tags?: string[];
        singing_energy?: string | null;
        vocal_comfort?: string | null;
        notes?: string | null;
      }>
    ) =>
      api.post<{
        songs_added: number;
        songs_updated: number;
        songs_failed: number;
      }>("/api/quiz/enjoy-singing", { songs }),
  },

  // ============================================================================
  // Services endpoints
  // ============================================================================

  services: {
    list: () =>
      api.get<
        Array<{
          service_type: string;
          service_username: string;
          last_sync_at: string | null;
          sync_status: string;
          sync_error: string | null;
          tracks_synced: number;
          songs_synced: number;
        }>
      >("/api/services"),

    connectSpotify: () =>
      api.post<{ auth_url: string }>("/api/services/spotify/connect"),

    connectLastfm: (username: string) =>
      api.post<{
        service_type: string;
        service_username: string;
        last_sync_at: string | null;
        sync_status: string;
        sync_error: string | null;
        tracks_synced: number;
        songs_synced: number;
      }>("/api/services/lastfm/connect", { username }),

    disconnect: (serviceType: string) =>
      api.delete<{ message: string }>(`/api/services/${serviceType}`),

    sync: () =>
      api.post<{
        job_id: string;
        status: string;
        message: string;
      }>("/api/services/sync"),

    getSyncStatus: () =>
      api.get<{
        services: Array<{
          service_type: string;
          service_username: string;
          last_sync_at: string | null;
          sync_status: string;
          sync_error: string | null;
          tracks_synced: number;
          songs_synced: number;
        }>;
        active_job: {
          job_id: string;
          status: string;
          progress: {
            current_service: string | null;
            current_phase: string | null;
            total_tracks: number;
            processed_tracks: number;
            matched_tracks: number;
            percentage: number;
          } | null;
          results: Array<{
            service_type: string;
            tracks_fetched: number;
            tracks_matched: number;
            user_songs_created: number;
            user_songs_updated: number;
            artists_stored: number;
            error: string | null;
          }> | null;
          error: string | null;
          created_at: string;
          completed_at: string | null;
        } | null;
      }>("/api/services/sync/status"),
  },

  // ============================================================================
  // Playlist endpoints
  // ============================================================================

  playlists: {
    list: (limit: number = 50, offset: number = 0) =>
      api.get<{
        playlists: Array<{
          id: string;
          name: string;
          description: string | null;
          song_ids: string[];
          song_count: number;
          created_at: string;
          updated_at: string;
        }>;
        total: number;
      }>(`/api/playlists?limit=${limit}&offset=${offset}`),

    create: (name: string, description?: string | null) =>
      api.post<{
        id: string;
        name: string;
        description: string | null;
        song_ids: string[];
        song_count: number;
        created_at: string;
        updated_at: string;
      }>("/api/playlists", { name, description }),

    get: (playlistId: string) =>
      api.get<{
        id: string;
        name: string;
        description: string | null;
        song_ids: string[];
        song_count: number;
        created_at: string;
        updated_at: string;
      }>(`/api/playlists/${playlistId}`),

    update: (playlistId: string, data: { name?: string; description?: string | null }) =>
      apiRequest<{
        id: string;
        name: string;
        description: string | null;
        song_ids: string[];
        song_count: number;
        created_at: string;
        updated_at: string;
      }>(`/api/playlists/${playlistId}`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),

    delete: (playlistId: string) =>
      api.delete<Record<string, never>>(`/api/playlists/${playlistId}`),

    addSong: (playlistId: string, songId: string) =>
      api.post<{
        id: string;
        name: string;
        description: string | null;
        song_ids: string[];
        song_count: number;
        created_at: string;
        updated_at: string;
      }>(`/api/playlists/${playlistId}/songs`, { song_id: songId }),

    removeSong: (playlistId: string, songId: string) =>
      api.delete<Record<string, never>>(`/api/playlists/${playlistId}/songs/${songId}`),
  },

  // ============================================================================
  // Known Songs endpoints
  // ============================================================================

  knownSongs: {
    list: (page: number = 1, perPage: number = 20) =>
      api.get<{
        songs: Array<{
          id: string;
          song_id: string;
          artist: string;
          title: string;
          source: string;
          is_saved: boolean;
          created_at: string;
          updated_at: string;
        }>;
        total: number;
        page: number;
        per_page: number;
      }>(`/api/known-songs?page=${page}&per_page=${perPage}`),

    add: (songId: number) =>
      api.post<{
        added: boolean;
        song_id: string;
        artist: string;
        title: string;
        already_existed: boolean;
      }>("/api/known-songs", { song_id: songId }),

    bulkAdd: (songIds: number[]) =>
      api.post<{
        added: number;
        already_existed: number;
        not_found: number;
        total_requested: number;
      }>("/api/known-songs/bulk", { song_ids: songIds }),

    remove: (songId: number) =>
      api.delete<Record<string, never>>(`/api/known-songs/${songId}`),

    addSpotifyTrack: (trackId: string) =>
      api.post<{
        added: boolean;
        track_id: string;
        track_name: string;
        artist_name: string;
        artist_id: string;
        popularity: number;
        duration_ms: number;
        explicit: boolean;
        already_existed: boolean;
      }>("/api/known-songs/spotify", { track_id: trackId }),

    removeSpotifyTrack: (trackId: string) =>
      api.delete<Record<string, never>>(`/api/known-songs/spotify/${trackId}`),

    /**
     * Mark a song as one the user enjoys singing at karaoke
     * @param songId - Karaoke catalog ID or "spotify:{track_id}"
     * @param metadata - Optional metadata about why user enjoys singing
     */
    setEnjoySinging: (
      songId: string,
      metadata?: {
        singing_tags?: string[];
        singing_energy?: string | null;
        vocal_comfort?: string | null;
        notes?: string | null;
      }
    ) =>
      api.post<{
        success: boolean;
        song_id: string;
        artist: string;
        title: string;
        enjoy_singing: boolean;
        singing_tags: string[];
        singing_energy: string | null;
        vocal_comfort: string | null;
        notes: string | null;
        created_new: boolean;
      }>(`/api/known-songs/enjoy-singing?song_id=${encodeURIComponent(songId)}`, metadata || {}),

    /**
     * Remove enjoy singing flag from a song
     * @param songId - Karaoke catalog ID or "spotify:{track_id}"
     */
    removeEnjoySinging: (songId: string) =>
      api.delete<Record<string, never>>(`/api/known-songs/enjoy-singing?song_id=${encodeURIComponent(songId)}`),
  },

  // ============================================================================
  // Admin endpoints
  // ============================================================================

  admin: {
    getStats: () =>
      api.get<{
        users: {
          total: number;
          verified: number;
          guests: number;
          active_7d: number;
        };
        sync_jobs: {
          total: number;
          pending: number;
          in_progress: number;
          completed: number;
          failed: number;
        };
        services: {
          spotify_connected: number;
          lastfm_connected: number;
        };
      }>("/api/admin/stats"),

    listUsers: (params?: {
      limit?: number;
      offset?: number;
      filter?: "all" | "verified" | "guests";
      search?: string;
    }) => {
      const searchParams = new URLSearchParams();
      if (params?.limit) searchParams.append("limit", params.limit.toString());
      if (params?.offset) searchParams.append("offset", params.offset.toString());
      if (params?.filter) searchParams.append("filter", params.filter);
      if (params?.search) searchParams.append("search", params.search);
      const queryString = searchParams.toString();
      return api.get<{
        users: Array<{
          id: string;
          email: string | null;
          display_name: string | null;
          is_guest: boolean;
          is_admin: boolean;
          created_at: string;
          last_sync_at: string | null;
          quiz_completed_at: string | null;
          total_songs_known: number;
        }>;
        total: number;
        limit: number;
        offset: number;
      }>(queryString ? `/api/admin/users?${queryString}` : "/api/admin/users");
    },

    getUser: (userId: string) =>
      api.get<{
        id: string;
        email: string | null;
        display_name: string | null;
        is_guest: boolean;
        is_admin: boolean;
        created_at: string;
        last_sync_at: string | null;
        quiz_completed_at: string | null;
        total_songs_known: number;
        services: Array<{
          service_type: string;
          service_username: string;
          sync_status: string;
          last_sync_at: string | null;
          tracks_synced: number;
          songs_synced: number;
          sync_error: string | null;
        }>;
        sync_jobs: Array<{
          id: string;
          status: string;
          created_at: string;
          completed_at: string | null;
          error: string | null;
        }>;
        data_summary: {
          artists_count: number;
          songs_count: number;
          playlists_count: number;
        };
      }>(`/api/admin/users/${userId}`),

    listSyncJobs: (params?: {
      limit?: number;
      offset?: number;
      status?: "all" | "pending" | "in_progress" | "completed" | "failed";
    }) => {
      const searchParams = new URLSearchParams();
      if (params?.limit) searchParams.append("limit", params.limit.toString());
      if (params?.offset) searchParams.append("offset", params.offset.toString());
      if (params?.status) searchParams.append("status", params.status);
      const queryString = searchParams.toString();
      return api.get<{
        jobs: Array<{
          id: string;
          user_id: string;
          user_email: string | null;
          status: string;
          created_at: string;
          completed_at: string | null;
          error: string | null;
        }>;
        total: number;
        limit: number;
        offset: number;
      }>(queryString ? `/api/admin/sync-jobs?${queryString}` : "/api/admin/sync-jobs");
    },

    getSyncJob: (jobId: string) =>
      api.get<{
        id: string;
        user_id: string;
        user_email: string | null;
        status: string;
        created_at: string;
        completed_at: string | null;
        error: string | null;
        progress: {
          current_service: string | null;
          current_phase: string | null;
          total_tracks: number;
          processed_tracks: number;
          matched_tracks: number;
          percentage: number;
        } | null;
        results: Array<{
          service_type: string;
          tracks_fetched: number;
          tracks_matched: number;
          user_songs_created: number;
          user_songs_updated: number;
          artists_stored: number;
          error: string | null;
        }>;
      }>(`/api/admin/sync-jobs/${jobId}`),
  },
};
