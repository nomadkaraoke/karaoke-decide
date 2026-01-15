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

// ============================================================================
// Enjoy Singing Types
// ============================================================================

export type SingingTag =
  | "easy_to_sing"
  | "crowd_pleaser"
  | "shows_range"
  | "fun_lyrics"
  | "nostalgic";

export type SingingEnergy = "upbeat_party" | "chill_ballad" | "emotional_powerhouse";

export type VocalComfort = "easy" | "comfortable" | "challenging";

export interface EnjoySingingMetadata {
  enjoy_singing: boolean;
  singing_tags: SingingTag[];
  singing_energy: SingingEnergy | null;
  vocal_comfort: VocalComfort | null;
  notes: string | null;
}

// Display labels for UI
export const SINGING_TAG_LABELS: Record<SingingTag, string> = {
  easy_to_sing: "Easy to sing",
  crowd_pleaser: "Crowd pleaser",
  shows_range: "Shows off my range",
  fun_lyrics: "Fun lyrics",
  nostalgic: "Nostalgic",
};

export const SINGING_ENERGY_LABELS: Record<SingingEnergy, { label: string; emoji: string }> = {
  upbeat_party: { label: "Upbeat Party", emoji: "🎉" },
  chill_ballad: { label: "Chill Ballad", emoji: "🌙" },
  emotional_powerhouse: { label: "Emotional Powerhouse", emoji: "💪" },
};

export const VOCAL_COMFORT_LABELS: Record<VocalComfort, { label: string; emoji: string }> = {
  easy: { label: "Easy", emoji: "😌" },
  comfortable: { label: "Comfortable", emoji: "👍" },
  challenging: { label: "Challenging", emoji: "💪" },
};

export interface UserSong {
  id: string;
  song_id: string;
  artist: string;
  title: string;
  source: "spotify" | "lastfm" | "quiz" | "known_songs" | "enjoy_singing";
  play_count: number;
  is_saved: boolean;
  times_sung: number;
  // Enjoy singing metadata
  enjoy_singing?: boolean;
  singing_tags?: SingingTag[];
  singing_energy?: SingingEnergy | null;
  vocal_comfort?: VocalComfort | null;
  notes?: string | null;
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
// Enjoy Singing Request/Response Types
// ============================================================================

export interface SetEnjoySingingRequest {
  singing_tags?: SingingTag[];
  singing_energy?: SingingEnergy | null;
  vocal_comfort?: VocalComfort | null;
  notes?: string | null;
}

export interface SetEnjoySingingResponse {
  success: boolean;
  song_id: string;
  artist: string;
  title: string;
  enjoy_singing: boolean;
  singing_tags: SingingTag[];
  singing_energy: SingingEnergy | null;
  vocal_comfort: VocalComfort | null;
  notes: string | null;
  created_new: boolean;
}

export interface EnjoySongEntry {
  song_id: string;
  singing_tags?: SingingTag[];
  singing_energy?: SingingEnergy | null;
  vocal_comfort?: VocalComfort | null;
  notes?: string | null;
}

export interface QuizEnjoySingingRequest {
  songs: EnjoySongEntry[];
}

export interface QuizEnjoySingingResponse {
  songs_added: number;
  songs_updated: number;
  songs_failed: number;
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

// ============================================================================
// Artist Types (MBID-first)
// ============================================================================

/**
 * Artist search result from catalog API.
 * MBID-first: MusicBrainz ID is the primary identifier when available.
 */
export interface ArtistSearchResult {
  // Primary identifier (MusicBrainz)
  mbid: string | null;
  name: string;

  // MusicBrainz metadata
  disambiguation: string | null;
  artist_type: string | null;
  tags: string[];

  // Spotify enrichment
  spotify_id: string | null;
  popularity: number;
  genres: string[];

  // Backward compatibility (deprecated)
  artist_id?: string; // Use mbid or spotify_id instead
  artist_name?: string; // Use name instead
}

/**
 * Compact artist entry from the index endpoint for client-side search.
 * Uses short field names to minimize payload size.
 */
export interface ArtistIndexEntry {
  m: string | null; // mbid (MusicBrainz ID, primary)
  i: string | null; // spotify_id (for images, backward compat)
  n: string; // name
  p: number; // popularity
}

/**
 * Artist response from quiz endpoints.
 */
export interface QuizArtist {
  mbid: string | null;
  name: string;
  song_count: number;
  top_songs: string[];
  total_brand_count: number;
  primary_decade: string;
  spotify_id: string | null;
  genres: string[];
  tags: string[];
  image_url: string | null;
  suggestion_reason: SuggestionReason | null;
}

export interface SuggestionReason {
  type: "fans_also_like" | "similar_artist" | "genre_match" | "decade_match" | "popular_choice";
  display_text: string;
  related_to: string | null;
}

/**
 * Artist from user's data (My Data page).
 */
export interface UserArtist {
  mbid: string | null;
  artist_name: string;
  sources: string[];
  spotify_id: string | null;
  spotify_rank: number | null;
  spotify_time_range: string | null;
  popularity: number | null;
  genres: string[];
  lastfm_rank: number | null;
  lastfm_playcount: number | null;
  tags: string[];
  is_excluded: boolean;
  is_manual: boolean;
}

/**
 * Manual artist input for quiz submission.
 */
export interface ManualArtistInput {
  mbid: string | null; // Primary identifier
  artist_id: string | null; // Spotify ID (backward compat)
  artist_name: string;
  genres: string[];
}
