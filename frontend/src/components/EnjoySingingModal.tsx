"use client";

import { useState, useCallback } from "react";
import { Button, Input } from "@/components/ui";
import { XIcon, CheckIcon } from "@/components/icons";
import { api } from "@/lib/api";
import type {
  SingingTag,
  SingingEnergy,
  VocalComfort,
  SINGING_TAG_LABELS,
  SINGING_ENERGY_LABELS,
  VOCAL_COMFORT_LABELS,
} from "@/types";

// Re-define the labels here to avoid import issues with objects
const TAG_LABELS: Record<SingingTag, string> = {
  easy_to_sing: "Easy to sing",
  crowd_pleaser: "Crowd pleaser",
  shows_range: "Shows off my range",
  fun_lyrics: "Fun lyrics",
  nostalgic: "Nostalgic",
};

const ENERGY_LABELS: Record<SingingEnergy, { label: string; emoji: string }> = {
  upbeat_party: { label: "Upbeat Party", emoji: "🎉" },
  chill_ballad: { label: "Chill Ballad", emoji: "🌙" },
  emotional_powerhouse: { label: "Emotional Powerhouse", emoji: "💪" },
};

const COMFORT_LABELS: Record<VocalComfort, { label: string; emoji: string }> = {
  easy: { label: "Easy", emoji: "😌" },
  comfortable: { label: "Comfortable", emoji: "👍" },
  challenging: { label: "Challenging", emoji: "💪" },
};

export interface EnjoySingingMetadataResult {
  singing_tags: SingingTag[];
  singing_energy: SingingEnergy | null;
  vocal_comfort: VocalComfort | null;
  notes: string | null;
}

interface EnjoySingingModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
  /** If provided, skips API call and returns metadata via this callback instead */
  onLocalSave?: (metadata: EnjoySingingMetadataResult) => void;
  song: {
    song_id: string;
    artist: string;
    title: string;
    // Optional pre-existing metadata
    enjoy_singing?: boolean;
    singing_tags?: SingingTag[];
    singing_energy?: SingingEnergy | null;
    vocal_comfort?: VocalComfort | null;
    notes?: string | null;
  };
}

