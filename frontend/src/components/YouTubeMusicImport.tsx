"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { api, ApiError } from "@/lib/api";
import { YouTubeIcon } from "@/components/icons";
import { Button } from "@/components/ui";

interface ImportResult {
  playlist_title: string;
  tracks_fetched: number;
  tracks_matched: number;
}

/** Error keys by API status code (see POST /api/services/youtube-music/import). */
const ERROR_KEYS: Record<number, string> = {
  400: "errorInvalidLink",
  404: "errorNotFound",
  422: "errorLikedMusicPrivate",
};

/**
 * Lets a user paste a public/unlisted YouTube Music playlist link (e.g. a copy
 * of their Liked Music) and imports its songs as listening data.
 */
export function YouTubeMusicImport() {
  const t = useTranslations("youtubeMusicImport");
  const [playlistUrl, setPlaylistUrl] = useState("");
  const [isImporting, setIsImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);

  const handleImport = async () => {
    if (!playlistUrl.trim() || isImporting) return;
    setIsImporting(true);
    setError(null);
    try {
      const response = await api.services.importYouTubeMusicPlaylist(playlistUrl.trim());
      setResult(response);
      setPlaylistUrl("");
    } catch (err) {
      const key = err instanceof ApiError ? ERROR_KEYS[err.status] : undefined;
      setError(t(key ?? "errorGeneric"));
    } finally {
      setIsImporting(false);
    }
  };

  return (
    <div data-testid="youtube-music-import">
      <div className="flex items-center gap-2 text-sm text-[var(--text)]/70 mb-2">
        <YouTubeIcon className="w-4 h-4 text-[#ff0000] flex-shrink-0" />
        <span>{t("title")}</span>
      </div>

      {result && (
        <p data-testid="youtube-music-import-result" className="text-sm text-[var(--brand-pink)] mb-2">
          {t("importedResult", {
            fetched: result.tracks_fetched,
            matched: result.tracks_matched,
            title: result.playlist_title,
          })}
        </p>
      )}

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          handleImport();
        }}
      >
        <input
          type="url"
          inputMode="url"
          value={playlistUrl}
          onChange={(e) => setPlaylistUrl(e.target.value)}
          placeholder={t("placeholder")}
          aria-label={t("placeholder")}
          className="flex-1 min-w-0 px-3 py-2 rounded-lg text-sm bg-[var(--bg)] border border-[var(--card-border)] text-[var(--text)] placeholder-[var(--text-subtle)] focus:outline-none focus:ring-2 focus:ring-[var(--brand-pink)]/50"
        />
        <Button
          type="submit"
          variant="secondary"
          size="sm"
          isLoading={isImporting}
          disabled={!playlistUrl.trim() || isImporting}
        >
          {isImporting ? t("importing") : t("import")}
        </Button>
      </form>

      {error && (
        <p data-testid="youtube-music-import-error" className="text-sm text-red-400 mt-2">
          {error}
        </p>
      )}

      <details className="mt-2 text-xs text-[var(--text)]/50">
        <summary className="cursor-pointer hover:text-[var(--text)]/70">{t("howToTitle")}</summary>
        <ol className="list-decimal ml-4 mt-2 space-y-1">
          <li>{t("howToStep1")}</li>
          <li>{t("howToStep2")}</li>
          <li>{t("howToStep3")}</li>
          <li>{t("howToStep4")}</li>
        </ol>
        <p className="mt-2">{t("howToAnyPlaylist")}</p>
      </details>
    </div>
  );
}
