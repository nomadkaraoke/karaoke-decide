/**
 * TypeScript types for Karaoke Decide frontend
 */

// ============================================================================
// Auth Types
// ============================================================================

export interface User {
  id: string;
  email: string;
  display_name: string | null;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

// ============================================================================
// Song Types
// ============================================================================

export interface CatalogSong {
  id: number;
  artist: string;
  title: string;
  brands: string[];
  brand_count: number;
  is_popular: boolean;
}

export interface UserSong {
  id: string;
  song_id: string;
  artist: string;
  title: string;
  source: "spotify" | "lastfm" | "quiz";
  play_count: number;
  is_saved: boolean;
  times_sung: number;
}

export interface Recommendation {
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
}

export interface QuizSong {
  id: string;
  artist: string;
  title: string;
  decade: string;
  popularity: number;
  brand_count: number;
}

// ============================================================================
// Music Service Types
// ============================================================================

export interface ConnectedService {
  service_type: "spotify" | "lastfm";
  service_username: string;
  last_sync_at: string | null;
  sync_status: "idle" | "syncing" | "error";
  sync_error: string | null;
  tracks_synced: number;
}

export interface SyncResult {
  service_type: string;
  tracks_fetched: number;
  tracks_matched: number;
  user_songs_created: number;
  user_songs_updated: number;
  error: string | null;
}

// ============================================================================
// API Response Types
// ============================================================================

export interface PaginatedResponse<T> {
  songs: T[];
  total: number;
  page: number;
  per_page: number;
  has_more: boolean;
}

export type CatalogSearchResponse = PaginatedResponse<CatalogSong>;

export type UserSongsResponse = PaginatedResponse<UserSong>;

export interface RecommendationsResponse {
  recommendations: Recommendation[];
}

export interface CategorizedRecommendationsResponse {
  from_artists_you_know: Recommendation[];
  create_your_own: Recommendation[];
  crowd_pleasers: Recommendation[];
  total_count: number;
  filters_applied: Record<string, string | number | boolean | null>;
}

export interface RecommendationFilters {
  has_karaoke?: boolean | null;
  min_popularity?: number;
  max_popularity?: number;
  exclude_explicit?: boolean;
  min_duration_ms?: number;
  max_duration_ms?: number;
  classics_only?: boolean;
}

export interface QuizSongsResponse {
  songs: QuizSong[];
}

export interface QuizSubmitResponse {
  message: string;
  songs_added: number;
  recommendations_ready: boolean;
}

export interface QuizStatusResponse {
  completed: boolean;
  completed_at: string | null;
  songs_known_count: number;
}

export interface SyncResultResponse {
  results: SyncResult[];
}

// ============================================================================
// Request Types
// ============================================================================

export interface QuizSubmitRequest {
  known_song_ids: string[];
  decade_preference?: string | null;
  energy_preference?: "chill" | "medium" | "high" | null;
}

// ============================================================================
// Playlist Types
// ============================================================================

export interface Playlist {
  id: string;
  name: string;
  description: string | null;
  song_ids: string[];
  song_count: number;
  created_at: string;
  updated_at: string;
}

export interface PlaylistsResponse {
  playlists: Playlist[];
  total: number;
}

export interface CreatePlaylistRequest {
  name: string;
  description?: string | null;
}

export interface UpdatePlaylistRequest {
  name?: string | null;
  description?: string | null;
}

export interface AddSongRequest {
  song_id: string;
}

// ============================================================================
// Karaoke Link Types
// ============================================================================

export type KaraokeLinkType = "youtube_search" | "karaoke_generator";

export interface KaraokeLink {
  type: KaraokeLinkType;
  url: string;
  label: string;
  description: string;
}

export interface SongLinksResponse {
  song_id: number;
  artist: string;
  title: string;
  links: KaraokeLink[];
}