export function EnjoySingingModal({
  isOpen,
  onClose,
  onSuccess,
  onLocalSave,
  song,
}: EnjoySingingModalProps) {
  const [selectedTags, setSelectedTags] = useState<SingingTag[]>(
    song.singing_tags || []
  );
  const [selectedEnergy, setSelectedEnergy] = useState<SingingEnergy | null>(
    song.singing_energy || null
  );
  const [selectedComfort, setSelectedComfort] = useState<VocalComfort | null>(
    song.vocal_comfort || null
  );
  const [notes, setNotes] = useState(song.notes || "");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggleTag = useCallback((tag: SingingTag) => {
    setSelectedTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]
    );
  }, []);

  const handleSave = async () => {
    const metadata: EnjoySingingMetadataResult = {
      singing_tags: selectedTags,
      singing_energy: selectedEnergy,
      vocal_comfort: selectedComfort,
      notes: notes.trim() || null,
    };

    // Local save mode - just return data via callback, skip API
    if (onLocalSave) {
      onLocalSave(metadata);
      onSuccess?.();
      onClose();
      return;
    }

    // API save mode
    try {
      setIsSaving(true);
      setError(null);

      await api.knownSongs.setEnjoySinging(song.song_id, metadata);

      onSuccess?.();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setIsSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50">
      <div className="bg-[var(--card)] rounded-2xl p-6 w-full max-w-md border border-[var(--card-border)] max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-xl font-bold text-[var(--text)]">
              I Enjoy Singing This
            </h2>
            <p className="text-sm text-[var(--text-muted)] mt-1">
              {song.artist} - {song.title}
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-[var(--text-muted)] hover:text-[var(--text)] transition-colors p-1"
          >
            <XIcon className="w-5 h-5" />
          </button>
        </div>

        {/* Error message */}
        {error && (
          <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 mb-4">
            <p className="text-sm text-red-400">{error}</p>
          </div>
        )}

        {/* Tags Section */}
        <div className="mb-5">
          <label className="block text-sm font-medium text-[var(--text-muted)] mb-2">
            Why do you enjoy singing this? (optional)
          </label>
          <div className="flex flex-wrap gap-2">
            {(Object.keys(TAG_LABELS) as SingingTag[]).map((tag) => (
              <button
                key={tag}
                onClick={() => toggleTag(tag)}
                className={`px-3 py-1.5 rounded-full text-sm font-medium transition-all ${
                  selectedTags.includes(tag)
                    ? "bg-[var(--brand-pink)]/20 text-[var(--brand-pink)] border border-[var(--brand-pink)]/50"
                    : "bg-[var(--secondary)] text-[var(--text-muted)] border border-[var(--card-border)] hover:border-[var(--text-subtle)]"
                }`}
              >
                {selectedTags.includes(tag) && (
                  <CheckIcon className="w-3 h-3 inline mr-1" />
                )}
                {TAG_LABELS[tag]}
              </button>
            ))}
          </div>
        </div>

        {/* Energy Section */}
        <div className="mb-5">
          <label className="block text-sm font-medium text-[var(--text-muted)] mb-2">
            What kind of song is this for you? (optional)
          </label>
          <div className="grid grid-cols-3 gap-2">
            {(Object.keys(ENERGY_LABELS) as SingingEnergy[]).map((energy) => (
              <button
                key={energy}
                onClick={() =>
                  setSelectedEnergy(selectedEnergy === energy ? null : energy)
                }
                className={`py-3 px-2 rounded-xl text-center transition-all ${
                  selectedEnergy === energy
                    ? "bg-[var(--brand-pink)]/20 border-[var(--brand-pink)]/50 border-2"
                    : "bg-[var(--secondary)] border border-[var(--card-border)] hover:border-[var(--text-subtle)]"
                }`}
              >
                <span className="text-xl block mb-1">
                  {ENERGY_LABELS[energy].emoji}
                </span>
                <span className="text-xs font-medium text-[var(--text)]">
                  {ENERGY_LABELS[energy].label}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Vocal Comfort Section */}
        <div className="mb-5">
          <label className="block text-sm font-medium text-[var(--text-muted)] mb-2">
            How comfortable is this in your vocal range? (optional)
          </label>
          <div className="grid grid-cols-3 gap-2">
            {(Object.keys(COMFORT_LABELS) as VocalComfort[]).map((comfort) => (
              <button
                key={comfort}
                onClick={() =>
                  setSelectedComfort(
                    selectedComfort === comfort ? null : comfort
                  )
                }
                className={`py-3 px-2 rounded-xl text-center transition-all ${
                  selectedComfort === comfort
                    ? "bg-[var(--brand-pink)]/20 border-[var(--brand-pink)]/50 border-2"
                    : "bg-[var(--secondary)] border border-[var(--card-border)] hover:border-[var(--text-subtle)]"
                }`}
              >
                <span className="text-xl block mb-1">
                  {COMFORT_LABELS[comfort].emoji}
                </span>
                <span className="text-xs font-medium text-[var(--text)]">
                  {COMFORT_LABELS[comfort].label}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Notes Section */}
        <div className="mb-6">
          <label className="block text-sm font-medium text-[var(--text-muted)] mb-2">
            Personal notes (optional)
          </label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Any notes about why you love singing this song..."
            maxLength={500}
            rows={3}
            className="w-full px-4 py-3 bg-[var(--secondary)] border border-[var(--card-border)] rounded-xl text-[var(--text)] placeholder:text-[var(--text-subtle)] focus:outline-none focus:ring-2 focus:ring-[var(--brand-pink)] focus:border-transparent resize-none"
          />
          <p className="text-xs text-[var(--text-subtle)] mt-1 text-right">
            {notes.length}/500
          </p>
        </div>

        {/* Action Buttons */}
        <div className="flex gap-3">
          <Button variant="secondary" className="flex-1" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            className="flex-1"
            onClick={handleSave}
            isLoading={isSaving}
          >
            Save
          </Button>
        </div>
      </div>
    </div>
  );
}
