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

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });

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

    getMe: () => api.get<{ id: string; email: string; display_name: string | null }>("/api/auth/me"),

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
  },

  // ============================================================================
  // My Songs / Recommendations endpoints
  // ============================================================================

  my: {
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
        }>;
      }>(`/api/my/recommendations?${params.toString()}`);
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

    submit: (data: {
      known_song_ids: string[];
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
      }>("/api/services/lastfm/connect", { username }),

    disconnect: (serviceType: string) =>
      api.delete<{ message: string }>(`/api/services/${serviceType}`),

    sync: () =>
      api.post<{
        results: Array<{
          service_type: string;
          tracks_fetched: number;
          tracks_matched: number;
          user_songs_created: number;
          user_songs_updated: number;
          error: string | null;
        }>;
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
        }>;
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
};
